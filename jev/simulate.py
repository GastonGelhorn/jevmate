"""What the same work would have cost with Claude Code compacting at a smaller context.

Each main-thread request in the transcripts is replayed in order. The text it added to the context
(its growth since the request before) is kept; the context is rebuilt from that growth, and when it
passes the window being tried it is compacted: it restarts at the size measured after real
compactions, and the compaction is charged (the summarizer reads the context from the cache and
writes the summary, and the next request writes the new context to the cache). The rest of each
request is priced as it really went: what it wrote to the cache is written again, and a request that
found the cache expired rewrites the whole (smaller) context. Output tokens are the same in every
window and are left out. What this leaves out: whatever the agent re-reads after a compaction to get
back details the summary dropped.
"""

from __future__ import annotations

import json
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .metrics import price_for
from .report import PROJECTS, _utc, transcripts

WINDOWS = (967_000, 600_000, 400_000, 300_000, 200_000)
DEFAULT_POST = 60_000      # context right after a compaction, when no real one was seen
DEFAULT_SUMMARY = 30_000   # tokens the summarizer writes (Claude Code records ~26-31K after a compaction)


def requests(path: Path, start: str, end: str) -> tuple[list[dict], list[dict]]:
    """A transcript's main-thread requests between `start` and `end` (UTC), once each, in order, and its compactions."""
    reqs: dict[str, dict] = {}
    comps: list[dict] = []
    try:
        f = open(path, "rb")
    except OSError:
        return [], []
    with f:
        for line in f:
            if b'"compact_boundary"' in line:
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                ts = (o.get("timestamp") or "")[:19]
                if o.get("subtype") == "compact_boundary" and start <= ts < end:
                    m = o.get("compactMetadata") or {}
                    comps.append({"ts": ts, "pre": int(m.get("preTokens") or 0), "post": int(m.get("postTokens") or 0)})
                continue
            if b'"assistant"' not in line:
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if o.get("isSidechain"):
                continue
            msg = o.get("message") or {}
            u = msg.get("usage") or {}
            ts = (o.get("timestamp") or "")[:19]
            model = msg.get("model") or ""
            if not u or model.startswith("<") or not (start <= ts < end):
                continue
            mid = msg.get("id") or o.get("uuid")
            if mid in reqs:
                continue
            new = (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)
            reqs[mid] = {"ts": ts, "model": model, "ctx": new + (u.get("cache_read_input_tokens") or 0), "new": new}
    return sorted(reqs.values(), key=lambda r: r["ts"]), comps


def measured(sessions: list[tuple[list[dict], list[dict]]]) -> tuple[int, int]:
    """The context right after a real compaction (the first request after it), and the size of what it kept."""
    posts, kept = [], []
    for reqs, comps in sessions:
        for c in comps:
            nxt = next((r for r in reqs if r["ts"] > c["ts"]), None)
            if nxt and nxt["ctx"] < c["pre"] * 0.5:
                posts.append(nxt["ctx"])
            if c["post"]:
                kept.append(c["post"])
    return (int(statistics.median(posts)) if posts else DEFAULT_POST, int(statistics.median(kept)) if kept else DEFAULT_SUMMARY)


def replay(reqs: list[dict], window: int, post: int, summary: int) -> dict:
    cost = reads = 0.0
    compactions = 0
    ctx = prev = 0
    fresh = False  # the request after a compaction writes the new context
    for r in reqs:
        grew = r["ctx"] - prev if prev and r["ctx"] >= prev * 0.5 else (r["ctx"] if not prev else max(0, r["ctx"] - post))
        prev = r["ctx"]
        _, p_out, p_cr, p_cw = price_for(r["model"])
        if ctx and ctx + grew >= window:
            cost += ctx / 1e6 * p_cr + summary / 1e6 * p_out  # the summarizer reads it all and writes the summary
            compactions += 1
            ctx, fresh = post, True
        else:
            ctx += grew
        if fresh or r["new"] > 0.5 * r["ctx"]:
            write, read = ctx, 0  # nothing of it in the cache: after a compaction, or the cache had expired
        else:
            write = min(r["new"], ctx)
            read = ctx - write
        fresh = False
        cost += read / 1e6 * p_cr + write / 1e6 * p_cw
        reads += read
    return {"usd": cost, "cache_read": reads, "compactions": compactions}


def simulate(days: float = 7, windows: tuple[int, ...] = WINDOWS, now: datetime | None = None, root: Path = PROJECTS) -> dict:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    main, _ = transcripts(since, root)
    sessions = [requests(p, _utc(since), _utc(now)) for p in main]
    sessions = [s for s in sessions if s[0]]
    post, summary = measured(sessions)
    rows = []
    for w in windows:
        runs = [replay(reqs, w, post, summary) for reqs, _ in sessions]
        rows.append({"window": w, "usd": round(sum(x["usd"] for x in runs), 2), "cache_read": int(sum(x["cache_read"] for x in runs)),
                     "compactions": sum(x["compactions"] for x in runs)})
    base = next((r for r in rows if r["window"] == max(windows)), rows[0]) if rows else None
    for r in rows:
        r["vs_largest"] = round(r["usd"] - base["usd"], 2) if base else 0
    return {"days": days, "sessions": len(sessions), "requests": sum(len(s[0]) for s in sessions),
            "real_compactions": sum(len(s[1]) for s in sessions), "post": post, "summary": summary, "windows": rows}


def render(s: dict) -> str:
    lines = [f"jev compact --simulate · {s['days']:g} days · {s['sessions']} sessions · {s['requests']:,} main-thread requests · "
             f"{s['real_compactions']} real compactions",
             f"  after a compaction the context restarts at ~{s['post'] // 1000}K; each compaction writes a ~{s['summary'] // 1000}K summary",
             "  window      context cost   compactions   vs the largest"]
    for r in s["windows"]:
        lines.append(f"  {r['window'] // 1000:>5}K     ${r['usd']:>10,.2f}   {r['compactions']:>11}   {r['vs_largest']:>+10,.2f}")
    lines.append("  context cost: cache reads and writes of the main thread, plus each compaction; output tokens are the same in every window")
    return "\n".join(lines)

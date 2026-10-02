"""What a Claude Code session spent, next to what jev decided instead.

The model side is read from the transcript Claude Code writes (`~/.claude/projects/<cwd>/
<session>.jsonl`): every assistant turn carries `usage` (input, output, cache read, cache write),
priced at list price per model. One API message spans several lines, one per content block, with
the same id and usage; each id counts once. The transcript is read incrementally, so a watcher
parses the file once and then only what each turn appends. It also records each turn's model and
context size, and where the conversation was compacted.

The jev side is the ledger: rows tagged with this session (the plugin's SessionStart hook puts the
tag in every Bash command's environment; the hooks tag their own rows), or, without a tag, rows
from this directory inside the session's time window. Only rows that stood in for the agent's own
reading count as text kept out of the context, plus the output the trim hook dropped. The other
hooks (guard, screen, inspect, routing, honesty, triage) are safety checks: they go through jev
but the agent would not have read that text anyway.

What reading that text would have cost: each piece once as input on the first turn after it, at
that turn's model, then again as a cache read on every later turn, until the conversation was
compacted or the text would no longer have fit in the window. On a subscription the dollars are
an API equivalent; `plan_view` turns them into a share of the 5-hour and weekly windows.
"""

from __future__ import annotations

import json
import os
import time
from bisect import insort
from datetime import datetime, timezone
from pathlib import Path

from . import ledger, settings
from .render import BOLD, DIM, GREEN, RESET, YELLOW, fmt_k

MODEL_PRICES = {  # USD per million tokens: input, output, cache read, cache write (5 minutes); list prices checked 2026-09-25
    "claude-fable-5-1": (10.0, 50.0, 0.25, 12.5),
    "claude-mythos-5-1": (10.0, 50.0, 0.25, 12.5),
    "claude-fable-5": (10.0, 50.0, 1.0, 12.5),
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.0),
    "claude-opus-5": (5.0, 25.0, 0.5, 6.25),
    "claude-opus-4": (5.0, 25.0, 0.5, 6.25),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.5),
    "claude-sonnet-5": (2.0, 10.0, 0.2, 2.5),
    "claude-sonnet-4": (3.0, 15.0, 0.3, 3.75),
    "claude-haiku-4": (1.0, 5.0, 0.1, 1.25),
}
CONTEXT_SIZE = {"claude-fable": 1_000_000, "claude-mythos": 1_000_000, "claude-opus-5": 1_000_000, "claude-opus-4-6": 1_000_000,
                "claude-opus-4-7": 1_000_000, "claude-opus-4-8": 1_000_000, "claude-sonnet-5": 1_000_000, "claude-sonnet-4-6": 1_000_000}
DEFAULT_PRICE = (5.0, 25.0, 0.5, 6.25)
COMPACT_AT = 0.9  # Claude Code compacts before the window is full; a lower bar means a smaller, safer estimate

# Ledger labels that are not the agent's reading: maintenance commands and every hook.
OVERHEAD_LABELS = {"inspect", "tune", "label", "doctor", "models", "cost"}

PLAN_WINDOWS = ("five_hour", "seven_day")
PLAN_DECAY = 0.995      # per interval: the estimate follows the plan if its limits change
PLAN_READY_USD = 2.0    # API-equivalent dollars observed before a window's rate is shown
PLAN_KEEP_DAYS = 14
MONEY_FLOOR = 0.50      # below this the band leads with what jev caught, not with cents


def price_for(model: str) -> tuple[float, float, float, float]:
    table = {**MODEL_PRICES, **{k: tuple(v) for k, v in (settings.config().get("model_prices") or {}).items()}}
    best = max((k for k in table if model.startswith(k)), key=len, default=None)
    return table[best] if best else DEFAULT_PRICE


def context_size(model: str) -> int:
    best = max((k for k in CONTEXT_SIZE if model.startswith(k)), key=len, default=None)
    return CONTEXT_SIZE[best] if best else 200_000


def reads_for_agent(label) -> bool:
    """Did this ledger row stand in for text the agent would otherwise have read?"""
    name = str(label or "")
    return not (name.startswith("hook:") or name in OVERHEAD_LABELS)


def find_transcript(cwd: str, explicit: str | None = None, session: str | None = None) -> Path | None:
    if explicit:
        return Path(explicit).expanduser()
    if session:
        marker = ledger.session_marker(session) or {}
        if marker.get("transcript") and Path(marker["transcript"]).exists():
            return Path(marker["transcript"])
    proj = Path.home() / ".claude" / "projects" / cwd.replace("/", "-")
    try:
        files = [f for f in proj.glob("*.jsonl") if not session or f.stem.startswith(session)]
    except OSError:
        return None
    return max(files, key=lambda f: f.stat().st_mtime) if files else None


class Transcript:
    def __init__(self, path: Path):
        self.path = path
        self.reset()

    def reset(self) -> None:
        self.offset = 0
        self.partial = b""
        self.per: dict[str, dict] = {}
        self.seen: set[str] = set()
        self.turns: list[tuple[str, str, int]] = []  # (UTC "YYYY-MM-DDTHH:MM:SS", model, context tokens), main thread, sorted
        self.compactions: list[str] = []             # UTC timestamps of compaction boundaries, sorted
        self.first = self.last = None
        self.last_ctx = 0
        self.last_model = ""

    @property
    def session(self) -> str:
        return self.path.stem

    @property
    def turn_ts(self) -> list[str]:
        return [t for t, _, _ in self.turns]

    def refresh(self) -> None:
        try:
            size = self.path.stat().st_size
            if size < self.offset:
                self.reset()
            with open(self.path, "rb") as f:
                f.seek(self.offset)
                data = f.read()
                self.offset = f.tell()
        except OSError:
            return
        lines = (self.partial + data).split(b"\n")
        self.partial = lines.pop()
        for line in lines:
            if b'"compact_boundary"' in line:
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if o.get("subtype") == "compact_boundary" and o.get("timestamp"):
                    insort(self.compactions, o["timestamp"][:19])
                continue
            if b'"type":"assistant"' not in line and b'"type": "assistant"' not in line:
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
            m = o.get("message") or {}
            u = m.get("usage") or {}
            if not u:
                continue
            mid = m.get("id") or o.get("uuid")
            if mid in self.seen:
                continue
            self.seen.add(mid)
            ts = (o.get("timestamp") or "")[:19]
            model = m.get("model") or "?"
            ctx = (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)
            if ts:
                self.first = ts if self.first is None or ts < self.first else self.first
                self.last = ts if self.last is None or ts > self.last else self.last
                if not o.get("isSidechain") and not model.startswith("<"):
                    insort(self.turns, (ts, model, ctx))
            d = self.per.setdefault(model, {"turns": 0, "in": 0, "out": 0, "cr": 0, "cw": 0})
            d["turns"] += 1
            d["in"] += u.get("input_tokens") or 0
            d["out"] += u.get("output_tokens") or 0
            d["cr"] += u.get("cache_read_input_tokens") or 0
            d["cw"] += u.get("cache_creation_input_tokens") or 0
            if not o.get("isSidechain") and not model.startswith("<"):
                self.last_ctx = ctx
                self.last_model = model

    def usd(self) -> float:
        total = 0.0
        for model, d in self.per.items():
            pi, po, pcr, pcw = price_for(model)
            d["usd"] = d["in"] / 1e6 * pi + d["out"] / 1e6 * po + d["cr"] / 1e6 * pcr + d["cw"] / 1e6 * pcw
            total += d["usd"]
        return total


def to_local(ts_utc: str) -> datetime:
    return datetime.fromisoformat(ts_utc.replace("Z", "+00:00")).replace(tzinfo=timezone.utc).astimezone()


def to_utc(ts_local: str) -> str | None:
    """A ledger timestamp (local, no zone) as the transcript writes its own: UTC, to the second."""
    try:
        return datetime.fromisoformat(ts_local).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    except (TypeError, ValueError):
        return None


def span(a: str | None, z: str | None) -> str:
    if not a or not z:
        return "?"
    try:
        la, lz = to_local(a), to_local(z)
    except ValueError:
        return f"{a[11:16]}→{z[11:16]} UTC"
    return f"{la:%H:%M}→{lz:%H:%M}" if la.date() == lz.date() else f"{la:%b %d %H:%M}→{lz:%b %d %H:%M}"


def would_have_cost(events: list[tuple[str | None, int]], turns: list[tuple[str, str, int]], compactions: list[str],
                    flat_price: float | None = None) -> tuple[float, float]:
    """What reading `events` ([(UTC ts, tokens)]) would have cost the agent: each piece once as input on the
    first main-thread turn after it, priced at that turn's model, then again as a cache read on every later
    turn, until a compaction emptied the context or the text would no longer have fit beside what was really
    there. Returns (once, re-read) in USD. `flat_price` replaces the per-model input price when the person set one."""
    marks = sorted([(ts, 0, 0) for ts in compactions] + [(ts, 1, n) for ts, n in events if ts and n > 0])
    once = reread = 0.0
    pending = resident = 0
    i = 0
    for ts, model, ctx in turns:
        while i < len(marks) and marks[i][0] < ts:
            _, kind, n = marks[i]
            if kind == 0:
                resident = 0  # compacted: whatever was read before is gone
            else:
                pending += n
            i += 1
        p_in, _, p_cr, _ = price_for(model)
        if resident and ctx + resident > context_size(model) * COMPACT_AT:
            resident = 0  # it would not have fit: a compaction would have dropped it
        reread += resident / 1e6 * p_cr
        once += pending / 1e6 * (flat_price if flat_price is not None else p_in)
        resident += pending
        pending = 0
    pending += sum(n for _, kind, n in marks[i:] if kind == 1)
    if pending:  # read by a turn that has not happened yet
        price = flat_price if flat_price is not None else (price_for(turns[-1][1])[0] if turns else settings.agent_price())
        once += pending / 1e6 * price
    return once, reread


def _top(counts: dict[str, int]) -> dict[str, int]:
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def jev_side(cwd: str, session_id: str | None, since: str, transcript: Transcript | None = None) -> dict:
    rows = ledger.session_rows(cwd, session_id, since)
    tag = ledger.session_tag(session_id)
    hooks = ledger.hook_rows()
    asked = ledger.asked_count(hooks, since, tag)
    # command output the trim hook kept out of the context, by session tag (or the time window without one)
    trims = [h for h in hooks if h.get("hook") == "trim" and h["ts"] >= since and (not tag or h.get("agent") in (None, "", tag))]
    trimmed = sum(int(h.get("dropped_tokens") or 0) for h in trims)
    size = lambda r: r.get("in", 0) + (r.get("cached_in") or 0)  # noqa: E731
    reads = [r for r in rows if reads_for_agent(r.get("cmd"))]
    tokens = sum(size(r) for r in rows)
    read_tokens = sum(size(r) for r in reads)
    paid = settings.cost_usd(sum(r.get("in", 0) for r in rows))
    paid_reads = settings.cost_usd(sum(r.get("in", 0) for r in reads))
    kept_out = read_tokens + trimmed
    explicit = settings.config().get("agent_price_per_mtok_in")
    flat = float(explicit) if explicit else None
    if transcript is not None and transcript.turns:
        events = [(to_utc(r["ts"]), size(r)) for r in reads] + [(to_utc(h["ts"]), int(h.get("dropped_tokens") or 0)) for h in trims]
        once, reread = would_have_cost(events, transcript.turns, transcript.compactions, flat)
        pricing = "flat" if flat is not None else "per-model"
    else:
        once, reread = kept_out / 1e6 * (flat if flat is not None else settings.agent_price()), 0.0
        pricing = "flat"
    labels: dict[str, int] = {}
    hook_labels: dict[str, int] = {}
    for r in rows:
        name = str(r.get("cmd") or "?")
        if reads_for_agent(name):
            labels[name] = labels.get(name, 0) + size(r)
        else:  # `inspect` by hand and the inspect hook are one check
            short = name.removeprefix("hook:")
            hook_labels[short] = hook_labels.get(short, 0) + size(r)
    would = once + reread
    mine = [h for h in hooks if h["ts"] >= since and (not tag or h.get("agent") in (None, "", tag))]
    kinds = lambda name: [h for h in mine if h.get("hook") == name]  # noqa: E731
    safety = {"asked": asked,
              "pages_flagged": sum(1 for h in kinds("screen") if h.get("warned")),
              "files_flagged": sum(int(h.get("new_flags") or 0) for h in kinds("inspect")),
              "triaged": sum(1 for h in kinds("triage") if h.get("noted")),
              "claims": sum(1 for h in kinds("honesty") if h.get("blocked")) + len(kinds("evidence")),
              "checks": sum(1 for h in mine if h.get("hook") in ("guard", "screen", "inspect", "honesty") and "err" not in h)}
    subagents = kinds("subagent")
    verdicts = [h for h in hooks if h.get("hook") == "effort-cache" and h.get("verdict")]
    routing = {"subagents": len(subagents), "subagent_saved": sum(float(h.get("saved_usd") or 0) for h in subagents),
               "subagent_spent": sum(float(h.get("spent") or 0) for h in subagents), "effort_turns": len(kinds("effort")),
               "effort_cache": verdicts[-1]["verdict"] if verdicts else None}
    saved = would - paid_reads
    return {"requests": len(rows), "decisions": sum(r.get("q", 0) for r in rows), "tokens": tokens, "cached": sum(1 for r in rows if r.get("cached")),
            "reads": read_tokens, "overhead": tokens - read_tokens, "trimmed": trimmed, "trim_runs": len(trims), "kept_out": kept_out,
            "paid": paid, "paid_reads": paid_reads, "once": once, "reread": reread, "would": would, "saved": saved,
            "saved_total": saved + routing["subagent_saved"], "asked": asked, "safety": safety, "routing": routing,
            "pricing": pricing, "agent_price": flat if flat is not None else settings.agent_price(), "compactions": len(transcript.compactions) if transcript else 0,
            "labels": _top(labels), "hook_labels": _top(hook_labels)}


# ---------------------------------------------------------------- subscription plans

def _plan_path() -> Path:
    return settings.HOME / "plan.json"


def _plan_load() -> dict:
    try:
        data = json.loads(_plan_path().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def parse_plan(values: list[str] | None) -> dict[str, dict]:
    """`five_hour=23.5@2026-10-02T14:00:00Z` (the reset time optional), as the plugin's mod passes them."""
    out: dict[str, dict] = {}
    for v in values or []:
        kind, _, rest = str(v).partition("=")
        pct, _, resets = rest.partition("@")
        try:
            out[kind.strip()] = {"pct": float(pct), "resets": resets.strip() or None}
        except ValueError:
            continue
    return out


def plan_record(session_id: str, spent: float | None, readings: dict[str, dict], now: float | None = None) -> None:
    """Learn how many points of each window one API-equivalent dollar uses, from consecutive readings of one
    session: the points the window moved over the dollars the session spent meanwhile. An interval in which
    the window reset, the session's figure went back, or another session was active, teaches nothing."""
    if not session_id or not readings:
        return
    now = time.time() if now is None else now
    data = _plan_load()
    sessions = {k: v for k, v in (data.get("sessions") or {}).items() if isinstance(v, dict) and now - float(v.get("ts", 0)) < PLAN_KEEP_DAYS * 86400}
    rates = data.get("rates") or {}
    prev = sessions.get(session_id)
    if prev and spent is not None and prev.get("spent") is not None:
        dc = spent - float(prev["spent"])
        busy = any(k != session_id and float(v.get("ts", 0)) > float(prev.get("ts", 0)) for k, v in sessions.items())
        if dc > 0 and not busy:
            for kind in PLAN_WINDOWS:
                cur, old = readings.get(kind), (prev.get("readings") or {}).get(kind)
                if not cur or not old or cur.get("resets") != old.get("resets") or cur["pct"] < old["pct"]:
                    continue
                acc = rates.get(kind) or {"p": 0.0, "c": 0.0, "n": 0}
                rates[kind] = {"p": acc["p"] * PLAN_DECAY + (cur["pct"] - old["pct"]), "c": acc["c"] * PLAN_DECAY + dc, "n": acc["n"] + 1}
    sessions[session_id] = {"ts": now, "spent": spent, "readings": readings}
    try:
        settings.HOME.mkdir(parents=True, exist_ok=True)
        _plan_path().write_text(json.dumps({"rates": rates, "sessions": sessions}))
    except OSError:
        pass


def plan_view(session_id: str | None, would: float, readings: dict[str, dict] | None = None) -> dict | None:
    """The session on a subscription: each window's use now and the share of it the kept-out text would
    have taken (points, or None while the rate is still being learnt). None off a subscription."""
    data = _plan_load()
    if readings is None and session_id:
        readings = ((data.get("sessions") or {}).get(session_id) or {}).get("readings")
    readings = {k: v for k, v in (readings or {}).items() if k in PLAN_WINDOWS}
    if not readings:
        return None
    windows = {}
    for kind, cur in readings.items():
        acc = (data.get("rates") or {}).get(kind) or {}
        ready = float(acc.get("c") or 0) >= PLAN_READY_USD and int(acc.get("n") or 0) >= 3
        rate = float(acc["p"]) / float(acc["c"]) if ready else None
        windows[kind] = {"used": cur["pct"], "resets_at": cur.get("resets"), "rate": rate, "kept_free": would * rate if rate is not None else None}
    return {"billing": "subscription", "windows": windows}


# ---------------------------------------------------------------- the summary

def session_summary(cwd: str, session_id: str | None = None, transcript: Transcript | Path | None = None,
                    spent: float | None = None, plan: dict[str, dict] | None = None) -> dict:
    """Everything `jev session`, `jev watch`, the plugin's mod and the MCP `session` tool show. `spent` and
    `plan` are Claude Code's own figures, passed by the mod: the session's cost and the plan windows."""
    cwd = os.path.realpath(cwd)
    tr: Transcript | None
    if isinstance(transcript, Transcript):
        tr = transcript
    else:
        path = transcript if isinstance(transcript, Path) else find_transcript(cwd, None, session_id)
        tr = Transcript(path) if path and path.exists() else None
    if tr is not None:
        tr.refresh()
    sid = session_id or (tr.session if tr else None)
    model = None
    if tr and tr.per:
        total = tr.usd()
        model = {"per": {k: dict(v) for k, v in tr.per.items()}, "usd": total, "turns": sum(d["turns"] for d in tr.per.values()),
                 "first": tr.first, "last": tr.last, "ctx": tr.last_ctx, "ctx_size": context_size(tr.last_model), "last_model": tr.last_model,
                 "cache_read": sum(d["cr"] for d in tr.per.values())}
    if tr and tr.first:
        try:
            since = to_local(tr.first).replace(tzinfo=None).isoformat(timespec="seconds")
        except ValueError:
            since = tr.first
    else:
        marker = ledger.session_marker(sid) if sid else None
        start = marker["first_seen"] - 5 if marker and marker.get("first_seen") else None
        since = datetime.fromtimestamp(start).isoformat(timespec="seconds") if start else datetime.now().replace(hour=0, minute=0, second=0).isoformat(timespec="seconds")
    jev = jev_side(cwd, sid, since, tr)
    session_usd = spent if spent is not None and spent > 0 else (model["usd"] if model else None)
    if session_usd:
        jev["share"] = jev["saved_total"] / session_usd
    if plan and sid:
        plan_record(sid, spent if spent is not None else (model["usd"] if model else None), plan)
    view = plan_view(sid, jev["would"] + jev["routing"]["subagent_saved"], plan) if sid else None
    return {"session": sid, "cwd": cwd, "transcript": str(tr.path) if tr else None, "since": since, "model": model, "jev": jev,
            "spent": spent, "plan": view}


def _window_name(kind: str) -> str:
    return {"five_hour": "5-hour window", "seven_day": "week"}.get(kind, kind)


def worth(points: float | None, name: str) -> str:
    """Points of a plan window, as a reader says them."""
    if points is None:
        return f"the {name}: measuring"
    if points >= 100:
        return f"≈ {points / 100:.1f}× the {name}"
    return f"< 0.1% of the {name}" if points < 0.1 else f"≈ {points:.1f}% of the {name}"


def plan_line(view: dict) -> str:
    return " · ".join(worth(w["kept_free"], _window_name(kind)) for kind in PLAN_WINDOWS if (w := view["windows"].get(kind)))


def render_session(s: dict, color: bool = True, jev_only: bool = False, title: str = "jev session") -> str:
    b, dim, g, y, r0 = (BOLD, DIM, GREEN, YELLOW, RESET) if color else ("",) * 5
    m, j, view = s["model"], s["jev"], s.get("plan")
    sid = s["session"]
    lines = [f"{b}{title}{r0} · {Path(s['cwd']).name}" + (f" · session {sid[:8]}" if sid else "")
             + (f" · {span(m['first'], m['last'])}" if m else "") + (f"{dim} · no transcript found{r0}" if not m else "")
             + f"{dim} · {datetime.now():%H:%M:%S}{r0}"]
    if m and not jev_only:
        lines.append(f"\n{b}model side{r0}{dim}  estimated at list price from the transcript"
                     + ("; on a subscription this is an API equivalent" if view else "; Claude Code's own figure may differ") + f"{r0}")
        for model, d in sorted(m["per"].items(), key=lambda kv: -kv[1]["usd"]):
            if not d["turns"] or model.startswith("<"):
                continue
            lines.append(f"  {model.replace('claude-', ''):<14}{d['turns']:>5} turns   in {fmt_k(d['in']):>6} · out {fmt_k(d['out']):>6} · cache read {fmt_k(d['cr']):>7}"
                         f" · cache write {fmt_k(d['cw']):>6}   {y}~${d['usd']:,.2f}{r0}")
        lines.append(f"  {'session':<14}{m['turns']:>5} turns   {y}~${m['usd']:,.2f}{r0} · context now {fmt_k(m['ctx'])} tokens ({100 * m['ctx'] / m['ctx_size']:.0f}% of {fmt_k(m['ctx_size'])})")
    lines.append(f"\n{b}jev side{r0}{dim}  this session{r0}")
    if not j["requests"] and not j["asked"] and not j.get("trimmed"):
        lines.append(f"  {dim}nothing decided yet this session · `jev sift`, `jev tests`, `jev cluster` … will show up here{r0}")
        return "\n".join(lines)
    lines.append(f"  {b}went through jev{r0}   {g}{j['decisions']:,} decisions{r0} over {j['tokens']:,} tokens · {j['requests']} request(s)"
                 + (f" ({j['cached']} from cache)" if j["cached"] else ""))
    lines.append(f"  {b}kept out{r0}           {g}{j['kept_out']:,} tokens{r0} the agent did not read: {j['reads']:,} judged by jev instead"
                 + (f", {j['trimmed']:,} trimmed from command output in {j['trim_runs']} run(s)" if j.get("trimmed") else ""))
    priced = (f"at each turn's model, once as input and then as cache reads until the next compaction" if j["pricing"] == "per-model"
              else f"at ${j['agent_price']:g}/M, once")
    eq = " API-equivalent" if view else ""
    lines.append(f"  {b}would have cost{r0}    {y}~${j['would']:.2f}{r0}{eq} to read it yourself: ${j['once']:.2f} once + ${j['reread']:.2f} re-read · {dim}{priced}{r0}")
    share = f" · ≈ {100 * j['share']:.1f}% of this session" if "share" in j else ""
    lines.append(f"  {b}saved{r0}              {g}~${j['saved']:.2f}{eq} at most{r0} on reading{share if not j['routing']['subagents'] else ''}"
                 f"{dim} — minus whatever the agent read anyway from the uncertain band{r0}")
    ro, sf = j["routing"], j["safety"]
    if ro["subagents"]:
        lines.append(f"  {b}subagents{r0}          {g}{ro['subagents']} ran on a cheaper model{r0} · {g}~${ro['subagent_saved']:.2f}{eq} saved{r0}"
                     f"{dim} against the session's model, from their own usage{r0}")
    if ro["effort_turns"] or ro["effort_cache"]:
        verdict = {"keeps": "the prompt cache survives it", "rewrites": "it re-wrote the prompt cache, so it is off"}.get(ro["effort_cache"] or "", "the cache check is pending")
        lines.append(f"  {b}low effort{r0}         {ro['effort_turns']} routine turn(s){dim} · {verdict}{r0}")
    if view:
        lines.append(f"  {b}plan{r0}               {g}{plan_line(view)}{r0}{dim} · "
                     + " · ".join(f"{_window_name(k)} {w['used']:g}% used" for k, w in view["windows"].items()) + f"{r0}")
    caught = [f"guard asked {sf['asked']}×"] if sf["asked"] else []
    caught += [f"{sf['pages_flagged']} fetched page(s) flagged"] if sf["pages_flagged"] else []
    caught += [f"{sf['files_flagged']} instruction file(s) flagged"] if sf["files_flagged"] else []
    caught += [f"{sf['triaged']} red run(s) sorted"] if sf["triaged"] else []
    caught += [f"{sf['claims']} unbacked claim(s) caught"] if sf["claims"] else []
    lines.append(f"  {b}safety{r0}             " + (f"{y}{' · '.join(caught)}{r0}" if caught else f"{dim}nothing to flag{r0}")
                 + f"{dim} · {sf['checks']:,} checks{r0}")
    if j["labels"]:
        lines.append(f"  {b}by command{r0}         {dim}" + " · ".join(f"{k} {fmt_k(v)}" for k, v in list(j["labels"].items())[:6]) + f"{r0}")
    if j["hook_labels"]:
        lines.append(f"  {b}safety checks{r0}      {dim}" + " · ".join(f"{k} {fmt_k(v)}" for k, v in list(j["hook_labels"].items())[:5])
                     + f" · these went through jev but are not text the agent avoided reading{r0}")
    lines.append(f"  {b}jev's own cost{r0}     ${j['paid']:.4f}{dim} on your jev backend, nothing from the Claude plan{r0}")
    if m and m["usd"] > 0:
        lines.append(f"  {dim}the session's spend is mostly the conversation itself re-sent every turn ({fmt_k(m['cache_read'])} cache-read tokens); "
                     f"jev only touches what it kept out of it{r0}")
    if j["saved_total"] < MONEY_FLOOR:
        lines.append(f"  {dim}jev pays off on reading-heavy work: large searches, logs, test suites, long diffs, many items to sort. "
                     f"This session had little of it.{r0}")
    return "\n".join(lines)


def one_line(s: dict) -> str:
    """A status-line sized version."""
    j, view = s["jev"], s.get("plan")
    if not j["requests"] and not j["asked"] and not j.get("trimmed"):
        return "jev: nothing decided yet this session"
    sf = j["safety"]
    worth = plan_line(view) if view else (f"~${j['saved_total']:.2f} saved" if j["saved_total"] >= MONEY_FLOOR else f"{fmt_k(j['kept_out'])} tokens kept out")
    caught = " · ".join(x for x in (f"guard asked {sf['asked']}×" if sf["asked"] else "", f"{sf['pages_flagged']} pages flagged" if sf["pages_flagged"] else "") if x)
    return f"jev: {j['decisions']:,} decisions · {worth}" + (f" · {caught}" if caught else "") + f" · ${j['paid']:.4f} paid"

"""What a Claude Code session spent, next to what jev decided instead.

The model side is estimated from the transcript Claude Code writes (`~/.claude/projects/<cwd>/
<session>.jsonl`): every assistant turn carries `usage` (input, output, cache read, cache write),
priced at list price per model. One API message spans several lines, one per content block, with
the same id and usage; each id counts once. The transcript is read incrementally, so a watcher
parses the file once and then only what each turn appends.

The jev side is the ledger: rows tagged with this session (the plugin's SessionStart hook puts the
tag in every Bash command's environment; the hooks tag their own rows), or, without a tag, rows
from this directory inside the session's time window.
"""

from __future__ import annotations

import json
import os
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path

from . import ledger, settings
from .render import BOLD, DIM, GREEN, RESET, YELLOW, fmt_k

MODEL_PRICES = {  # USD per million tokens: input, output, cache read, cache write (list)
    "claude-fable-5-1": (10.0, 50.0, 0.25, 12.5),
    "claude-fable-5": (10.0, 50.0, 1.0, 12.5),
    "claude-opus-5": (5.0, 25.0, 0.5, 6.25),
    "claude-opus-4": (5.0, 25.0, 0.5, 6.25),
    "claude-sonnet-5": (2.0, 10.0, 0.2, 2.5),
    "claude-sonnet-4": (3.0, 15.0, 0.3, 3.75),
    "claude-haiku-4": (1.0, 5.0, 0.1, 1.25),
}
CONTEXT_SIZE = {"claude-fable": 1_000_000, "claude-opus-5": 1_000_000, "claude-opus-4-6": 1_000_000, "claude-opus-4-7": 1_000_000,
                "claude-opus-4-8": 1_000_000, "claude-sonnet-5": 1_000_000, "claude-sonnet-4-6": 1_000_000}
DEFAULT_PRICE = (5.0, 25.0, 0.5, 6.25)


def price_for(model: str) -> tuple[float, float, float, float]:
    table = {**MODEL_PRICES, **{k: tuple(v) for k, v in (settings.config().get("model_prices") or {}).items()}}
    best = max((k for k in table if model.startswith(k)), key=len, default=None)
    return table[best] if best else DEFAULT_PRICE


def context_size(model: str) -> int:
    best = max((k for k in CONTEXT_SIZE if model.startswith(k)), key=len, default=None)
    return CONTEXT_SIZE[best] if best else 200_000


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
        self.turn_ts: list[str] = []  # sorted, UTC, "YYYY-MM-DDTHH:MM:SS"
        self.first = self.last = None
        self.last_ctx = 0
        self.last_model = ""

    @property
    def session(self) -> str:
        return self.path.stem

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
            if ts:
                self.turn_ts.insert(bisect_right(self.turn_ts, ts), ts)
                self.first = ts if self.first is None or ts < self.first else self.first
                self.last = ts if self.last is None or ts > self.last else self.last
            model = m.get("model") or "?"
            d = self.per.setdefault(model, {"turns": 0, "in": 0, "out": 0, "cr": 0, "cw": 0})
            d["turns"] += 1
            d["in"] += u.get("input_tokens") or 0
            d["out"] += u.get("output_tokens") or 0
            d["cr"] += u.get("cache_read_input_tokens") or 0
            d["cw"] += u.get("cache_creation_input_tokens") or 0
            self.last_ctx = (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)
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


def span(a: str | None, z: str | None) -> str:
    if not a or not z:
        return "?"
    try:
        la, lz = to_local(a), to_local(z)
    except ValueError:
        return f"{a[11:16]}→{z[11:16]} UTC"
    return f"{la:%H:%M}→{lz:%H:%M}" if la.date() == lz.date() else f"{la:%b %d %H:%M}→{lz:%b %d %H:%M}"


def jev_side(cwd: str, session_id: str | None, since: str, turn_ts: list[str] | None = None, cache_read_price: float = 0.25) -> dict:
    rows = ledger.session_rows(cwd, session_id, since)
    tag = ledger.session_tag(session_id)
    hooks = ledger.hook_rows()
    asked = ledger.asked_count(hooks, since, tag)
    # command output the trim hook kept out of the context, by session tag (or the time window without one)
    trims = [h for h in hooks if h.get("hook") == "trim" and h["ts"] >= since and (not tag or h.get("agent") in (None, "", tag))]
    trimmed = sum(int(h.get("dropped_tokens") or 0) for h in trims)
    tokens = sum(r.get("in", 0) + (r.get("cached_in") or 0) for r in rows)
    paid = settings.cost_usd(sum(r.get("in", 0) for r in rows))
    kept_out = tokens + trimmed
    once = kept_out / 1e6 * settings.agent_price()
    reread = 0.0
    if turn_ts:  # text the agent reads is re-sent on every later turn as a cache read while it stays in context
        for ts, n in [(r["ts"], r.get("in", 0) + (r.get("cached_in") or 0)) for r in rows] + [(h["ts"], int(h.get("dropped_tokens") or 0)) for h in trims]:
            try:
                utc = datetime.fromisoformat(ts).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            except (ValueError, KeyError):
                continue
            after = len(turn_ts) - bisect_right(turn_ts, utc)
            reread += n / 1e6 * after * cache_read_price
    labels: dict[str, int] = {}
    for r in rows:
        labels[str(r.get("cmd") or "?")] = labels.get(str(r.get("cmd") or "?"), 0) + r.get("in", 0) + (r.get("cached_in") or 0)
    would = once + reread
    return {"requests": len(rows), "decisions": sum(r.get("q", 0) for r in rows), "tokens": tokens, "cached": sum(1 for r in rows if r.get("cached")),
            "trimmed": trimmed, "trim_runs": len(trims), "kept_out": kept_out,
            "paid": paid, "once": once, "reread": reread, "would": would, "saved": would - paid, "asked": asked,
            "labels": dict(sorted(labels.items(), key=lambda kv: -kv[1])), "agent_price": settings.agent_price(), "cache_read_price": cache_read_price}


def session_summary(cwd: str, session_id: str | None = None, transcript: Transcript | Path | None = None) -> dict:
    """Everything `jev session`, `jev watch` and the MCP `session` tool show."""
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
    jev = jev_side(cwd, sid, since, tr.turn_ts if tr else None, price_for(tr.last_model)[2] if tr and tr.last_model else 0.25)
    if model and model["usd"] > 0:
        jev["share"] = jev["saved"] / model["usd"]
    return {"session": sid, "cwd": cwd, "transcript": str(tr.path) if tr else None, "since": since, "model": model, "jev": jev}


def render_session(s: dict, color: bool = True, jev_only: bool = False, title: str = "jev session") -> str:
    b, dim, g, y, r0 = (BOLD, DIM, GREEN, YELLOW, RESET) if color else ("",) * 5
    m, j = s["model"], s["jev"]
    sid = s["session"]
    lines = [f"{b}{title}{r0} · {Path(s['cwd']).name}" + (f" · session {sid[:8]}" if sid else "")
             + (f" · {span(m['first'], m['last'])}" if m else "") + (f"{dim} · no transcript found{r0}" if not m else "")
             + f"{dim} · {datetime.now():%H:%M:%S}{r0}"]
    if m and not jev_only:
        lines.append(f"\n{b}model side{r0}{dim}  estimated at list price from the transcript; Claude Code's own figure may differ{r0}")
        for model, d in sorted(m["per"].items(), key=lambda kv: -kv[1]["usd"]):
            lines.append(f"  {model.replace('claude-', ''):<14}{d['turns']:>5} turns   in {fmt_k(d['in']):>6} · out {fmt_k(d['out']):>6} · cache read {fmt_k(d['cr']):>7}"
                         f" · cache write {fmt_k(d['cw']):>6}   {y}~${d['usd']:,.2f}{r0}")
        lines.append(f"  {'session':<14}{m['turns']:>5} turns   {y}~${m['usd']:,.2f}{r0} · context now {fmt_k(m['ctx'])} tokens ({100 * m['ctx'] / m['ctx_size']:.0f}% of {fmt_k(m['ctx_size'])})")
    lines.append(f"\n{b}jev side{r0}{dim}  this session{r0}")
    if not j["requests"] and not j["asked"] and not j.get("trimmed"):
        lines.append(f"  {dim}nothing decided yet this session · `jev sift`, `jev tests`, `jev cluster` … will show up here{r0}")
        return "\n".join(lines)
    lines.append(f"  {b}went through jev{r0}   {g}{j['decisions']:,} decisions{r0} over {j['tokens']:,} tokens of text · {j['requests']} request(s)"
                 + (f" ({j['cached']} from cache)" if j["cached"] else "") + f" · {g}${j['paid']:.4f} paid to jev{r0}"
                 + (f" · {y}hooks asked {j['asked']}×{r0}" if j["asked"] else ""))
    if j.get("trimmed"):
        lines.append(f"  {b}trimmed{r0}            {g}{j['trimmed']:,} tokens{r0} of command output kept out of the context in {j['trim_runs']} run(s) (the full outputs are on disk)")
    lines.append(f"  {b}would have cost{r0}    {y}~${j['would']:.2f}{r0} had the agent read that text itself: ${j['once']:.2f} once as input (${j['agent_price']:g}/M)"
                 + (f" + ${j['reread']:.2f} re-read on the later turns (cache, ${j['cache_read_price']:g}/M)" if m else ""))
    lines.append(f"  {b}saved{r0}              {g}~${j['saved']:.2f} at most{r0}{dim} — minus whatever the agent read anyway from the uncertain band{r0}"
                 + (f" · ≈ {100 * j['share']:.1f}% of this session's ~${m['usd']:,.2f}" if m and "share" in j else ""))
    if j["labels"]:
        top = " · ".join(f"{k} {fmt_k(v)}" for k, v in list(j["labels"].items())[:6])
        lines.append(f"  {b}by command{r0}         {dim}{top}{r0}")
    if m and m["usd"] > 0:
        lines.append(f"  {dim}the session's spend is mostly the conversation itself re-sent every turn ({fmt_k(m['cache_read'])} cache-read tokens); "
                     f"jev only touches what it kept out of it{r0}")
    return "\n".join(lines)


def one_line(s: dict) -> str:
    """A status-line sized version."""
    j = s["jev"]
    if not j["requests"] and not j["asked"] and not j.get("trimmed"):
        return "jev: nothing decided yet this session"
    return (f"jev: {j['decisions']:,} decisions · {fmt_k(j.get('kept_out', j['tokens']))} tokens kept out · ~${j['would']:.2f} not spent (ceiling)"
            + (f" · hooks asked {j['asked']}" if j["asked"] else "") + f" · ${j['paid']:.4f} paid")

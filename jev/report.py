"""What Claude Code cost over a span of days, and what jev did in it.

The model side is read from the transcripts Claude Code writes under ~/.claude/projects (subagents
included): every request inside the span, priced per model at list price, with the size of the
context each main-thread request read. The jev side is read from the two ledgers: requests by who
made them, and the guard's checks, questions, answers, commands it could not judge, and the time a
command waited for it. Run it before and after a change to see what the change did.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import ledger, settings
from .metrics import price_for

PROJECTS = Path.home() / ".claude" / "projects"
BIG_CONTEXT = 700_000
GUARD_GROUP_S = 9  # one guard check's parallel requests end within a few seconds of each other


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _local(dt: datetime) -> str:
    return dt.astimezone().strftime("%Y-%m-%dT%H:%M:%S")


def _pct(values: list[int | float], p: float):
    s = sorted(values)
    return s[min(len(s) - 1, int(p * len(s)))] if s else None


def transcripts(since: datetime, root: Path = PROJECTS) -> tuple[list[Path], list[Path]]:
    """Main transcripts and subagent transcripts written to since `since`."""
    cutoff = since.timestamp()
    def fresh(paths):
        out = []
        for p in paths:
            try:
                if p.stat().st_mtime >= cutoff:
                    out.append(p)
            except OSError:
                pass
        return sorted(out)
    try:
        return fresh(root.glob("*/*.jsonl")), fresh(root.glob("*/*/subagents/*.jsonl"))
    except OSError:
        return [], []


def model_side(main: list[Path], subagents: list[Path], start: str, end: str) -> dict:
    """Requests between `start` and `end` (UTC): cost per model, the context each main-thread request read, and the
    compactions (who started them, from what size, how long they took)."""
    per: dict[str, dict] = {}
    contexts: list[int] = []
    compactions: list[dict] = []
    sessions: set[str] = set()
    for path in [*main, *subagents]:
        is_main = path in main
        seen: set[str] = set()
        try:
            f = open(path, "rb")
        except OSError:
            continue
        with f:
            for line in f:
                if b'"compact_boundary"' in line:
                    try:
                        o = json.loads(line)
                    except ValueError:
                        continue
                    ts = (o.get("timestamp") or "")[:19]
                    if o.get("subtype") == "compact_boundary" and start <= ts < end and is_main:
                        m = o.get("compactMetadata") or {}
                        compactions.append({"ts": ts, "trigger": m.get("trigger"), "pre": int(m.get("preTokens") or 0),
                                            "ms": int(m.get("durationMs") or 0), "session": path.stem})
                    continue
                if b'"assistant"' not in line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                msg = o.get("message") or {}
                u = msg.get("usage") or {}
                ts = (o.get("timestamp") or "")[:19]
                if not u or not (start <= ts < end):
                    continue
                mid = msg.get("id") or o.get("uuid")
                if mid in seen:
                    continue
                seen.add(mid)
                model = msg.get("model") or "?"
                if model.startswith("<"):
                    continue
                d = per.setdefault(model, {"requests": 0, "in": 0, "out": 0, "cr": 0, "cw": 0})
                d["requests"] += 1
                for key, field in (("in", "input_tokens"), ("out", "output_tokens"), ("cr", "cache_read_input_tokens"),
                                   ("cw", "cache_creation_input_tokens")):
                    d[key] += u.get(field) or 0
                if is_main and not o.get("isSidechain"):
                    contexts.append((u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0))
                    sessions.add(path.stem)
    usd = 0.0
    for model, d in per.items():
        pi, po, pcr, pcw = price_for(model)
        d["usd"] = round(d["in"] / 1e6 * pi + d["out"] / 1e6 * po + d["cr"] / 1e6 * pcr + d["cw"] / 1e6 * pcw, 4)
        usd += d["usd"]
    return {"sessions": len(sessions), "requests": sum(d["requests"] for d in per.values()), "usd": round(usd, 2),
            "cache_read": sum(d["cr"] for d in per.values()), "per_model": per,
            "context": {"requests": len(contexts), "mean": round(sum(contexts) / len(contexts)) if contexts else 0,
                        "p50": _pct(contexts, 0.5), "p90": _pct(contexts, 0.9),
                        "over_700k": round(sum(c > BIG_CONTEXT for c in contexts) / len(contexts), 3) if contexts else 0},
            "compactions": compactions}


def guard_waits(usage: list[dict], hooks: list[dict]) -> list[int]:
    """How long each guard check kept its command waiting, in ms: the check's own `ms` when it logged one, else
    its requests to the model grouped by session and time (they run side by side; the slowest is the wait)."""
    logged = [int(h["ms"]) for h in hooks if h.get("hook") == "guard" and isinstance(h.get("ms"), (int, float))]
    if logged:
        return logged
    waits: list[int] = []
    group: list[tuple[float, str, int]] = []
    for r in usage:
        if r.get("cmd") != "hook:guard":
            continue
        try:
            t = datetime.fromisoformat(r["ts"]).timestamp()
        except (KeyError, ValueError):
            continue
        if group and (r.get("agent") != group[-1][1] or t - group[0][0] > GUARD_GROUP_S):
            waits.append(max(g[2] for g in group))
            group = []
        group.append((t, r.get("agent") or "", int(r.get("ms") or 0)))
    if group:
        waits.append(max(g[2] for g in group))
    return waits


def jev_side(usage: list[dict], hooks: list[dict]) -> dict:
    callers: dict[str, dict] = {}
    for r in usage:
        agent = str(r.get("agent") or "")
        who = "sessions" if agent.startswith("session:") else (agent or "untagged")
        c = callers.setdefault(who, {"requests": 0, "tokens": 0, "errors": 0})
        c["requests"] += 1
        c["tokens"] += (r.get("in") or 0) + (r.get("cached_in") or 0)
        c["errors"] += 1 if "err" in r else 0
    guard = [h for h in hooks if h.get("hook") == "guard"]
    answers = [h for h in hooks if h.get("hook") == "guard-answer"]
    waits = guard_waits(usage, hooks)
    commands = len([h for h in guard if h.get("via") != "mod"])  # the mod's own row repeats a check already logged
    return {
        "requests": len(usage), "callers": dict(sorted(callers.items(), key=lambda kv: -kv[1]["requests"])),
        "paid_usd": round(settings.price_usd(sum(r.get("in", 0) for r in usage if not r.get("local") and "err" not in r)), 4),
        "guard": {"commands": commands, "judged": sum(1 for h in guard if "p" in h and h.get("via") != "mod"),
                  "questions": sum(1 for h in guard if h.get("decision") in ("ask", "deny", "warn")),
                  "answers": {a: sum(1 for h in answers if h.get("answer") == a) for a in ("run", "refuse", "dismissed")},
                  "unchecked": sum(1 for h in guard if "err" in h),
                  "wait_ms": {"p50": _pct(waits, 0.5), "p90": _pct(waits, 0.9), "total_h": round(sum(waits) / 3.6e6, 2)}},
        "compactions_by_jev": sum(1 for h in hooks if h.get("hook") == "compact-auto"),
        "precompact": {"runs": sum(1 for h in hooks if h.get("hook") == "compact" and "err" not in h),
                       "errors": sum(1 for h in hooks if h.get("hook") == "compact" and "err" in h),
                       "rereads": sum(1 for h in hooks if h.get("hook") == "compact-reread")},
    }


def build(days: float = 7, now: datetime | None = None, root: Path = PROJECTS) -> dict:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    main, subs = transcripts(since, root)
    start_local, end_local = _local(since), _local(now)
    usage = [r for r in ledger.read_since(settings.USAGE_FILE, start_local) if r["ts"] < end_local]
    hooks = [h for h in ledger.read_since(settings.HOOKS_LOG, start_local) if h["ts"] < end_local]
    return {"from": start_local, "to": end_local, "days": days,
            "model": model_side(main, subs, _utc(since), _utc(now)), "jev": jev_side(usage, hooks)}


def _k(n) -> str:
    n = n or 0
    return f"{n / 1e9:.2f}B" if n >= 1e9 else f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.0f}K" if n >= 1e3 else str(n)


def render(r: dict) -> str:
    m, j = r["model"], r["jev"]
    c = m["context"]
    comps = m["compactions"]
    by = {t: sum(1 for x in comps if x["trigger"] == t) for t in {x["trigger"] for x in comps}}
    mins = sorted(x["ms"] for x in comps if x["ms"])
    g = j["guard"]
    a = g["answers"]
    w = g["wait_ms"]
    callers = " · ".join(f"{name} {v['requests']:,}" for name, v in j["callers"].items())
    lines = [
        f"jev report · {r['from'][:16].replace('T', ' ')} → {r['to'][:16].replace('T', ' ')}",
        f"  model        ~${m['usd']:,.2f} API equivalent · {m['requests']:,} requests in {m['sessions']} sessions · cache reads {_k(m['cache_read'])} tokens",
        f"  context      mean {_k(c['mean'])} · p90 {_k(c['p90'])} · {c['over_700k']:.0%} of requests above 700K",
        f"  compactions  {len(comps)} by Claude Code ({', '.join(f'{n} {t}' for t, n in sorted(by.items(), key=lambda kv: str(kv[0])))})"
        + (f", median {mins[len(mins) // 2] / 60000:.1f} min" if mins else "") + f" · {j['compactions_by_jev']} started by jev"
        + f" · results read again after one: {j['precompact']['rereads']}",
        f"  guard        {g['commands']:,} commands · {g['questions']} questions · answers: run {a['run']}, refused {a['refuse']}, "
        f"dismissed {a['dismissed']} · {g['unchecked']} without a verdict",
        f"  guard wait   median {(w['p50'] or 0) / 1000:.1f} s · p90 {(w['p90'] or 0) / 1000:.1f} s · {w['total_h']} h in all",
        f"  jev          {j['requests']:,} requests ({callers}) · paid ${j['paid_usd']:.2f}",
    ]
    return "\n".join(lines)

"""The local record of every request: metadata only, never the state or the answers.

One JSON line per request in usage.jsonl. `jev usage` reads it whole; the status line, the
hooks and `jev watch` read a tail of it, because they run often and only need one session.
Timestamps are local ISO seconds, which sort as strings; comparisons stay string comparisons.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

from . import settings

WINDOW_ROWS_BYTES = 4_000_000


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def agent_tag() -> str:
    """Who made the call: an explicit JEV_AGENT, else the Claude Code session (JEV_SESSION is written
    to every Bash command's environment by the plugin's SessionStart hook), else a generic AGENT_ID."""
    return os.environ.get("JEV_AGENT") or os.environ.get("JEV_SESSION") or os.environ.get("AGENT_ID") or ""


def touch_session(session_id: str, cwd: str = "", transcript: str | None = None) -> dict:
    """Remember when a session was first seen (and where its transcript is). Returns the marker."""
    now = time.time()
    if not session_id:
        return {"first_seen": now}
    path = settings.SESSIONS_DIR / f"{session_id}.json"
    try:
        marker = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError):
        marker = {}
    marker.setdefault("first_seen", now)
    marker["last_seen"] = now
    if cwd:
        marker["cwd"] = cwd
    if transcript:
        marker["transcript"] = transcript
    try:
        settings.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(marker))
    except OSError:
        pass
    return marker


def session_marker(session_id: str) -> dict | None:
    try:
        return json.loads((settings.SESSIONS_DIR / f"{session_id}.json").read_text())
    except (OSError, ValueError):
        return None


def record(row: dict) -> None:
    try:
        settings.HOME.mkdir(parents=True, exist_ok=True)
        with open(settings.USAGE_FILE, "a") as f:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
    except OSError:
        pass


def read_tail(path: Path, max_bytes: int | None) -> str:
    try:
        with open(path, "rb") as f:
            if max_bytes is not None:
                size = os.fstat(f.fileno()).st_size
                if size > max_bytes:
                    f.seek(size - max_bytes)
                    f.readline()  # drop the partial line
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def parse_rows(text: str) -> list[dict]:
    rows = []
    loads = json.loads
    for line in text.splitlines():
        if not line:
            continue
        try:
            r = loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and "ts" in r:
            rows.append(r)
    return rows


def rows(since: str | None = None, tail_bytes: int | None = None) -> list[dict]:
    out = parse_rows(read_tail(settings.USAGE_FILE, tail_bytes))
    if since:
        out = [r for r in out if r["ts"] >= since]
    return out


def usage_row(label: str, n_questions: int, ms: float, resp: dict | None, request_id: str = "",
              attempts: int = 1, cached: bool = False, err: Exception | None = None) -> dict:
    row = {"ts": now_iso(), "cmd": label, "agent": agent_tag(), "q": n_questions, "ms": round(ms), "cwd": os.getcwd()}
    if resp is not None:
        u = resp.get("usage") or {}
        row.update(model=resp.get("model"), **{"in": u.get("input_tokens", 0), "out": u.get("output_tokens", 0)})
        if request_id:
            row["rid"] = request_id
        if attempts > 1:
            row["attempts"] = attempts
        if cached:
            row["cached"] = True
            row["cached_in"] = (resp.get("cached_usage") or {}).get("input_tokens", 0)
    else:
        msg = str(err)
        row.update({"in": 0, "out": 0, "err": msg.split(":", 1)[0].replace("HTTP ", "").strip() if msg.startswith("HTTP") else type(err).__name__})
    return row


def aggregate(sel: list[dict]) -> dict:
    ok = [r for r in sel if "err" not in r]
    tin = sum(r.get("in", 0) for r in ok)
    ms = sorted(r["ms"] for r in ok if r.get("ms"))
    pct = (lambda p: ms[min(len(ms) - 1, int(len(ms) * p))]) if ms else (lambda p: 0)
    return {"requests": len(sel), "errors": len(sel) - len(ok), "cached": sum(1 for r in ok if r.get("cached")),
            "questions": sum(r.get("q", 0) for r in ok), "input_tokens": tin, "output_tokens": sum(r.get("out", 0) for r in ok),
            "not_read_tokens": tin + sum(r.get("cached_in", 0) or 0 for r in ok),
            "usd": round(settings.cost_usd(tin), 6), "avg_ms": round(sum(ms) / len(ms)) if ms else 0,
            "p50_ms": pct(0.5), "p95_ms": pct(0.95), "max_ms": ms[-1] if ms else 0}


def peaks(sel: list[dict]) -> dict:
    per_min: dict[str, int] = {}
    per_sec: dict[str, int] = {}
    for r in sel:
        per_min[r["ts"][:16]] = per_min.get(r["ts"][:16], 0) + 1
        per_sec[r["ts"]] = per_sec.get(r["ts"], 0) + r.get("in", 0)
    return {"peak_rpm": max(per_min.values(), default=0), "peak_rpm_at": max(per_min, key=per_min.get) if per_min else None,
            "rpm_limit": settings.rpm_limit(), "peak_tps": max(per_sec.values(), default=0),
            "peak_tps_at": max(per_sec, key=per_sec.get) if per_sec else None, "tps_limit": settings.TPS_LIMIT}


def session_tag(session_id: str | None) -> str | None:
    return f"session:{session_id[:8]}" if session_id else None


def session_rows(cwd: str, session_id: str | None, since: str) -> list[dict]:
    """One session's rows: the hook tag when a hook made the call, else cwd and the time window
    (approximate when two sessions share a directory)."""
    tag = session_tag(session_id)
    out = []
    for r in rows(tail_bytes=WINDOW_ROWS_BYTES):
        if "err" in r:
            continue
        if (tag and r.get("agent") == tag) or (cwd and str(r.get("cwd", "")).startswith(cwd) and r["ts"] >= since):
            out.append(r)
    return out


def log_hook(hook: str, row: dict) -> None:
    try:
        settings.HOME.mkdir(parents=True, exist_ok=True)
        with open(settings.HOOKS_LOG, "a") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "hook": hook, "agent": agent_tag(), **row}) + "\n")
    except OSError:
        pass


def hook_rows(tail_bytes: int | None = 1_000_000) -> list[dict]:
    return parse_rows(read_tail(settings.HOOKS_LOG, tail_bytes))


def asked_count(hooks: list[dict], since: str, tag: str | None) -> int:
    return sum(1 for r in hooks if r.get("hook") == "guard" and r.get("decision") not in (None, "-") and r["ts"] >= since
               and (not tag or r.get("agent") in (None, "", tag)))

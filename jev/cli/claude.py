"""Claude Code: two advisory hooks, a status line, and a live view for the desktop app.

The hooks never answer `allow` (that would bypass the permission rules the person chose) and
answer `deny` only above a high bar and only where no prompt can appear. Everything here fails
open: a hook or status line that cannot reach the API prints nothing extra and the normal flow
continues.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path

from .. import ledger, settings
from ..errors import UsageError
from ..render import BOLD, DIM, GREEN, RESET, YELLOW, eprint, fmt_k
from ._common import out_path

DEFAULT_SETTINGS = "~/.claude/settings.json"

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


def register(sub) -> None:
    hk = sub.add_parser("hooks", help="install | uninstall | status of the Claude Code hooks (Bash guard, WebFetch screen)",
                        description="Advisory hooks. guard, on Bash before it runs: asks when p(destructive) >= JEV_GUARD_ASK (0.60); never "
                                    "allows; denies only at >= JEV_GUARD_DENY (0.90) and only in bypassPermissions mode or with "
                                    "JEV_GUARD_MODE=deny, where no prompt can appear. screen, on WebFetch after it returns: one line of "
                                    "context when p(instructions aimed at an agent) >= JEV_SCREEN_WARN (0.55). Both fail open and log.")
    hk.add_argument("action", nargs="?", choices=["status", "install", "uninstall"], default="status")
    hk.add_argument("--settings", help=f"settings file to edit (default {DEFAULT_SETTINGS}; .claude/settings.json for one project)")
    hk.add_argument("--no-guard", action="store_true", help="install only the WebFetch screen")
    hk.add_argument("--no-screen", action="store_true", help="install only the Bash guard")
    hk.add_argument("--tail", type=int, default=12, help="status: show the last N log lines")
    hk.set_defaults(fn=cmd_hooks)

    h = sub.add_parser("hook", help="run one hook (Claude Code calls this; the payload arrives on stdin)")
    h.add_argument("which", choices=["guard", "screen"])
    h.set_defaults(fn=cmd_hook)

    sl = sub.add_parser("statusline", help="install | uninstall | status | preview | render the Claude Code status line",
                        description="One row under the prompt: the session's model, cost and context use (from Claude Code) next to what "
                                    "jev decided for this session. A status line configured before is kept as the first row.")
    sl.add_argument("action", nargs="?", choices=["status", "install", "uninstall", "preview", "render"], default="status")
    sl.add_argument("--settings", help=f"settings file (default {DEFAULT_SETTINGS})")
    sl.add_argument("--refresh", type=int, metavar="SECONDS", help="also re-run every N seconds while idle")
    sl.set_defaults(fn=cmd_statusline)

    wt = sub.add_parser("watch", help="live view: this session's estimated model spend next to what jev decided (the desktop app ignores status lines)",
                        description="Reads the transcript Claude Code writes (~/.claude/projects/<cwd>/<session>.jsonl) incrementally, prices "
                                    "each turn at list price per model, and shows it next to jev's side of the session. Run it in the app's "
                                    "Terminal panel; --once prints and exits.")
    wt.add_argument("--cwd", help="project directory (default: the current one)")
    wt.add_argument("--transcript", help="a specific transcript file")
    wt.add_argument("--session", help="session id prefix to follow (default: the most recently active)")
    wt.add_argument("--interval", type=float, default=5.0)
    wt.add_argument("--once", action="store_true")
    wt.add_argument("--jev-only", action="store_true", help="only the jev block")
    wt.set_defaults(fn=cmd_watch)


# ---------------------------------------------------------------- settings.json plumbing

def launcher() -> str:
    """The absolute command that runs this jev, for hook and status line entries."""
    argv0 = Path(sys.argv[0]).resolve() if sys.argv and sys.argv[0] else None
    if argv0 and argv0.name == "jev" and argv0.exists():
        return str(argv0)
    found = shutil.which("jev")
    return found or str(Path.home() / ".local" / "bin" / "jev")


def _read_settings(path: Path) -> dict:
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except ValueError as e:
        raise UsageError(f"{path} is not valid JSON: {e}")


def _write_settings(path: Path, cfg: dict) -> None:
    if path.exists():
        path.with_suffix(".json.bak-jev").write_text(path.read_text())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2) + "\n")


def _ours_hook(entry: dict) -> bool:
    for h in entry.get("hooks", []) or []:
        cmd = (h.get("command") or "") if isinstance(h, dict) else ""
        if ("jev" in cmd and " hook " in cmd) or "/jev/hooks/" in cmd:
            return True
    return False


def _ours_statusline(cmd: str | None) -> bool:
    return bool(cmd) and (("jev" in cmd and cmd.rstrip().endswith("statusline render")) or "/jev/statusline.py" in cmd)


def cmd_hooks(args) -> int:
    path = out_path(args.settings or DEFAULT_SETTINGS)
    cfg = _read_settings(path)
    hooks = cfg.get("hooks") or {}
    have = {ev: any(_ours_hook(e) for e in (hooks.get(ev) or [])) for ev in ("PreToolUse", "PostToolUse")}
    if args.action == "status":
        print(f"{path}" + ("" if path.exists() else " (does not exist)"))
        print(f"  guard  (PreToolUse Bash -> ask)        {'installed' if have['PreToolUse'] else 'not installed'}")
        print(f"  screen (PostToolUse WebFetch -> note)  {'installed' if have['PostToolUse'] else 'not installed'}")
        print(f"  command: {launcher()} hook guard|screen")
        print("  env: JEV_GUARD_ASK=0.60 JEV_GUARD_DENY=0.90 JEV_GUARD_MODE=ask|deny|off · JEV_SCREEN_WARN=0.55 JEV_SCREEN_MODE=on|off")
        rows = ledger.hook_rows(None)
        if rows:
            g = [r for r in rows if r.get("hook") == "guard"]
            sc = [r for r in rows if r.get("hook") == "screen"]
            print(f"  log: {settings.HOOKS_LOG} · guard {len(g)} decisions ({sum(1 for r in g if r.get('decision') not in (None, '-'))} asked or denied, "
                  f"{sum(1 for r in g if 'err' in r)} errors) · screen {len(sc)} pages ({sum(1 for r in sc if r.get('warned'))} warned)")
            for r in rows[-args.tail:]:
                what = r.get("cmd") or r.get("src") or ""
                print(f"    {r.get('ts', '')[11:]} {r.get('hook', ''):<6} p={r.get('p', '-'):<5} {r.get('decision') or ('warn' if r.get('warned') else '-'):<5} "
                      f"{str(what)[:90]}" + (f"  ERR {r['err']}" if "err" in r else ""))
        else:
            print(f"  log: {settings.HOOKS_LOG} (nothing yet)")
        return 0
    for ev in ("PreToolUse", "PostToolUse"):
        hooks[ev] = [e for e in (hooks.get(ev) or []) if not _ours_hook(e)]
    if args.action == "install":
        run = launcher()
        if not args.no_guard:
            hooks["PreToolUse"].append({"matcher": "Bash", "hooks": [{"type": "command", "command": f"{run} hook guard", "timeout": 10}]})
        if not args.no_screen:
            hooks["PostToolUse"].append({"matcher": "WebFetch", "hooks": [{"type": "command", "command": f"{run} hook screen", "timeout": 15}]})
    hooks = {ev: v for ev, v in hooks.items() if v}
    if hooks:
        cfg["hooks"] = hooks
    else:
        cfg.pop("hooks", None)
    _write_settings(path, cfg)
    if args.action == "install":
        print(f"installed into {path} (backup: *.bak-jev) · takes effect in the next session")
        print("  guard : Bash, before it runs      -> asks at p(destructive) >= 0.60; never allows; denies only at >= 0.90 where no prompt can appear")
        print("  screen: WebFetch, after it returns -> one line of context at p(instructions aimed at an agent) >= 0.55")
        print("  `jev hooks status` tails the decisions; the JEV_GUARD_* / JEV_SCREEN_* variables move the bars")
    else:
        print(f"removed the jev hooks from {path}")
    return 0


# ---------------------------------------------------------------- the hooks themselves

def cmd_hook(args) -> int:
    from ..hooks import run
    return run(args.which)


# ---------------------------------------------------------------- status line

def _session_start(sid: str, cwd: str) -> float:
    now = time.time()
    if not sid:
        return now
    marker = settings.SESSIONS_DIR / f"{sid}.json"
    try:
        settings.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        first = float(json.loads(marker.read_text()).get("first_seen", now)) if marker.exists() else now
        marker.write_text(json.dumps({"first_seen": first, "last_seen": now, "cwd": cwd}))
        return first
    except (OSError, ValueError):
        return now


def render_statusline(raw: str) -> str:
    try:
        st = json.loads(raw) if raw.strip() else {}
    except ValueError:
        st = {}
    lines = []
    prev = settings.config().get("statusline_prev")
    if prev:
        try:
            import subprocess
            out = subprocess.run(prev, shell=True, input=raw, capture_output=True, text=True, timeout=2).stdout.rstrip("\n")
            if out:
                lines.append(out)
        except Exception:  # noqa: BLE001
            pass
    sid = str(st.get("session_id") or "")
    cwd = (st.get("workspace") or {}).get("current_dir") or st.get("cwd") or ""
    since = datetime.fromtimestamp(_session_start(sid, cwd) - 5).isoformat(timespec="seconds")
    model = (st.get("model") or {}).get("display_name") or ""
    cost = (st.get("cost") or {}).get("total_cost_usd")
    ctx = (st.get("context_window") or {}).get("used_percentage")
    left = " · ".join(x for x in (model, f"${cost:.2f}" if isinstance(cost, (int, float)) else "", f"ctx {ctx:.0f}%" if isinstance(ctx, (int, float)) else "") if x)
    mine = ledger.session_rows(cwd, sid or None, since)
    decisions = sum(r.get("q", 0) for r in mine)
    tokens = sum(r.get("in", 0) + (r.get("cached_in") or 0) for r in mine)
    paid = settings.cost_usd(sum(r.get("in", 0) for r in mine))
    avoided = tokens / 1e6 * settings.agent_price()
    asked = ledger.asked_count(ledger.hook_rows(), since, ledger.session_tag(sid or None))
    if mine or asked:
        right = (f"{GREEN}jev{RESET}: {decisions:,} decisions · {fmt_k(tokens)} tokens kept out · {GREEN}~${avoided:.2f} not spent{RESET}{DIM} (ceiling){RESET}"
                 + (f" · {YELLOW}hooks asked {asked}{RESET}" if asked else "") + f"{DIM} · ${paid:.4f} paid{RESET}")
    else:
        right = f"{DIM}jev: nothing decided yet this session{RESET}"
    line = f"{left}  {DIM}│{RESET}  {right}" if left else right
    cols = int(os.environ.get("COLUMNS") or 0)
    if cols and len(re.sub(r"\033\[[0-9;]*m", "", line)) > cols:
        line = line.replace(f"{DIM} · ${paid:.4f} paid{RESET}", "").replace(f"{DIM} (ceiling){RESET}", "")
    lines.append(line)
    return "\n".join(lines)


def cmd_statusline(args) -> int:
    if args.action == "render":
        try:
            print(render_statusline(sys.stdin.read()))
        except Exception:  # noqa: BLE001  a status line must never break the UI
            pass
        return 0
    if args.action == "preview":
        sample = {"session_id": "preview0-0000", "model": {"display_name": "Fable 5.1"}, "cost": {"total_cost_usd": 0.42},
                  "context_window": {"used_percentage": 38.0}, "workspace": {"current_dir": os.getcwd()}}
        print(render_statusline(json.dumps(sample)))
        return 0
    path = out_path(args.settings or DEFAULT_SETTINGS)
    cfg = _read_settings(path)
    current = (cfg.get("statusLine") or {}).get("command")
    if args.action == "status":
        print(f"{path}: statusLine = {current!r}")
        print("  " + ("installed (ours)" if _ours_statusline(current) else "not installed" if not current else "another status line is configured; install keeps it as the first row"))
        prev = settings.config().get("statusline_prev")
        if prev:
            print(f"  previous status line kept: {prev!r}")
        print("  the desktop app ignores status lines; there, `jev watch` in the Terminal panel shows the same numbers")
        return 0
    conf = dict(settings.config())
    if args.action == "install":
        if current and not _ours_statusline(current):
            conf["statusline_prev"] = current
            settings.save_config(conf)
        entry = {"type": "command", "command": f"{launcher()} statusline render", "padding": 0}
        if args.refresh:
            entry["refreshInterval"] = args.refresh
        cfg["statusLine"] = entry
    else:
        prev = conf.pop("statusline_prev", None)
        settings.save_config(conf)
        if prev:
            cfg["statusLine"] = {"type": "command", "command": prev}
        else:
            cfg.pop("statusLine", None)
    _write_settings(path, cfg)
    if args.action == "install":
        print(f"status line installed into {path} (backup *.bak-jev) · shows from the next terminal session"
              + (f" · the previous one ({current!r}) is kept as the first row" if current and not _ours_statusline(current) else ""))
        print("  `jev statusline preview` shows the row now · `jev config set agent_price 10` sets the agent's input price behind 'not spent'")
    else:
        print(f"status line removed from {path}")
    return 0


# ---------------------------------------------------------------- watch

def _price_for(model: str) -> tuple[float, float, float, float]:
    table = {**MODEL_PRICES, **{k: tuple(v) for k, v in (settings.config().get("model_prices") or {}).items()}}
    best = max((k for k in table if model.startswith(k)), key=len, default=None)
    return table[best] if best else (5.0, 25.0, 0.5, 6.25)


def _context_size(model: str) -> int:
    best = max((k for k in CONTEXT_SIZE if model.startswith(k)), key=len, default=None)
    return CONTEXT_SIZE[best] if best else 200_000


def find_transcript(cwd: str, explicit: str | None = None, session: str | None = None) -> Path | None:
    if explicit:
        return Path(explicit).expanduser()
    proj = Path.home() / ".claude" / "projects" / cwd.replace("/", "-")
    try:
        files = [f for f in proj.glob("*.jsonl") if not session or f.stem.startswith(session)]
    except OSError:
        return None
    return max(files, key=lambda f: f.stat().st_mtime) if files else None


class Transcript:
    """Per-model turns and tokens from a session transcript, read incrementally: only the bytes
    appended since the last refresh are parsed, so a 10 MB file costs one read at start and a few
    KB per turn after. One API message spans several lines (one per content block) with the same
    id and usage; each id counts once."""

    def __init__(self, path: Path):
        self.path = path
        self.reset()

    def reset(self) -> None:
        self.offset = 0
        self.partial = b""
        self.per: dict[str, dict] = {}
        self.seen: set[str] = set()
        self.turn_ts: list[str] = []   # sorted UTC timestamps, "YYYY-MM-DDTHH:MM:SS"
        self.first = self.last = None
        self.last_ctx = 0
        self.last_model = ""

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
            pi, po, pcr, pcw = _price_for(model)
            d["usd"] = d["in"] / 1e6 * pi + d["out"] / 1e6 * po + d["cr"] / 1e6 * pcr + d["cw"] / 1e6 * pcw
            total += d["usd"]
        return total


def _local(ts_utc: str):
    return datetime.fromisoformat(ts_utc.replace("Z", "+00:00")).replace(tzinfo=timezone.utc).astimezone()


def _span(a: str | None, z: str | None) -> str:
    if not a or not z:
        return "?"
    try:
        la, lz = _local(a), _local(z)
    except ValueError:
        return f"{a[11:16]}→{z[11:16]} UTC"
    return f"{la:%H:%M}→{lz:%H:%M}" if la.date() == lz.date() else f"{la:%b %d %H:%M}→{lz:%b %d %H:%M}"


def render_watch(cwd: str, tr: Transcript | None, jev_only: bool = False, color: bool = True) -> str:
    b, dim, g, y, r0 = (BOLD, DIM, GREEN, YELLOW, RESET) if color else ("",) * 5
    lines = []
    sid = tr.path.stem if tr else None
    total = tr.usd() if tr and tr.per else 0.0
    if tr and tr.first:
        try:
            since = _local(tr.first).replace(tzinfo=None).isoformat(timespec="seconds")
        except ValueError:
            since = tr.first
    else:
        since = datetime.now().replace(hour=0, minute=0, second=0).isoformat(timespec="seconds")
    rows = ledger.session_rows(cwd, sid, since)
    tag = ledger.session_tag(sid)
    asked = ledger.asked_count(ledger.hook_rows(), since, tag)
    head = f"{b}jev watch{r0} · {Path(cwd).name}" + (f" · session {sid[:8]} · {_span(tr.first, tr.last)}" if tr and tr.per else " · no transcript found") \
        + f"{dim} · {datetime.now():%H:%M:%S}{r0}"
    lines.append(head)
    if tr and tr.per and not jev_only:
        lines.append(f"\n{b}model side{r0}{dim}  estimated at list price from the transcript; Claude Code's own figure may differ{r0}")
        for model, d in sorted(tr.per.items(), key=lambda kv: -kv[1]["usd"]):
            lines.append(f"  {model.replace('claude-', ''):<14}{d['turns']:>5} turns   in {fmt_k(d['in']):>6} · out {fmt_k(d['out']):>6} · cache read {fmt_k(d['cr']):>7}"
                         f" · cache write {fmt_k(d['cw']):>6}   {y}~${d['usd']:,.2f}{r0}")
        ctx_size = _context_size(tr.last_model)
        lines.append(f"  {'session':<14}{sum(d['turns'] for d in tr.per.values()):>5} turns   {y}~${total:,.2f}{r0} · context now {fmt_k(tr.last_ctx)} tokens "
                     f"({100 * tr.last_ctx / ctx_size:.0f}% of {fmt_k(ctx_size)})")
    lines.append(f"\n{b}jev side{r0}{dim}  this session{r0}")
    if not rows and not asked:
        lines.append(f"  {dim}nothing decided yet this session · `jev sift`, `jev tests`, `jev cluster` … will show up here{r0}")
        return "\n".join(lines)
    decisions = sum(r.get("q", 0) for r in rows)
    tokens = sum(r.get("in", 0) + (r.get("cached_in") or 0) for r in rows)
    paid = settings.cost_usd(sum(r.get("in", 0) for r in rows))
    once = tokens / 1e6 * settings.agent_price()
    # Text the agent reads is paid once as input and again on every later turn as a cache read while
    # it stays in context; each jev row is counted against the turns that followed it.
    reread = 0.0
    cr_price = _price_for(tr.last_model)[2] if tr and tr.last_model else 0.25
    if tr and tr.turn_ts:
        for r in rows:
            try:
                utc = datetime.fromisoformat(r["ts"]).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            except (ValueError, KeyError):
                continue
            after = len(tr.turn_ts) - bisect_right(tr.turn_ts, utc)
            reread += (r.get("in", 0) + (r.get("cached_in") or 0)) / 1e6 * after * cr_price
    would = once + reread
    saved = would - paid
    lines.append(f"  {b}went through jev{r0}   {g}{decisions:,} decisions{r0} over {tokens:,} tokens of text · {len(rows)} request(s) · {g}${paid:.4f} paid to jev{r0}"
                 + (f" · {y}hooks asked {asked}×{r0}" if asked else ""))
    lines.append(f"  {b}would have cost{r0}    {y}~${would:.2f}{r0} had the agent read that text itself: ${once:.2f} once as input (${settings.agent_price():g}/M)"
                 + (f" + ${reread:.2f} re-read on the later turns (cache, ${cr_price:g}/M)" if tr and tr.turn_ts else ""))
    lines.append(f"  {b}saved{r0}              {g}~${saved:.2f} at most{r0}{dim} — minus whatever the agent read anyway from the uncertain band{r0}"
                 + (f" · ≈ {100 * saved / total:.1f}% of this session's ~${total:,.2f}" if total > 0 else ""))
    if total > 0:
        lines.append(f"  {dim}the session's spend is mostly the conversation itself re-sent every turn ({fmt_k(sum(d['cr'] for d in tr.per.values()))} cache-read tokens); "
                     f"jev only touches what it kept out of it{r0}")
    return "\n".join(lines)


def cmd_watch(args) -> int:
    cwd = os.path.realpath(args.cwd or os.getcwd())
    tr: Transcript | None = None
    color = sys.stdout.isatty() or not args.once
    while True:
        path = find_transcript(cwd, args.transcript, args.session)
        if path and (tr is None or tr.path != path):
            tr = Transcript(path)
        if tr:
            tr.refresh()
        out = render_watch(cwd, tr, args.jev_only, color)
        if args.once:
            print(out)
            return 0
        sys.stdout.write("\033[2J\033[H" + out + f"\n\n{DIM}every {args.interval:g}s · Ctrl-C to stop{RESET}\n")
        sys.stdout.flush()
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            print()
            return 0

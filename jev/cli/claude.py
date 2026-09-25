"""Claude Code: the hooks' installer, the status line, `jev session` and `jev watch`.

The plugin wires the hooks itself (hooks/hooks.json); `jev hooks install` is for a plain CLI
install without the plugin. Everything fails open: a hook or status line that cannot reach the
API prints nothing extra and the normal flow continues.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from .. import ledger, metrics, settings
from ..errors import UsageError
from ..render import DIM, GREEN, RESET, YELLOW, fmt_k
from ._common import out_path

DEFAULT_SETTINGS = "~/.claude/settings.json"


def register(sub) -> None:
    hk = sub.add_parser("hooks", help="install | uninstall | status of the Claude Code hooks (Bash guard, WebFetch screen) for a CLI install",
                        description="The plugin wires these hooks itself; this command is for a plain CLI install. guard, on Bash before it "
                                    "runs: asks when p(destructive) >= JEV_GUARD_ASK (0.60); never allows; denies only at >= JEV_GUARD_DENY "
                                    "(0.90) and only in bypassPermissions mode or with JEV_GUARD_MODE=deny, where no prompt can appear. "
                                    "screen, on WebFetch after it returns: one line of context when p(instructions aimed at an agent) >= "
                                    "JEV_SCREEN_WARN (0.55). Both fail open and log.")
    hk.add_argument("action", nargs="?", choices=["status", "install", "uninstall"], default="status")
    hk.add_argument("--settings", help=f"settings file to edit (default {DEFAULT_SETTINGS}; .claude/settings.json for one project)")
    hk.add_argument("--no-guard", action="store_true", help="install only the WebFetch screen")
    hk.add_argument("--no-screen", action="store_true", help="install only the Bash guard")
    hk.add_argument("--tail", type=int, default=12, help="status: show the last N log lines")
    hk.set_defaults(fn=cmd_hooks)

    h = sub.add_parser("hook", help="run one hook (Claude Code calls this; the payload arrives on stdin)")
    h.add_argument("which", choices=["guard", "screen", "session-start"])
    h.set_defaults(fn=cmd_hook)

    sl = sub.add_parser("statusline", help="install | uninstall | status | preview | render the Claude Code status line (terminal CLI)",
                        description="One row under the prompt: the session's model, cost and context use (from Claude Code) next to what "
                                    "jev decided for this session. A status line configured before is kept as the first row.")
    sl.add_argument("action", nargs="?", choices=["status", "install", "uninstall", "preview", "render"], default="status")
    sl.add_argument("--settings", help=f"settings file (default {DEFAULT_SETTINGS})")
    sl.add_argument("--refresh", type=int, metavar="SECONDS", help="also re-run every N seconds while idle")
    sl.set_defaults(fn=cmd_statusline)

    se = sub.add_parser("session", help="this session: what went through jev, what that text would have cost to read, what it saved",
                        description="Three measured rows for the current Claude Code session (found by its id when the plugin's SessionStart "
                                    "hook ran, else the most recent transcript for this directory), next to the session's model spend "
                                    "estimated from the transcript at list price. `/jev:stats` shows this in the chat.")
    se.add_argument("--cwd", help="project directory (default: the current one)")
    se.add_argument("--session", help="session id (or prefix)")
    se.add_argument("--transcript", help="a specific transcript file")
    se.add_argument("--jev-only", action="store_true", help="only the jev block")
    se.add_argument("--plain", action="store_true", help="no colours (for the chat, or a file)")
    se.add_argument("--json", action="store_true")
    se.add_argument("--compact", action="store_true")
    se.set_defaults(fn=cmd_session)

    wt = sub.add_parser("watch", help="live view of `jev session` for the desktop app's Terminal panel (the app ignores status lines)",
                        description="Refreshes `jev session` in place; the transcript is read incrementally. Run it in the app's Terminal panel.")
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
        print(f"  command: {launcher()} hook guard|screen   (the plugin wires its own copy; do not install both)")
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
            hooks["PostToolUse"].append({"matcher": "WebFetch|WebSearch", "hooks": [{"type": "command", "command": f"{run} hook screen", "timeout": 15}]})
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


def cmd_hook(args) -> int:
    from ..hooks import run
    return run(args.which)


# ---------------------------------------------------------------- status line

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
    marker = ledger.touch_session(sid, cwd, st.get("transcript_path")) if sid else {"first_seen": time.time()}
    since = datetime.fromtimestamp(marker["first_seen"] - 5).isoformat(timespec="seconds")
    model = (st.get("model") or {}).get("display_name") or ""
    cost = (st.get("cost") or {}).get("total_cost_usd")
    ctx = (st.get("context_window") or {}).get("used_percentage")
    left = " · ".join(x for x in (model, f"${cost:.2f}" if isinstance(cost, (int, float)) else "", f"ctx {ctx:.0f}%" if isinstance(ctx, (int, float)) else "") if x)
    j = metrics.jev_side(cwd, sid or None, since)
    if j["requests"] or j["asked"]:
        right = (f"{GREEN}jev{RESET}: {j['decisions']:,} decisions · {fmt_k(j['tokens'])} tokens kept out · {GREEN}~${j['would']:.2f} not spent{RESET}{DIM} (ceiling){RESET}"
                 + (f" · {YELLOW}hooks asked {j['asked']}{RESET}" if j["asked"] else "") + f"{DIM} · ${j['paid']:.4f} paid{RESET}")
    else:
        right = f"{DIM}jev: nothing decided yet this session{RESET}"
    line = f"{left}  {DIM}│{RESET}  {right}" if left else right
    cols = int(os.environ.get("COLUMNS") or 0)
    if cols and len(re.sub(r"\033\[[0-9;]*m", "", line)) > cols:
        line = line.replace(f"{DIM} · ${j['paid']:.4f} paid{RESET}", "").replace(f"{DIM} (ceiling){RESET}", "")
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
        print("  the desktop app ignores status lines; there, `/jev:stats` in the chat or `jev watch` in the Terminal panel show the same numbers")
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


# ---------------------------------------------------------------- session / watch

def cmd_session(args) -> int:
    cwd = os.path.realpath(args.cwd or os.getcwd())
    sid = args.session or (os.environ.get("JEV_SESSION", "").removeprefix("session:") or None)
    tr = Path(args.transcript).expanduser() if args.transcript else None
    s = metrics.session_summary(cwd, sid, tr)
    if args.json:
        print(json.dumps(s, indent=None if args.compact else 2, ensure_ascii=False))
        return 0
    print(metrics.render_session(s, color=not args.plain and sys.stdout.isatty(), jev_only=args.jev_only))
    return 0


def cmd_watch(args) -> int:
    cwd = os.path.realpath(args.cwd or os.getcwd())
    tr: metrics.Transcript | None = None
    color = sys.stdout.isatty() or not args.once
    while True:
        path = metrics.find_transcript(cwd, args.transcript, args.session)
        if path and (tr is None or tr.path != path):
            tr = metrics.Transcript(path)
        s = metrics.session_summary(cwd, args.session, tr)
        out = metrics.render_session(s, color=color, jev_only=args.jev_only, title="jev watch")
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

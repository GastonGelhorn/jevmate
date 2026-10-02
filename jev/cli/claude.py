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
    hk.add_argument("action", nargs="?", choices=["status", "install", "uninstall", "tune"], default="status")
    hk.add_argument("--settings", help=f"settings file to edit (default {DEFAULT_SETTINGS}; .claude/settings.json for one project)")
    hk.add_argument("--no-guard", action="store_true", help="install only the WebFetch screen")
    hk.add_argument("--no-screen", action="store_true", help="install only the Bash guard")
    hk.add_argument("--tail", type=int, default=12, help="status: show the last N log lines")
    hk.set_defaults(fn=cmd_hooks)

    h = sub.add_parser("hook", help="run one hook (Claude Code calls this; the payload arrives on stdin)")
    h.add_argument("which", choices=["guard", "screen", "after-bash", "route", "stop", "session-start", "record", "delegate"])
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
                                    "estimated from the transcript at list price. `/jevmate:stats` shows this in the chat.")
    se.add_argument("--cwd", help="project directory (default: the current one)")
    se.add_argument("--session", help="session id (or prefix)")
    se.add_argument("--transcript", help="a specific transcript file")
    se.add_argument("--jev-only", action="store_true", help="only the jev block")
    se.add_argument("--plain", action="store_true", help="no colours (for the chat, or a file)")
    se.add_argument("--json", action="store_true")
    se.add_argument("--compact", action="store_true")
    se.add_argument("--spent", type=float, help="the session's cost as Claude Code totals it (/cost); the plugin's mod passes it")
    se.add_argument("--plan", action="append", metavar="WINDOW=PCT[@RESETS]",
                    help="a plan window's use, e.g. five_hour=23.5@2026-10-02T14:00:00Z (repeatable); the plugin's mod passes them on a subscription")
    se.set_defaults(fn=cmd_session)

    ins = sub.add_parser("inspect", help="read installed skills, plugins, agents and hook files for instructions aimed at an agent, and planted markers",
                         description="Two questions per instruction file somebody else wrote and your agent will obey: does it tell an agent to do "
                                     "something the person would not want (exfiltrate, run concealed commands, override the person, hide actions), and "
                                     "does it plant a phrase or link the agent must repeat. Only new or changed files are sent; the rest come from the "
                                     "cache. The plugin's SessionStart hook runs this quietly and speaks only when a new file flags.")
    ins.add_argument("paths", nargs="*", help="directories or files (default: the skills and plugin directories of Claude Code, Codex and OpenCode, plus the project's .claude)")
    ins.add_argument("--all", action="store_true", help="re-read every file, ignoring the cache")
    ins.add_argument("--bar", type=float, default=0.60, help="p at which a file is flagged (default 0.60)")
    ins.add_argument("--json", action="store_true")
    ins.add_argument("--compact", action="store_true")
    ins.set_defaults(fn=cmd_inspect)

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
    if args.action == "tune":
        return hooks_tune(args)
    if args.action == "status":
        print(f"{path}" + ("" if path.exists() else " (does not exist)"))
        print(f"  guard  (PreToolUse Bash -> ask)        {'installed' if have['PreToolUse'] else 'not installed'}")
        print(f"  screen (PostToolUse WebFetch -> note)  {'installed' if have['PostToolUse'] else 'not installed'}")
        print(f"  command: {launcher()} hook guard|screen   (the plugin wires its own copy; do not install both)")
        print("  env: JEV_GUARD_ASK=0.60 JEV_GUARD_DENY=0.90 JEV_GUARD_MODE=ask|deny|off · JEV_SCREEN_WARN=0.55 JEV_SCREEN_MODE=on|off")
        print("       JEV_TRIM_MODE=on|off JEV_TRIM_MIN=4000 (tokens) · JEV_TRIAGE_MODE=on|off · JEV_INSPECT_MODE=on|off · JEV_ROUTE_MODE=off|hint|effort · JEV_HONESTY_MODE=off|on")
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


def hooks_tune(args) -> int:
    """What the guard asked, and what the person did next. A command that ran after an `ask` was
    allowed: the ask was friction. An `ask` that nothing followed within ten minutes was declined:
    the ask was right. Enough pairs, and the ask bar for this machine is a measurement."""
    from datetime import datetime, timedelta
    from .tune import metrics, sweep
    rows = ledger.hook_rows(None)
    ran = [r for r in rows if r.get("hook") == "ran"]
    # Only asks the after-bash hook could have witnessed count: before its first row, an ask with
    # nothing following looks declined when in fact nothing was recording what ran.
    first_ran = ran[0]["ts"] if ran else "9999"
    guards = [r for r in rows if r.get("hook") == "guard" and isinstance(r.get("p"), (int, float)) and r["ts"] >= first_ran]
    last_ts = rows[-1]["ts"] if rows else ""
    pairs, allowed_after_ask, declined_after_ask, silent_ran = [], [], [], 0
    for g in guards:
        t0 = g["ts"]
        try:
            horizon = (datetime.fromisoformat(t0) + timedelta(minutes=10)).isoformat(timespec="seconds")
        except ValueError:
            continue
        followed = any(r.get("cmd") == g.get("cmd") and t0 <= r["ts"] <= horizon and (not g.get("agent") or r.get("agent") in (None, "", g.get("agent"))) for r in ran)
        if g.get("decision") in ("ask", "deny"):
            if followed:
                allowed_after_ask.append(g["p"])
                pairs.append((float(g["p"]), False))
            elif last_ts > horizon:  # the session went on and the command never ran
                declined_after_ask.append(g["p"])
                pairs.append((float(g["p"]), True))
        elif followed:
            silent_ran += 1
            pairs.append((float(g["p"]), False))
    print(f"guard decisions with a p since {first_ran[:10] if ran else '-'} (when `ran` rows began): {len(guards)} · asked: {len(allowed_after_ask) + len(declined_after_ask)} "
          f"(allowed after the ask: {len(allowed_after_ask)}, declined: {len(declined_after_ask)}) · silent and ran: {silent_ran}")
    if not ran:
        print("no `ran` rows yet: the after-bash hook (plugin 1.2+) records them; come back after a session or two")
        return 0
    if len(pairs) < 20 or not declined_after_ask:
        print(f"{len(pairs)} pairs" + (", no declines yet" if not declined_after_ask else "") + ": too few to move the bar; the current ask bar stays "
              f"{settings.option('guard_ask', 'JEV_GUARD_ASK', '0.60')}")
        if allowed_after_ask:
            print(f"  p of the commands you allowed anyway: " + " ".join(f"{p:.2f}" for p in sorted(allowed_after_ask)[-12:]))
        return 0
    best = sweep(pairs, "balanced")
    at_now = metrics(pairs, float(settings.option("guard_ask", "JEV_GUARD_ASK", "0.60")))
    print(f"ask bar now {at_now['t']:.2f}: would ask {at_now['tp'] + at_now['fp']} times, {at_now['fp']} of them on commands you allowed")
    print(f"proposed  {best['t']:.2f}: would ask {best['tp'] + best['fp']} times, {best['fp']} on allowed commands, missing {best['fn']} you declined "
          f"(balanced accuracy {best['balanced']:.0%})")
    print(f"  set it: JEV_GUARD_ASK={best['t']:.2f} in the environment, or the plugin's guard settings; .jev/guard.json `safe` patterns skip the call entirely")
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
        right = (f"{GREEN}jev{RESET}: {j['decisions']:,} decisions · {fmt_k(j['kept_out'])} tokens kept out · {GREEN}~${j['would']:.2f} not spent{RESET}{DIM} (ceiling){RESET}"
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
        print("  the desktop app ignores status lines; there, `/jevmate:stats` in the chat or `jev watch` in the Terminal panel show the same numbers")
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


def cmd_inspect(args) -> int:
    from ..inspect import inspect, roots
    from ..render import dump
    from ._common import client_for
    paths = args.paths or roots(os.getcwd())
    if not paths:
        print("nothing to inspect: no skills or plugin directories found")
        return 0
    rows, sent = inspect(client_for(args, "inspect"), paths, only_changed=not args.all, bar=args.bar)
    if args.json:
        dump(args, {"files": rows, "sent": sent})
        return 0
    flagged = [r for r in rows if r["flag"]]
    print(f"{len(rows)} instruction file(s) · {sent} read now, {len(rows) - sent} from the cache · {len(flagged)} flagged at p >= {args.bar}")
    print(f"\n{'flag':<5} {'harm':>5} {'marker':>6}  path")
    for r in rows[:40]:
        print(f"{'!' if r['flag'] else '':<5} {r['harm']:>5.2f} {r['marker']:>6.2f}  {r['path']}")
    if len(rows) > 40:
        print(f"… {len(rows) - 40} more (--json for all)")
    if flagged:
        print("\nread a flagged file before relying on it: `harm` is an instruction the person would not want obeyed, `marker` a phrase or link the agent must plant.")
    return 0


# ---------------------------------------------------------------- session / watch

def cmd_session(args) -> int:
    cwd = os.path.realpath(args.cwd or os.getcwd())
    sid = args.session or (os.environ.get("JEV_SESSION", "").removeprefix("session:") or None)
    tr = Path(args.transcript).expanduser() if args.transcript else None
    s = metrics.session_summary(cwd, sid, tr, spent=args.spent, plan=metrics.parse_plan(args.plan) or None)
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

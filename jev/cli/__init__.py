"""The command line. Each command lives in a module that is imported only when it runs."""

from __future__ import annotations

import argparse
import json
import sys
from importlib import import_module

from .. import settings
from .._version import VERSION
from ..errors import DryRun, JevError

SHORT_HELP = f"""jev {VERSION} · calibrated yes/no, pick-one and rubric decisions for coding agents (~250 ms, $0.042/Mtok)

one item
  jev yes  'Does `text` …?' -s TEXT                 P(yes); exit 0 yes, 1 no
  jev pick 'Which …?' a=desc b=desc other -s TEXT   one option, P per option, confidence
  jev rate 'How …?' "low" "mid" "high" -s TEXT      a position on your rubric, confidence
  jev ask  --noul … --choice … --score … -s TEXT    several questions, one request (--json for the raw answer)

many items
  jev rank  --query Q --candidates-file F           grade every item against a query; --top, --min, --abstain
  jev batch --input rows.jsonl --noul …             the same questions over every row -> JSONL
  jev sift  --query "what I need" src/              which files, functions or grep hits to read first
  jev tune  --labels rows.jsonl -Q '…' -Q '…'       which phrasing and threshold measure best (do this first)
  jev label -i rows -o labelled.jsonl -Q '…'        build the labelled set, uncertain rows first (--pick N for agents)
  jev scaffold NAME --out x.py                      a script with a three-way semantic `if` over a JSONL

a change, a red suite, a stream
  jev tests [--ref main] --top 5 --paths-only       which test files exercise this diff -> run those first
  jev diff  [--task '…']                            hunks by risk; hunks outside the task flagged
  jev failures -i out.txt --split pytest-long       which failures this diff caused, which look flaky
  jev cluster  -i out.txt --split pytest            group failures or log lines by root cause
  tail -f x.log | jev stream 'Is `candidate` …?'    semantic grep, batched

claude code
  jev session                   this session: what went through jev, what that would have cost to read, what it saved
  jev watch                     the same, live, for the desktop app's Terminal panel   ·   /jevmate:stats in the chat
  jev hooks install|status      a guard on Bash (asks, never allows) and an injection screen on WebFetch (plain CLI installs; the plugin wires its own)
  jev statusline install        the session's cost next to what jev decided, under the prompt (terminal CLI)
  jev inspect                   installed skills, plugins and agents read for instructions aimed at an agent (also at session start)
  jev q list|save|show          saved questions: a phrasing with its measured threshold and band (jev tune --save NAME; --q NAME anywhere)
  jev mcp                       the same decisions as MCP tools (the plugin runs it)

  jev guide [topic]   jev examples [name]   jev usage   jev cost   jev cache   jev config   jev doctor   jev auth

State: -s TEXT | --state-file F | --state-json J | --field k=v (v may be @file) | stdin.
Single-quote questions that name fields in backticks: 'Does `message` ask for a refund?'
"""

COMMANDS = {
    "ask": "ask", "yes": "ask", "pick": "ask", "rate": "ask",
    "rank": "rank", "batch": "rank",
    "tune": "tune", "label": "tune",
    "sift": "sift", "scaffold": "sift",
    "cluster": "debug", "tests": "debug", "diff": "debug", "failures": "debug", "stream": "debug",
    "auth": "admin", "doctor": "admin", "models": "admin", "config": "admin", "cost": "admin", "cache": "admin",
    "schema": "admin", "usage": "admin", "version": "admin",
    "hooks": "claude", "hook": "claude", "statusline": "claude", "watch": "claude", "session": "claude", "inspect": "claude",
    "q": "library", "mcp": "library",
    "guide": "docs", "examples": "docs", "docs": "docs",
}


def build_parser(only: str | None = None) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jev", description=SHORT_HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"jev {VERSION}")
    sub = p.add_subparsers(dest="cmd", metavar="command")
    modules = [COMMANDS[only]] if only in COMMANDS else sorted(set(COMMANDS.values()))
    for name in modules:
        import_module(f".{name}", __name__).register(sub)
    return p


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(SHORT_HELP.rstrip())
        return 0
    if argv in (["--version"], ["version"]):
        print(f"jev {VERSION}")
        return 0
    # Claude Code runs these on every tool call and every state change: no parser, no command modules.
    if len(argv) == 2 and argv[0] == "hook" and argv[1] in ("guard", "screen", "after-bash", "route", "stop", "session-start"):
        from ..hooks import run
        return run(argv[1])
    if argv == ["statusline", "render"]:
        from .claude import render_statusline
        try:
            print(render_statusline(sys.stdin.read()))
        except Exception:  # noqa: BLE001  a status line must never break the UI
            pass
        return 0
    first = next((a for a in argv if not a.startswith("-")), None)
    args = build_parser(first).parse_args(argv)
    if not args.cmd:
        print(SHORT_HELP.rstrip())
        return 0
    for attr in ("json", "compact", "verbose"):
        if not hasattr(args, attr):
            setattr(args, attr, False)
    if getattr(args, "dry_run", False):
        settings.RUNTIME.dry_run = True
    if getattr(args, "no_cache", False):
        settings.RUNTIME.cache = False
    try:
        return int(args.fn(args) or 0)
    except DryRun as e:
        print(json.dumps({"url": e.url, "body": e.body}, indent=2, ensure_ascii=False))
        return 0
    except JevError as e:
        if args.json:
            print(json.dumps({"error": str(e), "exit_code": e.exit_code}), file=sys.stderr)
        else:
            print(f"jev: {e}", file=sys.stderr)
        return e.exit_code
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0

"""`jev compact`: what a compaction would keep, cut and move to disk, before it happens.

The plugin's hooks run the same judgment before Claude Code or Codex compacts a conversation
(PreCompact) and hand its result back once the compaction is done (SessionStart). This command
shows it for any transcript, applies it for the mod, and reports on past compactions.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .. import compact, ledger, metrics, settings
from ..errors import UsageError
from ..render import BOLD, DIM, GREEN, RESET, YELLOW, fmt_k
from ._common import add_common, client_for

RULE_NAMES = {"edited-later": "edited later", "read-again": "read again", "ran-again": "ran again", "retried": "retried"}


def register(sub) -> None:
    c = sub.add_parser("compact", help="what a compaction would keep, cut and move to disk (the plugin runs it before Claude Code or Codex compacts)",
                       description="Every tool result over --min-chars is judged. Rules settle a file read again or edited later, a command "
                                   "run again and an error a retry fixed; Jev settles the rest, one result per question, against what the "
                                   "person asked. Kept whole from --keep-at, cut to its head and tail from --cut-at, moved to disk below. "
                                   "The latest --recent results stay as they are. Nothing is written without --apply.")
    c.add_argument("--transcript", help="a Claude Code transcript or a Codex rollout (the format is detected)")
    c.add_argument("--session", help="Claude Code session id or prefix")
    c.add_argument("--cwd", help="project directory (default: the current one)")
    c.add_argument("--codex", action="store_true", help="the latest Codex session for this directory")
    c.add_argument("--messages", metavar="FILE", help="Claude Code session messages as JSON ('-' for stdin), as the plugin's mod passes them")
    c.add_argument("--apply", action="store_true", help="save the results on disk and keep the block shown after the compaction")
    c.add_argument("--pruned", action="store_true", help=argparse_hidden())
    c.add_argument("--price-model", help=argparse_hidden())
    c.add_argument("--rules-only", action="store_true", help="no model call: what the rules alone settle")
    c.add_argument("--report", action="store_true", help="past compactions: what was saved, and how often a saved result was read again")
    c.add_argument("--keep-at", type=float, help=f"kept whole at or above this p (default {compact.KEEP_AT}; JEV_COMPACT_KEEP)")
    c.add_argument("--cut-at", type=float, help=f"cut to head and tail at or above this p, moved to disk below (default {compact.CUT_AT}; JEV_COMPACT_CUT)")
    c.add_argument("--recent", type=int, help=f"the latest results left as they are (default {compact.RECENT}; JEV_COMPACT_RECENT)")
    c.add_argument("--min-chars", type=int, help=f"results smaller than this are left alone (default {compact.MIN_CHARS:,}; JEV_COMPACT_MIN)")
    c.add_argument("--budget", type=int, help=f"tokens for the block after the compaction (default {compact.RESTORE_TOKENS:,}; JEV_COMPACT_BUDGET)")
    c.add_argument("--top", type=int, default=8, help="how many of the largest moved results to list (default 8)")
    add_common(c)
    c.set_defaults(fn=cmd_compact)


def argparse_hidden():
    import argparse
    return argparse.SUPPRESS


def _latest_codex(cwd: str) -> Path | None:
    """The newest Codex rollout whose session started in `cwd`."""
    root = Path.home() / ".codex" / "sessions"
    try:
        files = sorted(root.glob("*/*/*/rollout-*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)[:400]
    except OSError:
        return None
    for f in files:
        try:
            with open(f, "rb") as fh:
                meta = json.loads(fh.readline() or b"{}")
        except (OSError, ValueError):
            continue
        if os.path.realpath(str((meta.get("payload") or {}).get("cwd") or "")) == cwd:
            return f
    return None


def _find(args) -> Path:
    if args.transcript:
        p = Path(args.transcript).expanduser()
        if not p.exists():
            raise UsageError(f"no such transcript: {p}")
        return p
    cwd = os.path.realpath(args.cwd or os.getcwd())
    p = _latest_codex(cwd) if args.codex else metrics.find_transcript(cwd, None, args.session)
    if not p:
        raise UsageError("no transcript found for this directory: pass --transcript FILE, or --codex for a Codex session")
    return p


def _tokens(rows, action: str) -> int:
    return sum(r["tokens"] for r in rows if r["action"] == action and r["chars"] >= 1)


def render(plan: dict, source: str, host: str, opts: dict, applied: bool, color: bool = True, top: int = 8) -> str:
    b, dim, g, y, r0 = (BOLD, DIM, GREEN, YELLOW, RESET) if color else ("",) * 5
    s, rows = plan["stats"], [r for r in plan["decisions"] if r["chars"] >= opts["min_chars"]]
    after = {c["id"]: c for c in plan["changes"] if c["kind"] == "result"}
    cut_rows = [r for r in rows if r["action"] == "cut"]
    cut_after = sum(compact.est_tokens(len(after[r["id"]]["text"])) for r in cut_rows if r["id"] in after)
    name = Path(source).name if source not in ("messages", "") else "session messages"
    lines = [f"{b}jev compact{r0} · {name} · {host} · {plan['results']} tool results, {s['judged']} over {opts['min_chars']:,} characters (~{fmt_k(s['tokens'])} tokens)"]
    if not rows:
        lines.append(f"  {dim}nothing large enough to judge since the last compaction{r0}")
        return "\n".join(lines)
    rule = " · ".join(f"{RULE_NAMES.get(k, k)} {v}" for k, v in sorted(s["rules"].items(), key=lambda kv: -kv[1]))
    lines.append(f"  {b}kept whole{r0}      {s['kept']:>4}   ~{fmt_k(_tokens(rows, 'keep')):>6} tokens" + (f"{dim} · {s['recent']} of them among the latest{r0}" if s["recent"] else ""))
    lines.append(f"  {b}cut{r0}             {s['cut']:>4}   ~{fmt_k(_tokens(rows, 'cut')):>6} → ~{fmt_k(cut_after)} tokens{dim} · Jev was unsure: head and tail stay{r0}")
    lines.append(f"  {b}moved to disk{r0}   {s['moved']:>4}   ~{fmt_k(_tokens(rows, 'move')):>6} tokens{dim} · "
                 + (f"by rule {sum(s['rules'].values())} ({rule})" if rule else "no rule applied")
                 + f" · by Jev {sum(1 for r in rows if r['why'] == 'jev' and r['action'] == 'move')}{r0}")
    if s["inputs"]:
        lines.append(f"  {b}calls shortened{r0} {s['inputs']:>4}   {dim}files the agent wrote: the text is in the file (the mod's summary only){r0}")
    lines.append(f"  {b}freed{r0}           {g}~{fmt_k(s['freed'])} tokens{r0} of the conversation")
    k = s.get("repeated", 0)
    lines.append(f"  {b}afterwards{r0}      {dim}a block of ~{fmt_k(plan['restore_tokens'])} tokens "
                 + (f"repeats {k} result(s) Jev judged still needed and lists where the rest is{r0}" if k else f"lists where they are and repeats none{r0}"))
    unjudged = sum(1 for r in rows if r["why"] == "unjudged")
    if s["requests"]:
        lines.append(f"  {b}Jev{r0}             {s['requests']} request(s)" + (f" ({s['cached']} from cache)" if s["cached"] else "")
                     + f" · ${settings.cost_usd(s['input_tokens']):.4f}")
    if s.get("error"):
        lines.append(f"  {y}Jev could not judge, so {unjudged} result(s) stay whole{r0}{dim}: {s['error']}{r0}")
    elif unjudged:
        lines.append(f"  {y}{unjudged} result(s) kept whole without a judgment{r0}{dim}: --rules-only{r0}")
    moved = sorted((r for r in rows if r["action"] == "move"), key=lambda r: -r["tokens"])[:top]
    if moved:
        lines.append(f"\n  {dim}largest moved{r0}")
        for r in moved:
            why = RULE_NAMES.get(r["why"], f"Jev p={r['p']:.2f}" if r["p"] is not None else r["why"])
            lines.append(f"    ~{fmt_k(r['tokens']):>6}  {r['tool']} {r['input'][:90]}{dim} · {why}{r0}")
    if applied:
        lines.append(f"\n  {dim}saved in {plan['dir']}{r0}")
    else:
        lines.append(f"\n  {dim}nothing written. With compact_mode on, the plugin's hooks run this before each compaction "
                     f"(`jev config set compact on` for Codex or a plain CLI install).{r0}")
    return "\n".join(lines)


def _report(args) -> int:
    rep = compact.report()
    if args.json:
        print(json.dumps(rep, indent=None if args.compact else 2, default=str))
        return 0
    if not rep["compactions"]:
        print("jev compact: no compaction judged yet. Turn on compact_mode (`jev config set compact on` outside the plugin) and it runs before each one.")
        return 0
    rate = rep["reread_rate"]
    print(f"{BOLD}jev compact{RESET} · {rep['compactions']} compaction(s)" + (f" · {rep['errors']} failed" if rep["errors"] else ""))
    print(f"  judged {rep['judged']:,} large results: {rep['moved']:,} moved to disk, {rep['cut']:,} cut, {rep['kept']:,} kept · ~{fmt_k(rep['freed'])} tokens freed")
    if rep["pruned"]:
        print(f"  the summarizer read the pruned conversation {rep['pruned']} time(s) · ~${rep['saved_usd']:.2f} off its input")
    if rate is None:
        print(f"  {DIM}read again later: nothing was moved or cut yet{RESET}")
    else:
        advice = ("more than one in seven: raise JEV_COMPACT_KEEP (or --keep-at) so more stays whole" if rate > 0.15
                  else "almost never: the bars could move more out (lower JEV_COMPACT_KEEP)" if rate < 0.02 and rep["moved"] >= 50
                  else "the bars look right")
        print(f"  read again later: {rep['rereads']} of {rep['moved'] + rep['cut']:,} ({100 * rate:.1f}%) · {advice}")
    return 0


def cmd_compact(args) -> int:
    if args.report:
        return _report(args)
    opts = compact.options()
    for key in ("keep_at", "cut_at", "recent", "min_chars", "budget"):
        v = getattr(args, key, None)
        if v is not None:
            opts[key] = v
    if not 0.0 <= opts["cut_at"] <= opts["keep_at"] <= 1.0:
        raise UsageError(f"--cut-at {opts['cut_at']} and --keep-at {opts['keep_at']}: expected 0 <= cut-at <= keep-at <= 1")
    by_mod = bool(args.messages)
    if by_mod:
        raw = sys.stdin.read() if args.messages == "-" else Path(args.messages).expanduser().read_text()
        try:
            data = json.loads(raw or "[]")
        except ValueError as e:
            raise UsageError(f"--messages: not JSON ({e})") from e
        events, host, source = compact.from_messages(data.get("messages") if isinstance(data, dict) else data), "claude", "messages"
    else:
        path = _find(args)
        events, host = compact.load(path)
        source = str(path)
    tag = ledger.agent_tag() or ledger.session_tag(args.session) or "session:none"
    client, why = None, None
    if not args.rules_only:
        try:
            client = client_for(args, "hook:compact" if by_mod else "compact")
        except Exception as e:  # noqa: BLE001  no key: the rules still decide, and the report says why Jev did not
            why = f"{type(e).__name__}: {str(e)[:120]}"
    plan = compact.build(events, client=client, client_error=why, apply=args.apply, tag=tag, **opts)
    if args.apply:
        compact.save_state(tag, plan, None if by_mod else source, by="mod" if by_mod else "cli")
        row = {"host": host, "by": "mod" if by_mod else "cli", **compact.log_fields(plan)}
        if args.pruned:
            price_in = metrics.price_for(args.price_model or "")[0]
            row.update(pruned=True, saved_usd=round(plan["stats"]["freed"] * price_in / 1e6, 6))
        ledger.log_hook("compact", row)
    if args.json:
        print(json.dumps(plan, indent=None if args.compact else 2, ensure_ascii=False))
        return 0
    print(render(plan, source, host, opts, args.apply, color=sys.stdout.isatty(), top=args.top))
    return 0

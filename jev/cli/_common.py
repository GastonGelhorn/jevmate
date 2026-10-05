"""Argument groups and parsers the commands share."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import settings
from ..errors import UsageError
from ..questions import choice, noul, parse_kv, parse_value, score
from ..render import eprint
from ..textio import implicit_stdin

RAW = argparse.RawDescriptionHelpFormatter


class QAction(argparse.Action):
    """Collects --noul / --choice / --score in the order given."""

    def __call__(self, parser, ns, values, option_string=None):
        lst = getattr(ns, "qlist", None) or []
        lst.append((self.dest, list(values)))
        ns.qlist = lst


def add_state_args(p) -> None:
    g = p.add_argument_group("state")
    g.add_argument("-s", "--state", help="text to judge; @path reads a file; '-' reads stdin")
    g.add_argument("--state-file", help="file to judge (.json is parsed, anything else is text)")
    g.add_argument("--state-json", help="inline JSON state")
    g.add_argument("--field", action="append", metavar="KEY=VALUE",
                   help="build an object state field by field; VALUE may be @file or JSON; repeatable; --state joins it as `text`")


def add_question_args(p) -> None:
    g = p.add_argument_group("questions (repeatable, any mix)")
    g.add_argument("--noul", dest="noul", action=QAction, nargs="+", metavar="ARG", help="ID INSTRUCTIONS [true=…] [false=…]")
    g.add_argument("--choice", dest="choice", action=QAction, nargs="+", metavar="ARG", help="ID INSTRUCTIONS OPTION[=desc] OPTION[=desc] …")
    g.add_argument("--score", dest="score", action=QAction, nargs="+", metavar="ARG", help="ID INSTRUCTIONS LEVEL LEVEL … (low to high, 2 to 10)")
    g.add_argument("--questions-file", "-q", help="JSON file of {id: question}")
    g.add_argument("--questions-json", help="inline JSON of {id: question}")


def add_common(p) -> None:
    p.add_argument("--model", default=None, help=f"model id or alias (default {settings.default_model()}); pin one in a tuned pipeline")
    p.add_argument("--api-key", default=None, help="override key resolution")
    p.add_argument("--timeout", type=float, default=None, help="seconds per request (default 30; 300 on a local backend)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--compact", action="store_true", help="single-line JSON")
    p.add_argument("--dry-run", action="store_true", help="print the request that would be sent, and send nothing")
    p.add_argument("--no-cache", action="store_true", help="bypass the local answer cache for this run")
    p.add_argument("-v", "--verbose", action="store_true")


def add_grading_args(p, chunk_size: int = 250, chunk_chars: int = 90_000, concurrency: int = 6) -> None:
    p.add_argument("--chunk-size", type=int, default=chunk_size, help=f"max items per request (default {chunk_size})")
    p.add_argument("--chunk-chars", type=int, default=chunk_chars, help=f"max item characters per request (default {chunk_chars:,})")
    p.add_argument("--concurrency", type=int, default=concurrency, help=f"parallel requests (default {concurrency})")


def add_diff_args(p) -> None:
    g = p.add_argument_group("diff source (default: git diff HEAD)")
    g.add_argument("--repo", default=".", help="repository root (default .)")
    g.add_argument("--diff", help="a diff file, or '-' for stdin, instead of running git")
    g.add_argument("--staged", action="store_true", help="git diff --staged")
    g.add_argument("--ref", help="git diff REF (main, HEAD~3, …)")


def build_state(args):
    given = [n for n in ("state", "state_file", "state_json") if getattr(args, n, None)]
    if len(given) > 1:
        raise UsageError("give the state one way: --state, --state-file or --state-json (--field may be added to any of them)")
    state = None
    if getattr(args, "state", None):
        s = args.state
        state = sys.stdin.read() if s == "-" else parse_value(s) if s.startswith("@") else s
    elif getattr(args, "state_file", None):
        state = parse_value("@" + args.state_file)
    elif getattr(args, "state_json", None):
        try:
            state = json.loads(args.state_json)
        except ValueError as e:
            raise UsageError(f"--state-json: invalid JSON: {e}")
    if getattr(args, "field", None):
        obj = {} if state is None else {"text": state}
        for kv in args.field:
            if "=" not in kv:
                raise UsageError(f"--field expects KEY=VALUE, got {kv!r}")
            k, v = kv.split("=", 1)
            obj[k.strip()] = parse_value(v)
        state = obj
    if state is None:
        state = implicit_stdin()
        if not state:
            raise UsageError("no state: use --state TEXT, --state-file PATH, --state-json JSON, --field k=v, or pipe text on stdin")
    n = len(state) if isinstance(state, str) else len(json.dumps(state, ensure_ascii=False))
    if n > settings.STATE_CHAR_CEILING:
        eprint(f"jev: warning: the state is {n:,} characters; the API stops accepting at about {settings.STATE_CHAR_CEILING:,} "
               f"(~{settings.STATE_CHAR_CEILING // 3:,} tokens). Cut it, or use `jev rank` / `jev batch`, which chunk.")
    return state


def questions_from_args(args) -> dict:
    qs: dict = {}
    if getattr(args, "questions_file", None):
        v = parse_value("@" + args.questions_file)
        if not isinstance(v, dict):
            raise UsageError("--questions-file must hold a JSON object of id -> question")
        qs.update(v)
    if getattr(args, "questions_json", None):
        try:
            qs.update(json.loads(args.questions_json))
        except ValueError as e:
            raise UsageError(f"--questions-json: invalid JSON: {e}")
    for kind, vals in getattr(args, "qlist", None) or []:
        if len(vals) < 2:
            raise UsageError(f"--{kind} needs ID INSTRUCTIONS [...]")
        qid, instr, rest = vals[0], parse_value(vals[1]), vals[2:]
        if kind == "noul":
            kv = parse_kv(rest)
            extra = set(kv) - {"true", "false"}
            if extra:
                raise UsageError(f"--noul {qid}: only true=… and false=… may follow the instructions, not {sorted(extra)}")
            qs[qid] = noul(instr, kv.get("true"), kv.get("false"))
        elif kind == "choice":
            if len(rest) < 2:
                raise UsageError(f"--choice {qid}: give at least two options as NAME or NAME=description")
            qs[qid] = choice(instr, parse_kv(rest))
        else:
            if len(rest) < 2:
                raise UsageError(f"--score {qid}: give 2 to {settings.MAX_SCORE_LEVELS} level descriptions, low to high")
            qs[qid] = score(instr, [parse_value(r) for r in rest])
    if not qs:
        raise UsageError("no questions: use --noul / --choice / --score, --questions-file or --questions-json")
    return qs


def read_candidates(args) -> list:
    if getattr(args, "candidates_file", None):
        v = parse_value("@" + args.candidates_file)
        return v if isinstance(v, list) else [ln for ln in str(v).splitlines() if ln.strip()]
    if getattr(args, "candidates", None):
        return list(args.candidates)
    txt = implicit_stdin()
    if txt:
        return [ln.rstrip("\n") for ln in txt.splitlines() if ln.strip()]
    raise UsageError("no candidates: pass them as arguments, with --candidates-file (lines or a JSON array), or on stdin")


def add_saved_arg(p) -> None:
    p.add_argument("--q", dest="saved", metavar="NAME", help="use a saved question (jev q list): its phrasing, threshold, band and pinned model fill in what you do not pass")


def use_saved(args, question_attr: str = "instructions"):
    """Apply --q NAME to the arguments; returns the spec, or None."""
    name = getattr(args, "saved", None)
    if not name:
        return None
    from ..library import apply, load
    spec = load(name)
    apply(spec, args, question_attr=question_attr)
    return spec


def client_for(args, label: str, record: bool = True):
    from ..client import Client
    from .. import settings
    timeout = getattr(args, "timeout", None) or (settings.LOCAL_TIMEOUT if settings.is_local(settings.base_url()) else 30.0)
    return Client(getattr(args, "api_key", None), getattr(args, "model", None), timeout=timeout, record=record, label=label)


def out_path(p: str) -> Path:
    return Path(p).expanduser()

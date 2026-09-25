"""sift and scaffold: decide what to read before reading it; put a semantic `if` in a script."""

from __future__ import annotations

import os
import time
from pathlib import Path

from .. import settings
from ..analysis import sift, sift_candidates
from ..decide import SCAFFOLD
from ..errors import UsageError
from ..questions import parse_value
from ..render import dump, eprint, footer
from ..textio import est_tokens, implicit_stdin, read_source
from ._common import RAW, add_common, add_grading_args, client_for, out_path
from .docs import example


def register(sub) -> None:
    sf = sub.add_parser("sift", help="which files, functions or grep hits are worth reading, before you read them", formatter_class=RAW, epilog=example("sift"),
                        description="Grades an excerpt of every candidate (a file's head, a function, or a file's matching grep lines) "
                                    "against what you are looking for and ranks them with the tokens each would cost to read. "
                                    "--budget-tokens marks where reading down the list would exceed the budget. A filter, not a judge.")
    sf.add_argument("--query", required=True, help="what you are looking for, in words")
    sf.add_argument("paths", nargs="*", help="files or directories (.git, node_modules, vendor … are skipped)")
    sf.add_argument("--files-from", help="one path per line, or '-' for stdin (rg -l … | jev sift --files-from - …)")
    sf.add_argument("--grep", action="store_true", help="the lines are grep hits (path:line:text); a candidate is a file with its matching lines")
    sf.add_argument("--functions", action="store_true", help="split files at def / class / function lines and grade each chunk")
    sf.add_argument("--instructions", help="a custom question naming `candidate` and `query`")
    sf.add_argument("--context", help="shared extra state (text, JSON, or @file), available as `context`")
    sf.add_argument("--head-chars", type=int, default=4000, help="excerpt per candidate the model sees (default 4000)")
    sf.add_argument("--budget-tokens", type=int, help="mark where reading the ranked list would exceed this many tokens")
    sf.add_argument("--top", type=int, help="keep the best N")
    sf.add_argument("--min", type=float, help="keep p >= MIN")
    add_grading_args(sf)
    add_common(sf)
    sf.set_defaults(fn=cmd_sift)

    sc = sub.add_parser("scaffold", help="write a script with a three-way semantic `if` over a JSONL of rows",
                        description="Prints (or --out writes) a Python script that runs one yes/no over every row with decide_many() and "
                                    "files them into yes / no / review, the review file being the rows jev could not decide. "
                                    "Edit QUESTION, THRESHOLD and BAND (from `jev tune`) and SRC, or point it at a saved question.")
    sc.add_argument("name", nargs="?", help="script name, also its label in `jev usage` (default decide_rows)")
    sc.add_argument("--out", help="write here instead of stdout (mode 755)")
    sc.add_argument("--force", action="store_true")
    sc.add_argument("--libpath", action="store_true", help="print the directory to put on sys.path for `import jev`")
    sc.set_defaults(fn=cmd_scaffold)


def cmd_sift(args) -> int:
    lines: list[str] = []
    if args.files_from:
        lines = read_source(args.files_from, "file list").splitlines()
    elif not args.paths:
        lines = (implicit_stdin() or "").splitlines()
    lines = [ln.rstrip("\n") for ln in lines if ln.strip()]
    if not lines and not args.paths:
        raise UsageError("nothing to sift: give paths, --files-from FILE|-, or pipe `rg -l …` (or `rg -n …` with --grep) on stdin")
    cands, meta = sift_candidates(args.paths, lines, grep=args.grep, functions=args.functions, head_chars=args.head_chars)
    if len(cands) > 2000:
        eprint(f"jev: sift: {len(cands)} candidates; each costs about {est_tokens(args.head_chars):,} tokens to grade, consider narrowing")
    c = client_for(args, "sift")
    t0 = time.monotonic()
    rows, cut_at, u = sift(c, parse_value(args.query), cands, meta, instructions=args.instructions,
                           context=parse_value(args.context) if args.context else None, top=args.top, min_p=args.min, budget_tokens=args.budget_tokens,
                           chunk_chars=args.chunk_chars, chunk_size=args.chunk_size, concurrency=args.concurrency)
    ms = (time.monotonic() - t0) * 1000
    if args.json:
        dump(args, {"results": rows, "n": len(cands), "budget_tokens": args.budget_tokens, "cut_at": cut_at, **u.as_dict(), "ms": round(ms)})
        return 0
    for k, r in enumerate(rows):
        if cut_at is not None and k == cut_at:
            print(f"--- budget of {args.budget_tokens:,} tokens reached after {k} item(s); {len(rows) - k} more below ---")
        print(f"{r['p']:.3f}  {r['tokens']:>7,}  {r['display']}")
    within = rows[:cut_at] if cut_at is not None else rows
    eprint(footer(f"{len(cands)} candidate(s)", u.requests, u.cached, u.input_tokens, ms,
                  f"reading the top {len(within)} costs ~{within[-1]['cumulative_tokens']:,} tokens" if within else ""))
    return 0


def libpath() -> Path:
    here = Path(__file__).resolve().parent.parent.parent  # the directory that holds the `jev` package
    return settings.LIB_DIR if (settings.LIB_DIR / "jev" / "__init__.py").exists() else here


def cmd_scaffold(args) -> int:
    if args.libpath:
        print(libpath())
        return 0
    src = SCAFFOLD.replace("__LIB__", str(libpath())).replace("__NAME__", args.name or "decide_rows")
    if not args.out:
        print(src, end="")
        return 0
    out = out_path(args.out)
    if out.exists() and not args.force:
        raise UsageError(f"{out} exists; --force to overwrite")
    out.write_text(src)
    os.chmod(out, 0o755)
    print(f"wrote {out}  (edit QUESTION, THRESHOLD/BAND and SRC; run `jev tune` first)")
    return 0

"""rank and batch: one judgment over many items."""

from __future__ import annotations

import json
import sys
import time

from ..errors import JevError, UsageError
from ..grading import grade, in_band, parse_band, write_uncertain
from ..questions import parse_value
from ..render import dump, eprint, footer, truncate
from ..textio import read_source
from ._common import RAW, add_common, add_grading_args, add_question_args, add_saved_arg, client_for, out_path, questions_from_args, read_candidates, use_saved
from .docs import example


def register(sub) -> None:
    rk = sub.add_parser("rank", help="grade many candidates against a query; rank, filter, hand off the uncertain band", formatter_class=RAW, epilog=example("rank"),
                        description="Every candidate is graded against --query with its own question (the item embedded, never indexed), "
                                    "packed into as few requests as the budget allows. Yes/no by default; with --levels a rubric whose "
                                    "score is normalised to 0..1 as p.")
    rk.add_argument("--query", required=True, help="what you are looking for: text, JSON, or @file")
    rk.add_argument("--candidates-file", help="one candidate per line, or a .json array")
    rk.add_argument("candidates", nargs="*", help="candidates as arguments (or on stdin)")
    rk.add_argument("--instructions", help="the question, naming `candidate` and `query`: 'Does `candidate` answer `query`?'")
    add_saved_arg(rk)
    rk.add_argument("--levels", nargs="+", metavar="LEVEL", help="a score rubric instead of yes/no")
    rk.add_argument("--context", help="shared extra state (text, JSON, or @file), available as `context`")
    rk.add_argument("--top", type=int, help="keep the best N")
    rk.add_argument("--min", type=float, help="keep p >= MIN")
    rk.add_argument("--abstain", nargs=2, type=float, metavar=("LO", "HI"), help="pull LO <= p <= HI out as uncertain, for a reader (`jev tune` suggests the band)")
    rk.add_argument("--uncertain-out", help="write the uncertain candidates here")
    rk.add_argument("--width", type=int, default=110, help="display width per candidate")
    add_grading_args(rk)
    add_common(rk)
    rk.set_defaults(fn=cmd_rank)

    b = sub.add_parser("batch", help="the same questions over many rows -> JSONL", formatter_class=RAW, epilog=example("batch"),
                       description="One request per row, in parallel. Rows are JSONL (the object is the state, or --state-key picks a "
                                   "field) or plain lines (--text-lines). Output is JSONL with the answers per row.")
    b.add_argument("--input", "-i", required=True, help="JSONL or text file; '-' for stdin")
    b.add_argument("--state-key", help="field of each row to use as the state")
    b.add_argument("--id-key", default="id", help="field echoed back as `id` (default id)")
    b.add_argument("--text-lines", action="store_true", help="every line is a plain-text state")
    b.add_argument("--echo", action="store_true", help="include the state in each output row")
    b.add_argument("--out", "-o", help="write JSONL here instead of stdout")
    b.add_argument("--resume", action="store_true", help="with --out: skip rows already answered in that file and append the rest")
    b.add_argument("--concurrency", type=int, default=8)
    b.add_argument("--abstain", nargs=2, type=float, metavar=("LO", "HI"), help="mark rows whose yes/no lands in [LO, HI] as uncertain")
    b.add_argument("--uncertain-out", help="also write the uncertain rows here")
    add_saved_arg(b)
    add_question_args(b)
    add_common(b)
    b.set_defaults(fn=cmd_batch)


def cmd_rank(args) -> int:
    use_saved(args, "instructions")
    cands = read_candidates(args)
    if not cands:
        raise UsageError("no candidates")
    levels = [parse_value(lv) for lv in args.levels] if args.levels else None
    question = args.instructions or ("How well does `candidate` match `query`?" if levels else "Is `candidate` relevant to `query`?")
    if "{i}" in question or "candidates[" in question:
        eprint("jev: warning: name the item `candidate`; each question carries its own item. Positional references mis-locate past ~20 items.")
    band = parse_band(args.abstain)
    c = client_for(args, "rank")
    t0 = time.monotonic()
    g = grade(c, cands, question, parse_value(args.query), context=parse_value(args.context) if args.context else None, levels=levels,
              chunk_chars=args.chunk_chars, chunk_size=args.chunk_size, concurrency=args.concurrency)
    results = sorted(g.results, key=lambda r: -r["p"])
    uncertain = []
    if band:  # the band comes out before --min/--top: an item jev cannot decide is neither kept nor dropped
        uncertain = [r for r in results if in_band(r["p"], band)]
        results = [r for r in results if not in_band(r["p"], band)]
    if args.min is not None:
        results = [r for r in results if r["p"] >= args.min]
    if args.top:
        results = results[: args.top]
    ms = (time.monotonic() - t0) * 1000
    if args.uncertain_out:
        write_uncertain(args.uncertain_out, [r["candidate"] for r in uncertain])
    if args.json:
        out = {"results": results, "n": len(cands), "requests": g.requests, "cached_requests": g.cached,
               "usage": {"input_tokens": g.input_tokens, "output_tokens": g.output_tokens}, "ms": round(ms)}
        if band:
            out.update(abstain=list(band), uncertain=uncertain)
        dump(args, out)
        return 0

    def line(r):
        cand = r["candidate"] if isinstance(r["candidate"], str) else json.dumps(r["candidate"], ensure_ascii=False)
        return f"{r['p']:.3f}  #{r['i']:<4} {truncate(cand, args.width)}"

    for r in results:
        print(line(r))
    if band:
        print(f"--- uncertain: {len(uncertain)} with p in [{band[0]}, {band[1]}] -> decide by hand"
              + (f" (written to {args.uncertain_out})" if args.uncertain_out else ""))
        for r in uncertain:
            print(line(r))
    eprint(footer(f"{len(cands)} candidates", g.requests, g.cached, g.input_tokens, ms))
    return 0


def _rows(raw: str, args) -> list[tuple[int, object, object]]:
    items = []
    for n, line in enumerate(raw.splitlines()):
        if not line.strip():
            continue
        if args.text_lines:
            items.append((n, None, line))
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            items.append((n, None, line))
            continue
        if isinstance(obj, dict):
            if args.state_key:
                if args.state_key not in obj:
                    raise UsageError(f"line {n}: no key {args.state_key!r}")
                items.append((n, obj.get(args.id_key), obj[args.state_key]))
            else:
                items.append((n, obj.get(args.id_key), obj))
        else:
            items.append((n, None, obj))
    return items


def cmd_batch(args) -> int:
    spec = use_saved(args, "_saved_question")
    if spec:
        from ..questions import noul
        qs = {spec["name"]: noul(spec["question"], spec.get("true"), spec.get("false"))}
        try:
            qs.update(questions_from_args(args))
        except UsageError:
            pass
    else:
        qs = questions_from_args(args)
    items = _rows(read_source(args.input, "input"), args)
    if not items:
        raise UsageError("no input rows")
    c = client_for(args, "batch")
    band = parse_band(args.abstain)
    done: set[int] = set()
    if args.resume:
        if not args.out:
            raise UsageError("--resume needs --out FILE, the file it resumes")
        try:
            for ln in out_path(args.out).read_text().splitlines():
                try:
                    row = json.loads(ln)
                    if "error" not in row:
                        done.add(int(row["line"]))
                except (ValueError, KeyError, TypeError):
                    continue
        except FileNotFoundError:
            pass
        items = [it for it in items if it[0] not in done]
        if not items:
            eprint(f"· nothing left to do: {len(done)} rows already in {args.out}")
            return 0
    out_f = open(out_path(args.out), "a" if args.resume else "w") if args.out else sys.stdout
    unc_f = open(out_path(args.uncertain_out), "w") if args.uncertain_out else None
    tokens = errors = uncertain = cached = 0
    t0 = time.monotonic()

    def run(item):
        n, ident, state = item
        try:
            resp = c.ask(state, qs)
            row = {"line": n, "answers": resp["answers"], "usage": resp["usage"]}
            if resp.get("cached"):
                row["cached"] = True
            if band:  # choice and score carry `confidence` instead; pick/rate gate on that
                row["uncertain"] = any(in_band(a["noul"], band) for a in resp["answers"].values() if "noul" in a)
        except JevError as e:
            row = {"line": n, "error": str(e)}
        if ident is not None:
            row["id"] = ident
        if args.echo:
            row["state"] = state
        return row

    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max(1, min(args.concurrency, len(items)))) as ex:
            for row in ex.map(run, items):
                if "error" in row:
                    errors += 1
                else:
                    tokens += row["usage"].get("input_tokens", 0)
                    cached += bool(row.get("cached"))
                    if row.get("uncertain"):
                        uncertain += 1
                        if unc_f:
                            unc_f.write(json.dumps(row, ensure_ascii=False) + "\n")
                out_f.write(json.dumps(row, ensure_ascii=False) + "\n")
                out_f.flush()
    finally:
        if args.out:
            out_f.close()
        if unc_f:
            unc_f.close()
    ms = (time.monotonic() - t0) * 1000
    eprint(footer(f"{len(items)} rows · {errors} errors" + (f" · {uncertain} uncertain" if band else "") + (f" · {len(done)} skipped (done before)" if done else ""),
                  len(items), cached, tokens, ms, f"wrote {args.out}" if args.out else "", f"uncertain -> {args.uncertain_out}" if unc_f else ""))
    return 0 if errors == 0 else 4

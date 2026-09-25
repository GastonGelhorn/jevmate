"""cluster, tests, diff, failures, stream: a change, a red suite, a stream of lines."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from ..analysis import RISK_NAMES, cluster, rank_tests, rate_hunks, sort_failures, test_candidates
from ..errors import UsageError
from ..grading import grade, in_band, parse_band
from ..render import dump, eprint, footer, truncate
from ..settings import cost_usd
from ..textio import SPLIT_PRESETS, compact_diff, first_line, git_diff, read_source, split_hunks, split_items
from ._common import RAW, add_common, add_diff_args, add_saved_arg, client_for, out_path, use_saved
from .docs import example


def register(sub) -> None:
    splits = " | ".join(SPLIT_PRESETS)

    cl = sub.add_parser("cluster", help="group failures, log lines or findings by root cause (k clusters cost k requests)", formatter_class=RAW, epilog=example("cluster"),
                        description="Greedy clustering against representatives: the first unassigned item represents a cluster, every "
                                    "remaining item is graded against it in one request, those at or above --threshold join, and the next "
                                    "unassigned item starts the next cluster. Input: one item per line (JSONL with --text-key), or a "
                                    "runner's output cut with --split.")
    cl.add_argument("--input", "-i", help="file, or '-' / omitted for stdin")
    cl.add_argument("--split", help=f"how to cut the input into items: {splits} | a regex matching each item's first line (default line)")
    cl.add_argument("--text-key", default="text")
    cl.add_argument("--threshold", type=float, default=0.70, help="p at which an item joins a cluster (default 0.70)")
    cl.add_argument("--abstain", nargs=2, type=float, metavar=("LO", "HI"), help="do not assign on a p inside the band; report the near miss")
    cl.add_argument("--rep", choices=["first", "longest"], default="first", help="which unassigned item represents the next cluster")
    cl.add_argument("--instructions", help="a custom question naming `candidate` and `query`")
    cl.add_argument("--head-chars", type=int, default=1500, help="characters of each item the model sees (default 1500)")
    cl.add_argument("--width", type=int, default=100)
    cl.add_argument("--concurrency", type=int, default=6)
    add_common(cl)
    cl.set_defaults(fn=cmd_cluster)

    ts = sub.add_parser("tests", help="which test files exercise this diff: run those first", formatter_class=RAW, epilog=example("tests"),
                        description="The diff is the shared state; every test file found by name under --tests (default: the repo) is a "
                                    "candidate described by its head and its test names. Name matches (Outbox.php ~ OutboxTest.php) and "
                                    "test files the diff itself changed are flagged: that is free and nearly certain. --paths-only feeds xargs.")
    add_diff_args(ts)
    ts.add_argument("--tests", nargs="*", help="directories or files to look for tests in (default: the repo, by name patterns)")
    ts.add_argument("--tests-from", help="an explicit test file list, or '-' for stdin")
    ts.add_argument("--instructions", help="a custom question naming `candidate` and `query`")
    ts.add_argument("--diff-chars", type=int, default=50_000, help="cap on the diff sent as state (default 50,000)")
    ts.add_argument("--head-chars", type=int, default=1200, help="characters of each test file's head the model sees")
    ts.add_argument("--top", type=int)
    ts.add_argument("--min", type=float, help="keep p >= MIN (name matches and changed test files always stay)")
    ts.add_argument("--boost-matches", action="store_true", help="list name matches and changed test files first, whatever their p")
    ts.add_argument("--paths-only", action="store_true", help="print only paths (for xargs or a runner)")
    ts.add_argument("--concurrency", type=int, default=6)
    add_common(ts)
    ts.set_defaults(fn=cmd_tests)

    df = sub.add_parser("diff", help="every hunk rated by risk (and, with --task, by scope): review where being wrong costs most", formatter_class=RAW, epilog=example("diff"),
                        description=f"Each hunk is rated on a four-level rubric ({' / '.join(RISK_NAMES)}). With --task a second question "
                                    "asks whether the hunk belongs to the task at all; hunks below --scope-bar are flagged.")
    add_diff_args(df)
    df.add_argument("--task", help="what the change was supposed to do; hunks unlikely to belong to it get flagged")
    df.add_argument("--scope-bar", type=float, default=0.40, help="p(in scope) below which a hunk is flagged (default 0.40)")
    df.add_argument("--min-level", choices=RISK_NAMES, help="show only this level and above (flagged hunks always show)")
    df.add_argument("--top", type=int)
    df.add_argument("--head-chars", type=int, default=3000, help="characters of each hunk the model sees")
    df.add_argument("--width", type=int, default=110)
    df.add_argument("--concurrency", type=int, default=6)
    add_common(df)
    df.set_defaults(fn=cmd_diff)

    fl = sub.add_parser("failures", help="which failures this diff caused, and which look flaky", formatter_class=RAW, epilog=example("failures"),
                        description="Two questions per failure against the diff, two requests in total: was it caused by this change, and "
                                    "does it look deterministic, environmental or flaky. Failures come from a file or stdin, cut with --split.")
    fl.add_argument("--failures", "-i", help="runner output or failures file; '-' / omitted reads stdin")
    fl.add_argument("--split", help=f"how to cut the failures: {splits} | a regex (default line)")
    fl.add_argument("--text-key", default="text")
    add_diff_args(fl)
    fl.add_argument("--mine-bar", type=float, default=0.60, help="p(caused by this diff) at which a failure is called yours (default 0.60)")
    fl.add_argument("--diff-chars", type=int, default=50_000)
    fl.add_argument("--head-chars", type=int, default=1500)
    fl.add_argument("--width", type=int, default=90)
    fl.add_argument("--concurrency", type=int, default=6)
    add_common(fl)
    fl.set_defaults(fn=cmd_failures)

    st = sub.add_parser("stream", help="semantic grep over stdin or tail -f: one yes/no per line, batched, printed as decided", formatter_class=RAW, epilog=example("stream"),
                        description="Reads lines from stdin, batches them (--batch lines or --every seconds, whichever comes first) into one "
                                    "request each, and prints `p yes|no|? line`. --only yes makes it a filter; --abstain marks the band `?`.")
    st.add_argument("question", nargs="?", help="a yes/no question naming the line as `candidate` (or --q NAME)")
    add_saved_arg(st)
    st.add_argument("--threshold", type=float, default=0.5)
    st.add_argument("--abstain", nargs=2, type=float, metavar=("LO", "HI"))
    st.add_argument("--uncertain-out", help="append the band's lines here as JSONL")
    st.add_argument("--only", choices=["yes", "no"], help="print only these")
    st.add_argument("--plain", action="store_true", help="print the line alone, for piping")
    st.add_argument("--query", help="shared `query` field, if the question names it")
    st.add_argument("--batch", type=int, default=50, help="lines per request (default 50)")
    st.add_argument("--every", type=float, default=1.0, help="seconds to wait for a batch to fill (default 1.0)")
    add_common(st)
    st.set_defaults(fn=cmd_stream)


def _diff_state(args) -> tuple[str, str, list[str]]:
    raw, changed = git_diff(args.repo, args.staged, args.ref, args.diff)
    if not raw.strip():
        raise UsageError("empty diff: nothing changed against HEAD; pass --staged, --ref REV or --diff FILE")
    return raw, compact_diff(raw, getattr(args, "diff_chars", 50_000)), changed


def cmd_cluster(args) -> int:
    items = split_items(read_source(args.input, "input"), args.split, args.text_key)
    if len(items) < 2:
        raise UsageError(f"{len(items)} item(s) after splitting with {args.split or 'line'!r}; nothing to cluster (presets: {', '.join(SPLIT_PRESETS)}, or a regex)")
    c = client_for(args, "cluster")
    t0 = time.monotonic()
    clusters, notes, u = cluster(c, items, threshold=args.threshold, band=parse_band(args.abstain), rep=args.rep, head_chars=args.head_chars,
                                 instructions=args.instructions, concurrency=args.concurrency)
    ms = (time.monotonic() - t0) * 1000
    index = {cl["rep"]: k for k, cl in enumerate(clusters, 1)}
    singles = sum(1 for cl in clusters if len(cl["members"]) == 1)
    if args.json:
        dump(args, {"n": len(items), "threshold": args.threshold,
                    "clusters": [{"id": k, "size": len(cl["members"]), "representative": cl["rep"],
                                  "members": [{"i": j, "p": round(p, 3), "text": items[j]} for j, p in cl["members"]]} for k, cl in enumerate(clusters, 1)],
                    "uncertain": [{"i": j, "cluster": index[r], "p": round(p, 3)} for j, r, p in notes], **u.as_dict(), "ms": round(ms)})
        return 0
    print(f"{len(clusters)} cluster(s) from {len(items)} items · {singles} singleton(s) · threshold {args.threshold}")
    for k, cl in enumerate(clusters, 1):
        print(f"\n── cluster {k} ({len(cl['members'])}) " + "─" * max(0, 60 - len(str(k)) - len(str(len(cl["members"])))))
        for j, p in cl["members"]:
            print(f"  {' rep' if j == cl['rep'] else f'{p:.2f}':>4}  #{j:<4} {first_line(items[j], args.width)}")
    if notes:
        print("\nuncertain (singletons that nearly joined a cluster):")
        for j, r, p in notes:
            print(f"  #{j} ~ cluster {index[r]}  p={p:.2f}  {first_line(items[j], args.width - 20)}")
    eprint(footer(f"{len(items)} items", u.requests, u.cached, u.input_tokens, ms))
    return 0


def cmd_tests(args) -> int:
    raw, diff_txt, changed = _diff_state(args)
    extra = [x.strip() for x in read_source(args.tests_from, "test list").splitlines()] if args.tests_from else []
    cands, meta = test_candidates(args.repo, changed, args.tests, extra, head_chars=args.head_chars)
    c = client_for(args, "tests")
    t0 = time.monotonic()
    results, u = rank_tests(c, diff_txt, cands, meta, instructions=args.instructions, min_p=args.min, top=args.top, boost=args.boost_matches,
                            concurrency=args.concurrency)
    ms = (time.monotonic() - t0) * 1000
    if args.paths_only:
        for r in results:
            print(r["path"])
        return 0
    if args.json:
        dump(args, {"changed": changed, "results": results, "n_test_files": len(cands), **u.as_dict(), "ms": round(ms)})
        return 0
    print(f"{len(changed)} changed file(s) · {len(cands)} test file(s) graded")
    for r in results:
        flags = ("changed " if r["changed"] else "") + ("name-match" if r["name_match"] else "")
        print(f"{r['p']:.3f}  {flags:<18} {r['path']}  ({r['n_tests']} tests)")
    eprint(footer(f"{len(cands)} test files", u.requests, u.cached, u.input_tokens, ms,
                  f"run the top ones first: jev tests --top {min(5, len(results))} --paths-only | xargs <runner>"))
    return 0


def cmd_diff(args) -> int:
    raw, _, changed = _diff_state(args)
    hunks = split_hunks(raw)
    c = client_for(args, "diff")
    t0 = time.monotonic()
    rows, u = rate_hunks(c, hunks, head_chars=args.head_chars, task=args.task, scope_bar=args.scope_bar, min_level=args.min_level, top=args.top,
                         concurrency=args.concurrency)
    ms = (time.monotonic() - t0) * 1000
    if args.json:
        dump(args, {"changed": changed, "hunks": rows, "n_hunks": len(hunks), **u.as_dict(), "ms": round(ms)})
        return 0
    scope = bool(args.task)
    by_level = {name: sum(1 for r in rows if r["level"] == name) for name in RISK_NAMES}
    print(f"{len(hunks)} hunk(s) in {len(changed)} file(s) · " + " · ".join(f"{v} {k}" for k, v in by_level.items() if v)
          + (f" · {sum(1 for r in rows if r.get('flagged'))} outside the task (< {args.scope_bar})" if scope else ""))
    print(f"\n{'risk':>5}  {'level':<8} {'scope  ' if scope else ''}where")
    for r in rows:
        col = f"{r['in_scope']:>5.2f}  " if scope else ""
        print(f"{r['risk']:>5.2f}  {r['level']:<8} {col}{r['file']}:{r['line']}  (+{r['added']}/-{r['removed']})" + ("  <- outside the task?" if r.get("flagged") else ""))
        print(f"{'':>5}  {'':<8} {' ' * len(col)}  {truncate(r['summary'], args.width)}")
    eprint(footer(f"{len(hunks)} hunks", u.requests, u.cached, u.input_tokens, ms))
    return 0


def cmd_failures(args) -> int:
    if args.diff == "-" and (not args.failures or args.failures == "-"):
        raise UsageError("stdin cannot carry both the diff and the failures; pass one as a file")
    raw, diff_txt, changed = _diff_state(args)
    items = split_items(read_source(args.failures, "failures"), args.split, args.text_key)
    c = client_for(args, "failures")
    t0 = time.monotonic()
    rows, u = sort_failures(c, diff_txt, items, head_chars=args.head_chars, concurrency=args.concurrency)
    ms = (time.monotonic() - t0) * 1000
    for r in rows:
        r["summary"] = first_line(items[r["i"]], args.width)
    if args.json:
        dump(args, {"changed": changed, "failures": [dict(r, text=items[r["i"]]) for r in rows], **u.as_dict(), "ms": round(ms)})
        return 0
    yours = sum(1 for r in rows if r["mine"] >= args.mine_bar)
    print(f"{len(items)} failure(s) · {yours} likely caused by this diff (p >= {args.mine_bar}) · {sum(1 for r in rows if r['flaky_level'] == 'flaky')} look flaky")
    print(f"\n{'mine':>5}  {'flaky':>5}  {'kind':<13} failure")
    for r in rows:
        tag = "  <- yours" if r["mine"] >= args.mine_bar else "  <- rerun first" if r["flaky_level"] == "flaky" else ""
        print(f"{r['mine']:>5.2f}  {r['flaky']:>5.2f}  {r['flaky_level']:<13} #{r['i']:<3} {r['summary']}{tag}")
    eprint(footer(f"{len(items)} failures", u.requests, u.cached, u.input_tokens, ms))
    return 0


def cmd_stream(args) -> int:
    use_saved(args, "question")
    if not args.question:
        raise UsageError("give the question (naming the line as `candidate`) or --q NAME")
    band = parse_band(args.abstain)
    c = client_for(args, "stream")
    unc_f = open(out_path(args.uncertain_out), "a") if args.uncertain_out else None
    buf: list[str] = []
    first_at = None
    seen = yes = no = unsure = req = tin = 0
    t_start = time.monotonic()
    try:
        import select
        waitable = sys.platform != "win32"
    except ImportError:
        waitable = False

    def flush():
        nonlocal buf, first_at, yes, no, unsure, req, tin
        if not buf:
            return
        g = grade(c, buf, args.question, args.query or "a stream of lines", chunk_size=args.batch, concurrency=2)
        req, tin = req + g.requests, tin + g.input_tokens
        for r in g.results:
            ln, p = buf[r["i"]], r["p"]
            if in_band(p, band):
                unsure += 1
                if unc_f:
                    unc_f.write(json.dumps({"p": round(p, 3), "line": ln}, ensure_ascii=False) + "\n")
                    unc_f.flush()
                if not args.only:
                    print(f"{p:.2f}  ?    {ln}", flush=True)
            elif p >= args.threshold:
                yes += 1
                if args.only in (None, "yes"):
                    print(ln if args.plain else f"{p:.2f}  yes  {ln}", flush=True)
            else:
                no += 1
                if args.only in (None, "no"):
                    print(ln if args.plain else f"{p:.2f}  no   {ln}", flush=True)
        buf, first_at = [], None

    try:
        while True:
            if waitable:
                timeout = None if first_at is None else max(0.0, args.every - (time.monotonic() - first_at))
                ready, _, _ = select.select([sys.stdin], [], [], timeout)
                if not ready:
                    flush()
                    continue
            ln = sys.stdin.readline()
            if ln == "":
                break
            ln = ln.rstrip("\n")
            if not ln.strip():
                continue
            seen += 1
            buf.append(ln)
            first_at = first_at or time.monotonic()
            if len(buf) >= args.batch:
                flush()
        flush()
    except KeyboardInterrupt:
        flush()
    finally:
        if unc_f:
            unc_f.close()
    eprint(f"· {seen} lines · {yes} yes · {no} no · {unsure} unsure · {req} request(s) · {tin} in tokens · {time.monotonic() - t_start:.1f}s · ${cost_usd(tin):.5f}")
    return 0

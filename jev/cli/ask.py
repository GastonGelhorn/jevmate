"""ask, yes, pick, rate: one state, one request."""

from __future__ import annotations

import json
import sys

from ..errors import UsageError
from ..questions import choice, level_name, noul, parse_kv, parse_value, score
from ..render import emit
from ..settings import MAX_SCORE_LEVELS
from ..textio import implicit_stdin, read_source
from ._common import RAW, add_common, add_question_args, add_saved_arg, add_state_args, build_state, client_for, questions_from_args, use_saved
from .docs import example


def register(sub) -> None:
    a = sub.add_parser("ask", help="several questions about one state, in one request", formatter_class=RAW, epilog=example("triage"),
                       description="Any mix of noul / choice / score questions about one state, answered in ONE request: they run "
                                   "in parallel, so a question that only matters on some branch is nearly free. Also takes a full "
                                   "request as `-f request.json` or as JSON on stdin.")
    add_state_args(a)
    add_question_args(a)
    a.add_argument("-f", "--request", help="a complete request JSON (state + questions); '-' for stdin")
    add_common(a)
    a.set_defaults(fn=cmd_ask)

    y = sub.add_parser("yes", help="one yes/no -> P(yes); exit 0 yes, 1 no",
                       description="One noul question. Prints `<p> yes|no|uncertain`. Exit 0 when p >= --threshold (or >= the top of --band), else 1.")
    y.add_argument("instructions", nargs="?", help="the question, phrased so that high means yes (or --q NAME)")
    add_saved_arg(y)
    y.add_argument("--true", help="what a yes means (optional criteria)")
    y.add_argument("--false", help="what a no means (optional criteria)")
    y.add_argument("--threshold", type=float, default=0.5)
    y.add_argument("--band", type=float, nargs=2, metavar=("LOW", "HIGH"), help="p < LOW no · LOW..HIGH uncertain · >= HIGH yes")
    add_state_args(y)
    add_common(y)
    y.set_defaults(fn=cmd_yes)

    k = sub.add_parser("pick", help="one choice -> option, probabilities, confidence",
                       description="One choice question. Options as NAME or NAME=description. Exit 1 when confidence < --min-confidence.")
    k.add_argument("instructions")
    k.add_argument("options", nargs="+", metavar="OPTION[=desc]")
    k.add_argument("--min-confidence", type=float, default=0.0)
    add_state_args(k)
    add_common(k)
    k.set_defaults(fn=cmd_pick)

    r = sub.add_parser("rate", help="one score -> position on a rubric, confidence",
                       description="One score question. Levels low to high, 2 to 10, each a concrete situation. Exit 1 when confidence < --min-confidence.")
    r.add_argument("instructions")
    r.add_argument("levels", nargs="+", metavar="LEVEL")
    r.add_argument("--min-confidence", type=float, default=0.0)
    add_state_args(r)
    add_common(r)
    r.set_defaults(fn=cmd_rate)


def _full_request(args, raw: str):
    try:
        body = json.loads(raw)
    except ValueError as e:
        raise UsageError(f"request JSON is invalid: {e}")
    if not isinstance(body, dict) or "state" not in body or "questions" not in body:
        raise UsageError("a request JSON needs `state` and `questions`")
    c = client_for(args, "ask")
    if body.get("model") and not args.model:
        c.model = body["model"]
    resp = c.ask(body["state"], body["questions"])
    emit(args, resp, c.last_ms)
    return 0


def cmd_ask(args) -> int:
    if args.request:
        return _full_request(args, read_source(args.request, "request"))
    has_state = args.state or args.state_file or args.state_json or args.field
    has_q = getattr(args, "qlist", None) or args.questions_file or args.questions_json
    if not has_state and not has_q and not sys.stdin.isatty():
        raw = implicit_stdin() or ""
        if raw.lstrip().startswith("{"):
            return _full_request(args, raw)
        raise UsageError("stdin was not a request JSON; pass questions with --noul/--choice/--score and the state with --state or stdin")
    state = build_state(args)
    qs = questions_from_args(args)
    c = client_for(args, "ask")
    resp = c.ask(state, qs)
    emit(args, resp, c.last_ms)
    return 0


def cmd_yes(args) -> int:
    use_saved(args, "instructions")
    if not args.instructions:
        raise UsageError("give the question, or --q NAME")
    state = build_state(args)
    c = client_for(args, "yes")
    resp = c.ask(state, {"q": noul(parse_value(args.instructions), args.true, args.false)})
    p = resp["answers"]["q"]["noul"]
    lo, hi = args.band if args.band else (args.threshold, args.threshold)
    verdict = "yes" if p >= hi else "no" if p < lo else "uncertain"
    if args.json:
        print(json.dumps({"noul": p, "verdict": verdict, "threshold": args.threshold, "band": args.band, "model": resp.get("model"),
                          "usage": resp.get("usage"), "cached": bool(resp.get("cached"))}))
    else:
        print(f"{p:.3f} {verdict}")
    return 0 if verdict == "yes" else 1


def cmd_pick(args) -> int:
    state = build_state(args)
    opts = parse_kv(args.options)
    if len(opts) < 2:
        raise UsageError("pick needs at least two options (NAME or NAME=description)")
    c = client_for(args, "pick")
    a = c.ask(state, {"q": choice(parse_value(args.instructions), opts)})["answers"]["q"]
    conf = a.get("confidence", 0.0)
    probs = a.get("probabilities") or {}
    ok = conf >= args.min_confidence
    if args.json:
        print(json.dumps({"choice": a["choice"], "confidence": conf, "probabilities": probs, "verdict": "ok" if ok else "uncertain",
                          "min_confidence": args.min_confidence, "model": c.model}))
    else:
        ranked = ", ".join(f"{k} {v:.2f}" for k, v in sorted(probs.items(), key=lambda kv: -kv[1]))
        print(f"{a['choice']}  conf={conf:.2f}  [{ranked}]" if ok else f"uncertain  best={a['choice']} conf={conf:.2f} < {args.min_confidence}  [{ranked}]")
    return 0 if ok else 1


def cmd_rate(args) -> int:
    state = build_state(args)
    levels = [parse_value(lv) for lv in args.levels]
    if not 2 <= len(levels) <= MAX_SCORE_LEVELS:
        raise UsageError(f"rate needs 2 to {MAX_SCORE_LEVELS} level descriptions, low to high")
    c = client_for(args, "rate")
    a = c.ask(state, {"q": score(parse_value(args.instructions), levels)})["answers"]["q"]
    conf = a.get("confidence", 0.0)
    top = len(levels) - 1
    nearest = min(top, max(0, int(round(a["score"]))))
    ok = conf >= args.min_confidence
    if args.json:
        print(json.dumps({"score": a["score"], "top": top, "normalized": a["score"] / top, "nearest_level": nearest,
                          "nearest": level_name(levels[nearest]), "confidence": conf, "probabilities": a.get("probabilities"),
                          "verdict": "ok" if ok else "uncertain", "model": c.model}))
    else:
        flag = "" if ok else f"  uncertain (conf < {args.min_confidence})"
        print(f"{a['score']:.2f}/{top}  conf={conf:.2f}  ≈ {level_name(levels[nearest])}{flag}")
    return 0 if ok else 1

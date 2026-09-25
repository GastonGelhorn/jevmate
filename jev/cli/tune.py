"""tune and label: measure a question before it decides anything at volume, and build the rows to measure it on."""

from __future__ import annotations

import json
import random
import time

from ..errors import UsageError
from ..grading import grade, in_band, parse_band
from ..questions import parse_value
from ..render import dump, eprint, footer, truncate
from ..settings import cost_usd
from ..textio import read_source
from ._common import RAW, add_common, add_grading_args, add_saved_arg, client_for, out_path, use_saved
from .docs import example

BOOLISH = {"true": True, "false": False, "yes": True, "no": False, "y": True, "n": False, "1": True, "0": False, "t": True, "f": False}


def register(sub) -> None:
    tn = sub.add_parser("tune", help="which phrasing and threshold measure best on rows you labelled", formatter_class=RAW, epilog=example("tune"),
                        description="Runs every candidate question over the labelled rows, sweeps the threshold from 0.05 to 0.95 and "
                                    "reports, per question: best threshold, accuracy, balanced accuracy, F1, precision, recall, the score "
                                    "at 0.50 and ECE. For the winner: the confusion matrix, flip risk, a reliability table and abstention bands.")
    tn.add_argument("--labels", required=True, help="JSONL, one {text, label} per line (--text-key / --label-key rename them)")
    tn.add_argument("--text-key", default="text")
    tn.add_argument("--label-key", default="label")
    tn.add_argument("--positive", help="the label that means yes (required unless labels are true/false, yes/no, 1/0)")
    tn.add_argument("--question", "-Q", action="append", help="a candidate question naming `candidate`; repeatable")
    tn.add_argument("--questions-file", help="one candidate question per line (# comments allowed)")
    tn.add_argument("--query", help="shared `query` field, when the questions name it")
    tn.add_argument("--context", help="shared extra state (text, JSON, or @file), available as `context`")
    tn.add_argument("--optimize", choices=["accuracy", "balanced", "f1", "precision", "recall"], default="accuracy",
                    help="what the sweep maximises (balanced for imbalanced classes)")
    tn.add_argument("--bins", type=int, default=10, help="reliability table bins (default 10)")
    tn.add_argument("--report", help="write the full JSON report, per-row p included, here")
    tn.add_argument("--errors", type=int, metavar="N", help="list the winner's N most confident errors; the label is the first suspect")
    tn.add_argument("--save", metavar="NAME", help="save the winner as a question: phrasing, threshold, band, model (jev q list; --q NAME anywhere)")
    tn.add_argument("--project", action="store_true", help="with --save: into the project's .jev/questions/")
    add_grading_args(tn)
    add_common(tn)
    tn.set_defaults(fn=cmd_tune)

    lb = sub.add_parser("label", help="build a labelled set: the uncertain rows first, one key per row (--pick N for an agent)", formatter_class=RAW, epilog=example("label"),
                        description="Labels rows into the JSONL `jev tune` reads. With -Q the rows are graded first (cached) and the ones "
                                    "inside --band, or nearest 0.5, come first: those move the threshold estimate most. Every answer is "
                                    "appended as it is given. An agent has no terminal: it runs --pick N, reads the rows, writes the labels.")
    lb.add_argument("--input", "-i", help="JSONL (rows with --text-key) or plain lines; '-' or omitted reads stdin")
    lb.add_argument("--text-key", default="text")
    lb.add_argument("--out", "-o", required=True, help="labelled JSONL to append to (existing rows are skipped)")
    lb.add_argument("--positive", default="yes", help="label written for [y] (default yes)")
    lb.add_argument("--negative", default="no", help="label written for [n] (default no)")
    lb.add_argument("--question", "-Q", help="grade first with this question (naming `candidate`) so the uncertain rows come first")
    add_saved_arg(lb)
    lb.add_argument("--band", nargs=2, type=float, metavar=("LO", "HI"), help="rows with p inside the band come first (from `jev tune`)")
    lb.add_argument("--query", help="shared `query` field, if the question names it")
    lb.add_argument("--pick", type=int, metavar="N", help="print the N most useful unlabelled rows as JSONL and exit")
    lb.add_argument("--limit", type=int, help="stop after this many labels")
    lb.add_argument("--shuffle", action="store_true", help="random order (when not grading)")
    lb.add_argument("--width", type=int, default=160, help="display width ([f] prints the whole row)")
    add_common(lb)
    lb.set_defaults(fn=cmd_label)


# ---------------------------------------------------------------- metrics

def metrics(pairs: list[tuple[float, bool]], t: float) -> dict:
    tp = fp = fn = tn = 0
    for p, y in pairs:
        if p >= t:
            tp += y
            fp += not y
        else:
            fn += y
            tn += not y
    n = len(pairs) or 1
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    spec = tn / (tn + fp) if tn + fp else 0.0
    return {"t": round(t, 2), "tp": tp, "fp": fp, "fn": fn, "tn": tn, "accuracy": (tp + tn) / n, "precision": prec, "recall": rec,
            "specificity": spec, "f1": 2 * prec * rec / (prec + rec) if prec + rec else 0.0, "balanced": (rec + spec) / 2}


def sweep(pairs, optimize: str) -> dict:
    best = None
    for i in range(5, 96):
        m = metrics(pairs, i / 100)
        key = (round(m[optimize], 6), -abs(i / 100 - 0.5))  # ties go to the less extreme threshold
        if best is None or key > best[0]:
            best = (key, m)
    return best[1]


def reliability(pairs, bins: int) -> tuple[list[dict], float]:
    """Per bin: mean p against the share that were actually positive. ECE is the size-weighted gap;
    0 is perfect, 0.10 and up says the number ranks but does not mean."""
    rows, ece, n = [], 0.0, len(pairs) or 1
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        sel = [(p, y) for p, y in pairs if lo <= p < hi or (b == bins - 1 and p == 1.0)]
        if not sel:
            continue
        mp = sum(p for p, _ in sel) / len(sel)
        fy = sum(1 for _, y in sel if y) / len(sel)
        ece += len(sel) / n * abs(mp - fy)
        rows.append({"lo": round(lo, 2), "hi": round(hi, 2), "n": len(sel), "mean_p": round(mp, 3), "observed": round(fy, 3)})
    return rows, ece


def abstention(pairs, t: float, widths=(0.05, 0.10, 0.15)) -> list[dict]:
    out = []
    for w in widths:
        lo, hi = max(0.0, t - w), min(1.0, t + w)
        dec = [(p, y) for p, y in pairs if not lo <= p <= hi]
        acc = sum(1 for p, y in dec if (p >= t) == y) / len(dec) if dec else 0.0
        out.append({"lo": round(lo, 2), "hi": round(hi, 2), "decided": len(dec), "of": len(pairs), "accuracy": acc})
    return out


def _labelled(path, text_key: str, label_key: str) -> tuple[list, list]:
    texts, labels = [], []
    for k, ln in enumerate(read_source(path, "labels").splitlines()):
        if not ln.strip():
            continue
        try:
            row = json.loads(ln)
        except ValueError as e:
            raise UsageError(f"{path}:{k + 1}: invalid JSON: {e}")
        if not isinstance(row, dict) or text_key not in row or label_key not in row:
            raise UsageError(f"{path}:{k + 1}: need keys {text_key!r} and {label_key!r} (--text-key / --label-key)")
        texts.append(row[text_key])
        labels.append(row[label_key])
    return texts, labels


def cmd_tune(args) -> int:
    questions = list(args.question or [])
    if args.questions_file:
        questions += [ln.strip() for ln in read_source(args.questions_file, "questions").splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    if not questions:
        raise UsageError("give at least one --question / -Q (repeatable) or --questions-file")
    texts, labels = _labelled(args.labels, args.text_key, args.label_key)
    if len(texts) < 10:
        raise UsageError(f"{len(texts)} labelled rows is too few to sweep a threshold; 30 starts to mean something, 100 is comfortable")
    if args.positive is not None:
        ys = [str(lab) == args.positive for lab in labels]
        if not any(ys) or all(ys):
            raise UsageError(f"label {args.positive!r} matches {sum(ys)}/{len(ys)} rows; labels seen: {sorted(set(map(str, labels)))[:8]}")
        positive = args.positive
    else:
        norm = [str(lab).strip().lower() for lab in labels]
        if not all(v in BOOLISH for v in norm):
            raise UsageError(f"--positive LABEL is required; labels seen: {sorted(set(map(str, labels)))[:8]}")
        ys = [BOOLISH[v] for v in norm]
        positive = "true"
    base = sum(ys) / len(ys)
    c = client_for(args, "tune")
    ctx = parse_value(args.context) if args.context else None
    query = parse_value(args.query) if args.query else "the item under judgment"
    report, tot_in, tot_req, tot_cached = [], 0, 0, 0
    t0 = time.monotonic()
    for q in questions:
        g = grade(c, texts, q, query, context=ctx, chunk_chars=args.chunk_chars, chunk_size=args.chunk_size, concurrency=args.concurrency)
        ps = [r["p"] for r in g.results]
        pairs = list(zip(ps, ys))
        best = sweep(pairs, args.optimize)
        rel, ece = reliability(pairs, args.bins)
        report.append({"question": q, "best": best, "at_0.50": metrics(pairs, 0.5), "ece": round(ece, 4), "reliability": rel,
                       "abstention": abstention(pairs, best["t"]), "requests": g.requests, "input_tokens": g.input_tokens,
                       "cached_requests": g.cached, "p": [round(p, 4) for p in ps]})
        tot_in, tot_req, tot_cached = tot_in + g.input_tokens, tot_req + g.requests, tot_cached + g.cached
    report.sort(key=lambda r: -r["best"][args.optimize])
    ms = (time.monotonic() - t0) * 1000
    full = {"n": len(texts), "positive": positive, "base_rate": round(base, 4), "majority_baseline": round(max(base, 1 - base), 4),
            "optimize": args.optimize, "model": c.model, "questions": report,
            "usage": {"requests": tot_req, "cached_requests": tot_cached, "input_tokens": tot_in, "usd": round(cost_usd(tot_in), 6)}, "ms": round(ms)}
    if args.report:
        out_path(args.report).write_text(json.dumps(full, indent=2, ensure_ascii=False) + "\n")
    if args.save:
        from datetime import date
        from ..library import save
        w0, b0 = report[0], report[0]["best"]
        saved_to = save(args.save, {"question": w0["question"], "threshold": b0["t"], "band": [w0["abstention"][1]["lo"], w0["abstention"][1]["hi"]],
                                    "model": c.model, "query": args.query, "kind": "rank",
                                    "measured": {"n": len(texts), "accuracy": round(b0["accuracy"], 4), "balanced": round(b0["balanced"], 4),
                                                 "f1": round(b0["f1"], 4), "ece": w0["ece"], "optimize": args.optimize, "date": date.today().isoformat()}},
                        project=args.project)
        eprint(f"· saved as {args.save!r} -> {saved_to} (jev yes/rank/batch --q {args.save})")
    if args.json:
        if not args.verbose:
            for r in full["questions"]:
                r.pop("p", None)
        dump(args, full)
        return 0

    w = report[0]
    b = w["best"]
    print(f"jev tune · {len(texts)} labelled rows · positive = {positive!r} at {base:.1%} · majority baseline {max(base, 1 - base):.1%} · optimizing {args.optimize} · {c.model}")
    print(f"\n{'#':>2}  {'best t':>6}  {'acc':>6}  {'bal':>6}  {'F1':>5}  {'prec':>6}  {'rec':>6}  {'@0.50':>6}  {'ECE':>5}  question")
    for k, r in enumerate(report, 1):
        m = r["best"]
        print(f"{k:>2}  {m['t']:>6.2f}  {m['accuracy']:>6.1%}  {m['balanced']:>6.1%}  {m['f1']:>5.2f}  {m['precision']:>6.1%}  {m['recall']:>6.1%}"
              f"  {r['at_0.50']['accuracy']:>6.1%}  {r['ece']:>5.2f}  {truncate(r['question'], 70)}")
    print(f"\nwinner: {w['question']}")
    print(f"  threshold {b['t']:.2f}" + (f" · {args.optimize} {b[args.optimize]:.1%}" if args.optimize != "accuracy" else "")
          + f" · accuracy {b['accuracy']:.1%} (was {w['at_0.50']['accuracy']:.1%} at 0.50) · balanced {b['balanced']:.1%} · F1 {b['f1']:.2f}")
    lean = max(b["fp"], b["fn"]) >= 3 * max(1, min(b["fp"], b["fn"]))
    print(f"  confusion @ {b['t']:.2f}:  TP {b['tp']}  FP {b['fp']}  FN {b['fn']}  TN {b['tn']}"
          + (f"   <- the errors lean one way ({max(b['fp'], b['fn'])} vs {min(b['fp'], b['fn'])}): a threshold problem, not confusion" if lean else ""))
    near = sum(1 for p in w["p"] if abs(p - b["t"]) <= 0.02)
    print(f"  flip risk: {near} row(s) within ±0.02 of the threshold"
          + (" -- identical requests jitter about that much, so these can land on either side between runs; --abstain is the fix" if near else ""))
    print(f"  reliability (is p honest?)   {'bin':<12}{'n':>4}   {'mean p':>6}   {'observed':>8}")
    for r in w["reliability"]:
        print(f"                               [{r['lo']:.1f},{r['hi']:.1f})  {r['n']:>4}   {r['mean_p']:>6.2f}   {r['observed']:>8.2f}")
    print(f"                               ECE {w['ece']:.3f}  (0 = perfectly calibrated; 0.10+ means p ranks but does not mean)"
          + ("  n is small, read as a trend" if len(texts) < 200 else ""))
    print("  abstention around the threshold:")
    for a in w["abstention"]:
        print(f"    p in [{a['lo']:.2f}, {a['hi']:.2f}] -> decide {a['decided']}/{a['of']} at {a['accuracy']:.1%}, hand {a['of'] - a['decided']} to a reader")
    if args.errors:
        wrong = [(p, y, t) for p, y, t in zip(w["p"], ys, texts) if (p >= b["t"]) != y]
        wrong.sort(key=lambda x: -abs(x[0] - b["t"]))
        print(f"\n  most confident errors ({min(args.errors, len(wrong))} of {len(wrong)}): the model is sure and the label disagrees; check the label first")
        for p, y, t in wrong[: args.errors]:
            print(f"    p={p:.2f} label={positive if y else 'not ' + positive:<12} {truncate(str(t), 100)}")
    import shlex
    a1 = w["abstention"][1]
    print("\nuse it:")
    print(f"  jev rank --query ... --candidates-file items.txt --instructions {shlex.quote(w['question'])} --min {b['t']:.2f}")
    print(f"  jev rank --query ... --candidates-file items.txt --instructions {shlex.quote(w['question'])} --abstain {a1['lo']:.2f} {a1['hi']:.2f} --uncertain-out review.txt")
    if len(questions) == 1:
        print("  (one phrasing tested; four phrasings of one judgment once spanned ten points. Try two or three more.)")
    eprint(footer(f"{len(questions)} question(s)", tot_req, tot_cached, tot_in, ms, f"report {args.report}" if args.report else ""))
    return 0


# ---------------------------------------------------------------- label

def _items(raw: str, text_key: str) -> list[str]:
    items = []
    for ln in raw.splitlines():
        if not ln.strip():
            continue
        try:
            obj = json.loads(ln)
        except ValueError:
            items.append(ln)
            continue
        if isinstance(obj, dict):
            items.append(str(obj[text_key]) if text_key in obj else json.dumps(obj, ensure_ascii=False))
        else:
            items.append(ln)
    return items


def cmd_label(args) -> int:
    use_saved(args, "question")
    items = _items(read_source(args.input, "input"), args.text_key)
    out = out_path(args.out)
    done: dict[str, str] = {}
    if out.exists():
        for ln in out.read_text().splitlines():
            try:
                row = json.loads(ln)
                done[str(row[args.text_key])] = str(row.get("label"))
            except (ValueError, KeyError, TypeError):
                continue
    todo = list(dict.fromkeys(t for t in items if t not in done))
    if not todo:
        print(f"nothing left to label: {len(done)} rows in {out}")
        return 0
    if args.shuffle:
        random.shuffle(todo)

    p_of: dict[str, float] = {}
    inside_n = 0
    if args.question:
        c = client_for(args, "label")
        g = grade(c, todo, args.question, parse_value(args.query) if args.query else "the item under judgment")
        p_of = {todo[r["i"]]: r["p"] for r in g.results}
        band = parse_band(args.band)
        if band:
            mid = (band[0] + band[1]) / 2
            inside = sorted((t for t in todo if in_band(p_of[t], band)), key=lambda t: abs(p_of[t] - mid))
            outside = sorted((t for t in todo if not in_band(p_of[t], band)), key=lambda t: abs(p_of[t] - mid))
            todo, inside_n = inside + outside, len(inside)
        else:
            todo.sort(key=lambda t: abs(p_of[t] - 0.5))
        eprint(footer(f"graded {len(todo)} unlabelled rows", g.requests, g.cached, g.input_tokens, 0,
                      f"{inside_n} inside the band come first" if band else "least certain first"))

    if args.pick:
        for t in todo[: args.pick]:
            row = {args.text_key: t}
            if t in p_of:
                row["p"] = round(p_of[t], 3)
            print(json.dumps(row, ensure_ascii=False))
        eprint(f'· {min(args.pick, len(todo))} of {len(todo)} unlabelled rows · append {{"{args.text_key}": …, "label": "{args.positive}"|"{args.negative}"}} lines to {out}')
        return 0

    try:
        tty = open("/dev/tty")
    except OSError:
        raise UsageError("labelling needs a terminal. An agent runs `jev label … --pick N`, reads the rows and writes the labels itself")
    labelled, k, history = 0, 0, []
    print(f"{len(todo)} to label · {len(done)} already in {out} · keys: [y] {args.positive}  [n] {args.negative}  [s] skip  [f] full text  [u] undo  [q] quit")
    while k < len(todo) and (not args.limit or labelled < args.limit):
        t = todo[k]
        pinfo = f"  p={p_of[t]:.2f}" if t in p_of else ""
        print(f"\n[{k + 1}/{len(todo)}]{pinfo}  {truncate(t, args.width)}")
        print("> ", end="", flush=True)
        ans = tty.readline().strip().lower()
        if ans == "":
            continue
        if ans in ("q", "quit"):
            break
        if ans == "f":
            print(t)
        elif ans == "s":
            k += 1
        elif ans == "u":
            if history:
                lines = out.read_text().splitlines()
                out.write_text("\n".join(lines[:-1]) + ("\n" if len(lines) > 1 else ""))
                history.pop()
                labelled = max(0, labelled - 1)
                k = max(0, k - 1)
                print("undone")
        elif ans in ("y", "n"):
            row = {args.text_key: t, "label": args.positive if ans == "y" else args.negative}
            if t in p_of:
                row["p"] = round(p_of[t], 3)
            with out.open("a") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            history.append(t)
            labelled += 1
            k += 1
        else:
            print("y / n / s / f / u / q")
    rows = [json.loads(ln) for ln in out.read_text().splitlines() if ln.strip()] if out.exists() else []
    pos = sum(1 for r in rows if str(r.get("label")) == args.positive)
    print(f"\n{labelled} labelled this session · {len(rows)} in {out} · {pos} {args.positive} / {len(rows) - pos} {args.negative}")
    if len(rows) >= 30 and args.question:
        import shlex
        print(f"next: jev tune --labels {out} --positive {args.positive} --text-key {args.text_key} -Q {shlex.quote(args.question)}")
    elif len(rows) < 30:
        print(f"{30 - len(rows)} more and `jev tune` starts to mean something")
    return 0

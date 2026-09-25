"""q: saved questions. mcp: the MCP server."""

from __future__ import annotations

import json

from .. import library
from ..errors import UsageError
from ..render import dump, truncate


def register(sub) -> None:
    q = sub.add_parser("q", help="saved questions: list | show | save | rm (a phrasing with its measured threshold, band and model)",
                       description="A saved question is a phrasing plus what `jev tune` measured for it: threshold, band, the model it was tuned "
                                   "on. `jev yes --q NAME`, `jev rank --q NAME`, `jev batch --q NAME`, `jev label --q NAME` and `decide_many(rows, "
                                   "library.load(NAME))` reuse it. Project questions live in .jev/questions/, personal ones in the jev home.")
    q.add_argument("action", nargs="?", choices=["list", "show", "save", "rm"], default="list")
    q.add_argument("name", nargs="?")
    q.add_argument("--question", "-Q", help="save: the phrasing (names the item `candidate`, or the state's fields)")
    q.add_argument("--threshold", type=float, help="save: p at which the answer is yes")
    q.add_argument("--band", nargs=2, type=float, metavar=("LO", "HI"), help="save: the band handed to a reader")
    q.add_argument("--true", help="save: what a yes means")
    q.add_argument("--false", help="save: what a no means")
    q.add_argument("--model", help="save: pin the model the thresholds were measured on")
    q.add_argument("--note", help="save: a line for the next reader")
    q.add_argument("--from", dest="from_report", help="save: take the winner of a `jev tune --report` file")
    q.add_argument("--project", action="store_true", help="save into the project's .jev/questions/ instead of the home")
    q.add_argument("--json", action="store_true")
    q.add_argument("--compact", action="store_true")
    q.set_defaults(fn=cmd_q)

    m = sub.add_parser("mcp", help="serve the decisions as MCP tools over stdio (the plugin's .mcp.json runs this)",
                       description="A Model Context Protocol server: decide, ask, rank, sift, tests, diff, cluster, session. One process per "
                                   "Claude Code session, so the keep-alive connection is reused across calls.")
    m.set_defaults(fn=cmd_mcp)


def cmd_q(args) -> int:
    if args.action == "list":
        rows = library.list_all()
        if args.json:
            dump(args, rows)
            return 0
        if not rows:
            print("no saved questions yet: jev tune … --save NAME, or jev q save NAME -Q '…' --threshold 0.42 --band 0.32 0.52")
            return 0
        print(f"{'name':<20} {'scope':<8} {'t':>5} {'band':<12} {'measured':<20} question")
        for r in rows:
            band = f"{r['band'][0]:.2f}–{r['band'][1]:.2f}" if r.get("band") else "-"
            m = r.get("measured") or {}
            meas = f"{m.get('accuracy', 0):.1%} on {m.get('n', '?')} ({m.get('date', '')[:10]})" if m else "-"
            t = f"{r['threshold']:.2f}" if r.get("threshold") is not None else "-"
            print(f"{r['name']:<20} {r['scope']:<8} {t:>5} {band:<12} {meas:<20} {truncate(r['question'], 70)}")
        return 0
    if not args.name:
        raise UsageError(f"jev q {args.action} NAME")
    if args.action == "show":
        spec = library.load(args.name)
        dump(args, spec)
        return 0
    if args.action == "rm":
        print(f"removed {library.remove(args.name)}")
        return 0
    spec: dict = {}
    if args.from_report:
        try:
            rep = json.loads(open(args.from_report).read())
            w = rep["questions"][0]
            spec = {"question": w["question"], "threshold": w["best"]["t"], "band": [w["abstention"][1]["lo"], w["abstention"][1]["hi"]],
                    "model": rep.get("model"), "kind": "rank",
                    "measured": {"n": rep.get("n"), "accuracy": w["best"]["accuracy"], "balanced": w["best"]["balanced"], "f1": w["best"]["f1"],
                                 "ece": w.get("ece"), "optimize": rep.get("optimize"), "date": _today()}}
        except (OSError, ValueError, KeyError, IndexError) as e:
            raise UsageError(f"could not read a tune report from {args.from_report}: {e}")
    for k in ("question", "threshold", "true", "false", "model", "note"):
        v = getattr(args, k, None)
        if v is not None:
            spec[k] = v
    if args.band:
        spec["band"] = list(args.band)
    p = library.save(args.name, spec, project=args.project)
    print(f"saved {args.name} -> {p}")
    return 0


def _today() -> str:
    from datetime import date
    return date.today().isoformat()


def cmd_mcp(args) -> int:
    from ..mcp import serve
    return serve()

"""The semantic `if`: a calibrated yes/no in a script's condition, with a third outcome.

A question that is right 76% of the time does its damage in the rows near the threshold. A
script that files those rows as `no` because `bool(p >= t)` said so hides them; a script that
sets them aside hands a person thirty rows instead of four hundred. `Decision` makes the second
script the only one you can write.
"""

from __future__ import annotations

from .questions import noul


class Decision:
    """Exactly one of .yes / .no / .unsure is True. `if decision:` raises on purpose."""

    __slots__ = ("p", "threshold", "band", "item")

    def __init__(self, p: float, threshold: float = 0.5, band: tuple[float, float] | None = None, item=None):
        self.p = float(p)
        self.threshold = float(threshold)
        self.band = band
        self.item = item

    @property
    def unsure(self) -> bool:
        return self.band is not None and self.band[0] <= self.p <= self.band[1]

    @property
    def yes(self) -> bool:
        return not self.unsure and self.p >= self.threshold

    @property
    def no(self) -> bool:
        return not self.unsure and self.p < self.threshold

    @property
    def answer(self) -> str:
        return "unsure" if self.unsure else ("yes" if self.p >= self.threshold else "no")

    def __bool__(self):
        raise TypeError("a Decision has three outcomes; test .yes, .no or .unsure (or read .answer)")

    def __repr__(self):
        band = f", band={self.band}" if self.band else ""
        return f"Decision({self.answer}, p={self.p:.3f}, t={self.threshold}{band})"


def decide(state, question: str, *, threshold: float = 0.5, band=None, true=None, false=None, client=None, label: str = "decide") -> Decision:
    """One yes/no about one state; `question` names the state's fields in backticks."""
    if client is None:
        from .client import Client
        client = Client(label=label)
    r = client.ask(state, {"q": noul(question, true, false)})
    return Decision(r["answers"]["q"]["noul"], threshold, band, state)


def decide_many(items, question: str, *, threshold: float = 0.5, band=None, query=None, context=None,
                client=None, label: str = "decide_many", chunk_size: int = 250, concurrency: int = 6) -> list[Decision]:
    """The same yes/no over many items, about 250 per request, one Decision per item in input order.
    `question` refers to the item as `candidate` and, if given, to `query` and `context`."""
    items = list(items)
    if not items:
        return []
    if client is None:
        from .client import Client
        client = Client(label=label)
    from .grading import grade
    g = grade(client, items, question, query if query is not None else "the item under judgment",
              context=context, chunk_size=chunk_size, concurrency=concurrency)
    return [Decision(r["p"], threshold, band, items[r["i"]]) for r in g.results]


SCAFFOLD = r"""#!/usr/bin/env python3
'''__NAME__: <one line: what this decides, over which rows>

Three buckets. Rows the model cannot decide go to REVIEW for a person; forcing them into yes or
no is where an imperfect question does its damage. THRESHOLD and BAND come from
    jev tune --labels labelled.jsonl --positive yes -Q '<QUESTION>'
run on thirty or more rows labelled by hand.
'''
import json
import sys

sys.path.insert(0, "__LIB__")
from jev import JevError, decide_many  # noqa: E402

QUESTION = "Is `candidate` ...?"        # one judgment, literal words; the row is `candidate`
THRESHOLD, BAND = 0.50, (0.40, 0.60)     # from `jev tune`: its threshold, and the band it got wrong
SRC, TEXT_KEY = "rows.jsonl", "text"
YES, NO, REVIEW = "yes.jsonl", "no.jsonl", "review.jsonl"

rows = [json.loads(line) for line in open(SRC) if line.strip()]
try:
    decisions = decide_many([r[TEXT_KEY] for r in rows], QUESTION, threshold=THRESHOLD, band=BAND, label="__NAME__")
except JevError as e:
    sys.exit(f"jev: {e}")

out = {name: open(name, "w") for name in (YES, NO, REVIEW)}
for row, d in zip(rows, decisions):
    row["p"] = round(d.p, 3)
    out[YES if d.yes else NO if d.no else REVIEW].write(json.dumps(row) + "\n")
for f in out.values():
    f.close()
n = lambda attr: sum(1 for d in decisions if getattr(d, attr))  # noqa: E731
print(f"{n('yes')} yes -> {YES} · {n('no')} no -> {NO} · {n('unsure')} unsure -> {REVIEW} (read these yourself)")
"""

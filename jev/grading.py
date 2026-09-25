"""One question over many items, packed into as few requests as the budget allows.

The item travels inside its own question, never at an index into a shared array. Asked about
`candidates[i]`, the model mis-located the item 86 times in 320 at 150 items per request and 29
times at 25 per request; with the item embedded in the question it was wrong 0 times in 320 at
320 per request, in 0.4 s. Every command that grades a list goes through `grade`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

from .errors import JevError, UsageError
from .questions import noul, score

SCAFFOLD_CHARS = 40  # JSON around each question: id, type, braces


def size_of(item) -> int:
    return len(item) if isinstance(item, str) else len(json.dumps(item, ensure_ascii=False))


def pack(items, max_chars: int, max_items: int, per_item_overhead: int = 0) -> list[list[tuple[int, object]]]:
    """Greedy packing in input order. `per_item_overhead` is the question text repeated around every
    item; 250 items carrying a 400-character instruction add 100k characters the items alone hide."""
    chunks: list[list[tuple[int, object]]] = []
    cur: list[tuple[int, object]] = []
    size = 0
    for i, item in enumerate(items):
        n = size_of(item) + per_item_overhead
        if cur and (size + n > max_chars or len(cur) >= max_items):
            chunks.append(cur)
            cur, size = [], 0
        cur.append((i, item))
        size += n
    if cur:
        chunks.append(cur)
    return chunks


class Graded(NamedTuple):
    results: list[dict]   # one per item, input order: {"i", "p", "candidate"} (+ "score", "confidence" with levels)
    requests: int
    input_tokens: int
    output_tokens: int
    cached: int


def grade(client, items, question: str, query, *, context=None, levels=None,
          chunk_chars: int = 90_000, chunk_size: int = 250, concurrency: int = 6) -> Graded:
    """Grade every item against one question. Yes/no by default; with `levels` a rubric, and `p` is
    the score divided by the top level so it sits on 0..1 like a probability."""
    items = list(items)
    if not items:
        return Graded([], 0, 0, 0, 0)
    chunks = pack(items, chunk_chars, chunk_size, len(question) + SCAFFOLD_CHARS)
    state = {"query": query}
    if context is not None:
        state["context"] = context
    top = (len(levels) - 1) if levels else 1

    def run(chunk):
        qs = {}
        for j, (_, item) in enumerate(chunk):
            body = {"question": question, "candidate": item}
            qs[f"c{j}"] = score(body, levels) if levels else noul(body)
        resp = client.ask(state, qs)
        answers = resp.get("answers") or {}
        out = []
        for j, (i, item) in enumerate(chunk):
            a = answers.get(f"c{j}")
            if a is None:
                raise JevError(f"the response is missing an answer for item #{i}")
            if levels:
                out.append({"i": i, "p": a["score"] / top, "score": a["score"], "confidence": a.get("confidence"), "candidate": item})
            else:
                out.append({"i": i, "p": a["noul"], "candidate": item})
        u = resp.get("usage") or {}
        return out, u.get("input_tokens", 0), u.get("output_tokens", 0), bool(resp.get("cached"))

    workers = max(1, min(concurrency, len(chunks)))
    if workers == 1:
        parts = [run(c) for c in chunks]
    else:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=workers) as ex:
            parts = list(ex.map(run, chunks))
    results: list[dict] = []
    tin = tout = cached = 0
    for out, i_, o_, c_ in parts:
        results.extend(out)
        tin += i_
        tout += o_
        cached += c_
    return Graded(results, len(chunks), tin, tout, cached)


def parse_band(v) -> tuple[float, float] | None:
    if not v:
        return None
    lo, hi = float(v[0]), float(v[1])
    if not 0.0 <= lo < hi <= 1.0:
        raise UsageError(f"--abstain expects 0 <= LO < HI <= 1, got {lo} {hi}")
    return lo, hi


def in_band(p: float, band) -> bool:
    return band is not None and band[0] <= p <= band[1]


def write_uncertain(path: str, items) -> None:
    """One per line when every item is text, JSONL otherwise: a file meant for a reader."""
    with open(Path(path).expanduser(), "w") as f:
        for x in items:
            f.write((x.replace("\n", " ") if isinstance(x, str) else json.dumps(x, ensure_ascii=False)) + "\n")

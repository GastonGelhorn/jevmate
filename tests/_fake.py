"""A transport that answers like the API without a network, and a temp home for the ledger and cache."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("TYPESAFE_API_KEY", "test-key-0000000000000000")

from jev import settings  # noqa: E402


def fresh_home() -> Path:
    home = Path(tempfile.mkdtemp(prefix="jev-test-"))
    settings.set_home(home)
    settings.RUNTIME.dry_run = False
    settings.RUNTIME.cache = True
    return home


def p_for(question: dict, state) -> float:
    """Deterministic: the judged text (the embedded candidate, else the state) scores high when it
    contains 'yes', lands in the middle on 'maybe', low otherwise."""
    instr = question.get("instructions")
    if isinstance(instr, dict) and "candidate" in instr:
        src = instr["candidate"]
    elif isinstance(state, dict) and "`commands`" in str(instr) and "commands" in state:
        src = state["commands"]  # the honesty hook's second question judges the commands, not the reply
    else:
        src = state
    text = json.dumps(src, ensure_ascii=False).lower()
    if "maybe" in text:
        return 0.5
    return 0.9 if "yes" in text else 0.1


def answer(q: dict, state) -> dict:
    kind = q["type"]
    if kind == "noul":
        return {"type": "noul", "noul": p_for(q, state)}
    if kind == "choice":
        opts = list(q["criteria"])
        probs = {o: (0.7 if i == 0 else 0.3 / max(1, len(opts) - 1)) for i, o in enumerate(opts)}
        return {"type": "choice", "choice": opts[0], "probabilities": probs, "confidence": 0.7}
    levels = q["criteria"]
    top = len(levels) - 1
    s = top * p_for(q, state)
    return {"type": "score", "score": s, "legend": {str(i): lv for i, lv in enumerate(levels)}, "probabilities": {}, "confidence": 0.8}


class FakeTransport:
    """Scripted statuses (default: all 200). Counts requests and remembers bodies."""

    def __init__(self, statuses=None):
        self.statuses = list(statuses or [])
        self.calls: list[tuple[str, str, bytes | None]] = []

    def request(self, method, path, body=None, headers=None):
        self.calls.append((method, path, body))
        status = self.statuses.pop(0) if self.statuses else 200
        if status != 200:
            return status, {"retry-after": "0"}, json.dumps({"error": f"scripted {status}"}).encode()
        if path.endswith("/models"):
            return 200, {}, json.dumps({"models": [{"name": "jev-latest"}]}).encode()
        req = json.loads(body.decode("utf-8"))
        answers = {qid: answer(q, req["state"]) for qid, q in req["questions"].items()}
        tokens = len(body) // 4
        return 200, {"x-typesafe-request-id": f"rid-{len(self.calls)}"}, json.dumps(
            {"model": "jev-test", "answers": answers, "usage": {"input_tokens": tokens, "output_tokens": 0}}).encode()

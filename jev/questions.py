"""The three question shapes, their validation, and the small parsers the CLI shares.

    noul    a statement that is true or false     -> P(true)
    choice  one option out of a fixed set         -> option, P(option) for each, confidence
    score   a position on an ordered rubric       -> score 0..N-1 (fractional), confidence

Instructions and criteria may be strings or JSON objects; the model reads either.
"""

from __future__ import annotations

import json
from pathlib import Path

from .errors import UsageError
from .settings import MAX_CHOICE_OPTIONS, MAX_SCORE_LEVELS

TYPES = ("noul", "choice", "score")


def noul(instructions, true=None, false=None) -> dict:
    q = {"type": "noul", "instructions": instructions}
    criteria = {k: v for k, v in (("true", true), ("false", false)) if v is not None}
    if criteria:
        q["criteria"] = criteria
    return q


def choice(instructions, options) -> dict:
    if not isinstance(options, dict):
        options = {str(o): None for o in options}
    return {"type": "choice", "instructions": instructions, "criteria": dict(options)}


def score(instructions, levels) -> dict:
    return {"type": "score", "instructions": instructions, "criteria": list(levels)}


def validate(questions: dict) -> None:
    if not isinstance(questions, dict) or not questions:
        raise UsageError("questions must be a non-empty object of id -> question")
    for qid, q in questions.items():
        if not isinstance(q, dict):
            raise UsageError(f"question {qid!r}: must be an object")
        kind = q.get("type")
        if kind not in TYPES:
            raise UsageError(f"question {qid!r}: type must be one of {', '.join(TYPES)}, not {kind!r}")
        if "instructions" not in q:
            raise UsageError(f"question {qid!r}: `instructions` is required")
        crit = q.get("criteria")
        if kind == "choice":
            if not isinstance(crit, dict) or len(crit) < 2:
                raise UsageError(f"choice {qid!r}: criteria must map at least two options to descriptions (null is allowed)")
            if len(crit) > MAX_CHOICE_OPTIONS:
                raise UsageError(f"choice {qid!r}: at most {MAX_CHOICE_OPTIONS} options")
        elif kind == "score":
            if not isinstance(crit, list) or not 2 <= len(crit) <= MAX_SCORE_LEVELS:
                raise UsageError(f"score {qid!r}: criteria must be an ordered list of 2 to {MAX_SCORE_LEVELS} levels, low to high")
        elif crit is not None and (not isinstance(crit, dict) or not set(crit) <= {"true", "false"}):
            raise UsageError(f"noul {qid!r}: criteria may only carry `true` and `false`")


def parse_value(v: str):
    """`@path` reads a file (.json parsed, .jsonl as a list); text that looks like JSON is parsed; else the string."""
    if v.startswith("@"):
        p = Path(v[1:]).expanduser()
        try:
            txt = p.read_text()
        except OSError:
            raise UsageError(f"file not found: {p}")
        suffix = p.suffix.lower()
        if suffix in (".json", ".jsonl"):
            try:
                if suffix == ".json":
                    return json.loads(txt)
                return [json.loads(line) for line in txt.splitlines() if line.strip()]
            except ValueError as e:
                raise UsageError(f"{p}: invalid JSON: {e}")
        return txt
    s = v.strip()
    if (s and s[0] in "{[") or s in ("true", "false", "null"):
        try:
            return json.loads(s)
        except ValueError:
            pass
    return v


def parse_kv(items) -> dict:
    """['billing=Payments', 'other'] -> {'billing': 'Payments', 'other': None}"""
    out = {}
    for it in items:
        if "=" in it:
            k, v = it.split("=", 1)
            out[k.strip()] = parse_value(v) if v != "" else None
        else:
            out[it.strip()] = None
    return out


def level_name(desc) -> str:
    """A short name for a rubric level that may be a string or an object."""
    if isinstance(desc, dict):
        for k in ("what", "level", "name", "description"):
            if k in desc:
                return str(desc[k])
        return json.dumps(desc)[:80]
    return str(desc)

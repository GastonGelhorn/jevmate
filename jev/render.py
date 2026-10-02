"""Terminal output shared by the commands."""

from __future__ import annotations

import json
import sys

from .questions import level_name
from .settings import cost_usd

BOLD, DIM, GREEN, YELLOW, RED, RESET = "\033[1m", "\033[2m", "\033[32m", "\033[33m", "\033[31m", "\033[0m"


def eprint(*a, **k) -> None:
    print(*a, file=sys.stderr, **k)


def fmt_k(n: float) -> str:
    n = int(n)
    return f"{n / 1e6:.1f}M" if n >= 1_000_000 else f"{n / 1e3:.0f}k" if n >= 1000 else str(n)


def truncate(s: str, width: int) -> str:
    s = s.replace("\n", " ")
    return s if len(s) <= width else s[: max(0, width - 1)] + "…"


def footer(head: str, requests: int, cached: int, tokens: int, ms: float, *extra: str) -> str:
    parts = [head, f"{requests} request(s)" + (f" ({cached} from cache)" if cached else ""),
             f"{tokens:,} in tokens", f"{ms:.0f} ms", f"${cost_usd(tokens):.5f}", *[e for e in extra if e]]
    return "· " + " · ".join(parts)


def render_answer(qid: str, a: dict) -> str:
    kind = a.get("type")
    if kind == "noul":
        p = a["noul"]
        return f"{qid:<24} noul    {p:.2f}   {'yes' if p >= 0.5 else 'no'}"
    if kind == "choice":
        probs = a.get("probabilities") or {}
        best = a.get("choice", "?")
        rest = ", ".join(f"{k} {v:.2f}" for k, v in sorted(probs.items(), key=lambda kv: -kv[1]) if k != best)
        return f"{qid:<24} choice  {best}   p={probs.get(best, 0):.2f} conf={a.get('confidence', 0):.2f}   [{rest}]"
    if kind == "score":
        legend = a.get("legend") or {}
        top = max(len(legend) - 1, 1)
        s = a["score"]
        return f"{qid:<24} score   {s:.2f}/{top}   conf={a.get('confidence', 0):.2f}   ≈ {level_name(legend.get(str(int(round(s))), ''))}"
    return f"{qid:<24} {kind}     {json.dumps(a)}"


def render_response(resp: dict, ms: float) -> str:
    lines = [render_answer(qid, a) for qid, a in (resp.get("answers") or {}).items()]
    u = resp.get("usage") or {}
    src = "cache" if resp.get("cached") else f"{ms:.0f} ms"
    lines.append(f"· {resp.get('model')} · {u.get('input_tokens', 0)} in / {u.get('output_tokens', 0)} out tokens · {src} · ${cost_usd(u.get('input_tokens', 0)):.6f}")
    return "\n".join(lines)


def emit(args, resp: dict, ms: float, extra: dict | None = None) -> None:
    if getattr(args, "json", False):
        out = dict(resp)
        if extra:
            out.update(extra)
        print(json.dumps(out, indent=None if getattr(args, "compact", False) else 2, ensure_ascii=False))
    else:
        print(render_response(resp, ms))


def dump(args, obj) -> None:
    print(json.dumps(obj, indent=None if getattr(args, "compact", False) else 2, ensure_ascii=False))

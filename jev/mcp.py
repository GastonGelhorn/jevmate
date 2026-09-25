"""An MCP server over stdio (`jev mcp`): the same decisions as typed tool calls.

For an agent, a tool call beats a shell command in three ways: no quoting of backticks, a schema
instead of flags, and no permission prompt on every call. The process stays alive for the whole
session, so the keep-alive connection is reused across calls and nothing is recompiled.
Every tool is read-only on the machine; the only side effects are one request to the API and a
row of metadata in the local ledger. JSON-RPC 2.0, one message per line.
"""

from __future__ import annotations

import json
import os
import sys
import traceback

from . import settings
from ._version import VERSION
from .errors import JevError, UsageError

PROTOCOL = "2025-06-18"
KNOWN_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")

STATE = {"description": "What to judge: text, or an object whose fields the question names in backticks (`ticket.message`).",
         "anyOf": [{"type": "string"}, {"type": "object"}, {"type": "array"}]}
BAND = {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2, "description": "[LO, HI]: a p inside it is `unsure`, for a reader to decide."}
LIB = {"type": "string", "description": "A saved question (jev q list) that supplies the phrasing, threshold, band and pinned model."}

TOOLS = [
    {"name": "decide", "description": "One calibrated yes/no about one state. Returns p (0..1) and yes / no / unsure.",
     "inputSchema": {"type": "object", "properties": {
         "state": STATE, "question": {"type": "string", "description": "Phrased so that high means yes; name fields in backticks."},
         "threshold": {"type": "number", "default": 0.5}, "band": BAND,
         "true": {"type": "string", "description": "What a yes means (optional criteria)."}, "false": {"type": "string", "description": "What a no means."},
         "saved": LIB}, "required": ["state"]}},
    {"name": "ask", "description": "Several typed questions (noul / choice / score) about one state in one request; returns the raw answers.",
     "inputSchema": {"type": "object", "properties": {
         "state": STATE, "questions": {"type": "object", "description": "{id: {type: noul|choice|score, instructions, criteria?}}"}},
         "required": ["state", "questions"]}},
    {"name": "rank", "description": "Grade many candidates against a query (each embedded in its own question, ~250 per request); rank, filter, and set aside the uncertain band.",
     "inputSchema": {"type": "object", "properties": {
         "query": {"description": "What you are looking for.", "anyOf": [{"type": "string"}, {"type": "object"}]},
         "candidates": {"type": "array", "items": {"anyOf": [{"type": "string"}, {"type": "object"}]}},
         "instructions": {"type": "string", "description": "The question, naming `candidate` and `query`."},
         "levels": {"type": "array", "items": {"type": "string"}, "description": "A score rubric instead of yes/no."},
         "top": {"type": "integer"}, "min": {"type": "number"}, "abstain": BAND, "saved": LIB}, "required": ["query", "candidates"]}},
    {"name": "sift", "description": "Which files, functions or grep hits are worth reading for a query, ranked, with the tokens each costs to read. Use before reading a large set.",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string"}, "paths": {"type": "array", "items": {"type": "string"}, "description": "Files or directories (default: the project)."},
         "lines": {"type": "array", "items": {"type": "string"}, "description": "File paths, or grep hits as path:line:text with grep=true."},
         "grep": {"type": "boolean", "default": False}, "functions": {"type": "boolean", "default": False},
         "top": {"type": "integer", "default": 10}, "min": {"type": "number"}, "budget_tokens": {"type": "integer"}, "head_chars": {"type": "integer", "default": 4000}},
         "required": ["query"]}},
    {"name": "tests", "description": "Which test files exercise a diff (git diff HEAD, --staged, or a ref), so they can run first. Name matches and changed test files are flagged.",
     "inputSchema": {"type": "object", "properties": {
         "repo": {"type": "string", "default": "."}, "ref": {"type": "string"}, "staged": {"type": "boolean", "default": False},
         "diff": {"type": "string", "description": "A diff text instead of running git."}, "tests": {"type": "array", "items": {"type": "string"}},
         "top": {"type": "integer", "default": 8}, "min": {"type": "number"}, "boost_matches": {"type": "boolean", "default": False}}}},
    {"name": "diff", "description": "Every hunk of a diff rated cosmetic / local / shared / critical, and with `task`, whether it belongs to the task at all.",
     "inputSchema": {"type": "object", "properties": {
         "repo": {"type": "string", "default": "."}, "ref": {"type": "string"}, "staged": {"type": "boolean", "default": False},
         "diff": {"type": "string"}, "task": {"type": "string"}, "min_level": {"type": "string", "enum": ["cosmetic", "local", "shared", "critical"]},
         "top": {"type": "integer"}}}},
    {"name": "cluster", "description": "Group failures, log lines or findings by root cause (greedy against representatives; k clusters cost k requests).",
     "inputSchema": {"type": "object", "properties": {
         "items": {"type": "array", "items": {"type": "string"}}, "text": {"type": "string", "description": "Runner output to cut with `split`."},
         "split": {"type": "string", "description": "pytest | pytest-long | phpunit | jest | tap | go | blank | line | a regex"},
         "threshold": {"type": "number", "default": 0.7}, "abstain": BAND, "rep": {"type": "string", "enum": ["first", "longest"], "default": "first"}}}},
    {"name": "session", "description": "What jev decided in this session, what that text would have cost the agent to read, and the session's estimated model spend.",
     "inputSchema": {"type": "object", "properties": {"session_id": {"type": "string"}, "cwd": {"type": "string"}}}},
]


def _client(label: str, model: str | None = None):
    from .client import Client
    return Client(model=model, label=f"mcp:{label}")


def _project(path: str | None = None) -> str:
    return os.path.realpath(path or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())


def _band(v):
    from .grading import parse_band
    return parse_band(v) if v else None


def call(name: str, a: dict) -> dict:
    a = dict(a or {})
    if name == "decide":
        from .decide import decide
        from .library import load
        q = load(a["saved"]) if a.get("saved") else a.get("question")
        if not q:
            raise UsageError("decide needs a `question` or a `saved` question")
        d = decide(a["state"], q, threshold=float(a.get("threshold", 0.5)), band=_band(a.get("band")), true=a.get("true"), false=a.get("false"),
                   client=_client("decide"))
        return {"p": round(d.p, 4), "answer": d.answer, "threshold": d.threshold, "band": list(d.band) if d.band else None}
    if name == "ask":
        r = _client("ask").ask(a["state"], a["questions"])
        return {"answers": r.get("answers"), "model": r.get("model"), "cached": bool(r.get("cached"))}
    if name == "rank":
        from .grading import grade, in_band
        spec = None
        if a.get("saved"):
            from .library import load
            spec = load(a["saved"])
        levels = a.get("levels")
        question = a.get("instructions") or (spec or {}).get("question") or ("How well does `candidate` match `query`?" if levels else "Is `candidate` relevant to `query`?")
        band = _band(a.get("abstain")) or (tuple(spec["band"]) if spec and spec.get("band") else None)
        min_p = a.get("min") if a.get("min") is not None else ((spec or {}).get("threshold") if spec and not band else None)
        g = grade(_client("rank", (spec or {}).get("model")), a["candidates"], question, a["query"], levels=levels)
        results = sorted(g.results, key=lambda r: -r["p"])
        uncertain = [r for r in results if in_band(r["p"], band)] if band else []
        results = [r for r in results if not in_band(r["p"], band)] if band else results
        if min_p is not None:
            results = [r for r in results if r["p"] >= float(min_p)]
        if a.get("top"):
            results = results[: int(a["top"])]
        strip = lambda r: {k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()}  # noqa: E731
        return {"results": [strip(r) for r in results], "uncertain": [strip(r) for r in uncertain], "n": len(a["candidates"]),
                "requests": g.requests, "input_tokens": g.input_tokens}
    if name == "sift":
        from .analysis import sift, sift_candidates
        root = _project()
        paths = a.get("paths") or ([] if a.get("lines") else [root])
        paths = [p if os.path.isabs(p) else os.path.join(root, p) for p in paths]
        cands, meta = sift_candidates(paths, a.get("lines") or [], grep=bool(a.get("grep")), functions=bool(a.get("functions")), head_chars=int(a.get("head_chars", 4000)))
        rows, cut_at, u = sift(_client("sift"), a["query"], cands, meta, top=int(a.get("top", 10)), min_p=a.get("min"), budget_tokens=a.get("budget_tokens"))
        return {"results": rows, "n": len(cands), "cut_at": cut_at, **u.as_dict()}
    if name in ("tests", "diff"):
        from .textio import compact_diff, git_diff, split_hunks
        repo = _project(a.get("repo") if a.get("repo") not in (None, ".") else None)
        if a.get("diff"):
            raw = a["diff"]
            import re
            changed = re.findall(r"^\+\+\+ b/(.+)$", raw, re.M)
        else:
            raw, changed = git_diff(repo, bool(a.get("staged")), a.get("ref"))
        if not raw.strip():
            raise UsageError("empty diff: nothing changed against HEAD; pass ref, staged, or a diff text")
        if name == "tests":
            from .analysis import rank_tests, test_candidates
            diff_txt = compact_diff(raw, 50_000)
            roots = [os.path.join(repo, t) if not os.path.isabs(t) else t for t in (a.get("tests") or [])] or None
            cands, meta = test_candidates(repo, changed, roots)
            results, u = rank_tests(_client("tests"), diff_txt, cands, meta, min_p=a.get("min"), top=int(a.get("top", 8)), boost=bool(a.get("boost_matches")))
            return {"changed": changed, "results": results, "n_test_files": len(cands), **u.as_dict()}
        from .analysis import rate_hunks
        rows, u = rate_hunks(_client("diff"), split_hunks(raw), task=a.get("task"), min_level=a.get("min_level"), top=a.get("top"))
        return {"changed": changed, "hunks": rows, **u.as_dict()}
    if name == "cluster":
        from .analysis import cluster
        from .textio import split_items
        items = list(a.get("items") or []) or split_items(a.get("text") or "", a.get("split"))
        clusters, notes, u = cluster(_client("cluster"), items, threshold=float(a.get("threshold", 0.7)), band=_band(a.get("abstain")), rep=a.get("rep") or "first")
        index = {cl["rep"]: k for k, cl in enumerate(clusters, 1)}
        return {"n": len(items),
                "clusters": [{"id": k, "size": len(cl["members"]), "representative": items[cl["rep"]][:300],
                              "members": [{"i": j, "p": round(p, 3), "text": items[j][:300]} for j, p in cl["members"]]} for k, cl in enumerate(clusters, 1)],
                "uncertain": [{"i": j, "cluster": index[r], "p": round(p, 3)} for j, r, p in notes], **u.as_dict()}
    if name == "session":
        from .metrics import one_line, render_session, session_summary
        s = session_summary(a.get("cwd") or _project(), a.get("session_id") or os.environ.get("CLAUDE_SESSION_ID"))
        return {"summary": one_line(s), "text": render_session(s, color=False), "session": s["session"], "model": s["model"], "jev": s["jev"]}
    raise UsageError(f"unknown tool {name!r}")


def _reply(out, id_, result=None, error=None) -> None:
    msg = {"jsonrpc": "2.0", "id": id_}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    out.write(json.dumps(msg, ensure_ascii=False) + "\n")
    out.flush()


def serve(inp=None, out=None) -> int:
    inp = inp or sys.stdin
    out = out or sys.stdout
    settings.RUNTIME.dry_run = False
    for line in inp:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            _reply(out, None, error={"code": -32700, "message": "parse error"})
            continue
        if isinstance(msg, list):  # batches: answer each
            for m in msg:
                _handle(m, out)
            continue
        _handle(msg, out)
    return 0


def _handle(msg: dict, out) -> None:
    method = msg.get("method")
    id_ = msg.get("id")
    params = msg.get("params") or {}
    if method is None:
        return  # a response to something we never send
    if method == "initialize":
        want = params.get("protocolVersion")
        _reply(out, id_, {"protocolVersion": want if want in KNOWN_PROTOCOLS else PROTOCOL,
                          "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": {"name": "jev", "version": VERSION},
                          "instructions": "Calibrated decisions. Use `sift` before reading many files, `rank` for many items, `tests`/`diff` on a change, "
                                          "`cluster` on a red suite, `decide` for one calibrated yes/no. Set a band and read what lands in it yourself."})
        return
    if method == "ping":
        _reply(out, id_, {})
        return
    if method.startswith("notifications/"):
        return
    if method == "tools/list":
        _reply(out, id_, {"tools": [dict(t, annotations={"readOnlyHint": True, "openWorldHint": True}) for t in TOOLS]})
        return
    if method == "tools/call":
        name = params.get("name", "")
        try:
            result = call(name, params.get("arguments") or {})
            text = json.dumps(result, ensure_ascii=False, indent=None if len(json.dumps(result)) > 4000 else 1)
            _reply(out, id_, {"content": [{"type": "text", "text": text}], "structuredContent": result, "isError": False})
        except (JevError, KeyError, TypeError, ValueError) as e:
            _reply(out, id_, {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True})
        except Exception as e:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            _reply(out, id_, {"content": [{"type": "text", "text": f"internal error: {e}"}], "isError": True})
        return
    if id_ is not None:
        _reply(out, id_, error={"code": -32601, "message": f"method not found: {method}"})

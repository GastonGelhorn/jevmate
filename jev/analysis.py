"""The judgments behind sift, tests, diff and cluster, as functions: the CLI and the MCP server
are two thin fronts over these."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .errors import UsageError
from .grading import grade, in_band
from .textio import NON_TEST_STEM, TEST_FILE_RE, est_tokens, read_head, split_functions, test_names, walk

HIT_RE = re.compile(r"^(.+?):(\d+):(.*)$")

SAME_CAUSE = {"question": "Is `candidate` caused by the same underlying problem as `query`?",
              "criteria": {"true": "the same defect or cause (the same missing key, the same unreachable service, the same wrong assumption) "
                                   "even when the test name, file, line numbers or values differ",
                           "false": "a different cause that merely looks alike: the same exception type for a different reason, or two "
                                    "failures that only share a category"}}
EXERCISES = {"question": "Would running the tests in `candidate` exercise the code changed in `query`?",
             "criteria": {"true": "the test file imports, calls, mocks or asserts on the changed functions, classes, routes, templates or "
                                  "behaviour, directly or through an obvious wrapper",
                          "false": "it covers other modules and would pass or fail the same way with or without this change"}}
RISK_LEVELS = [
    "Cosmetic: comments, formatting, renames, docs, log wording; behaviour cannot change",
    "Local logic: behaviour changes confined to one function or template, no external effect if wrong",
    "Shared behaviour: a public interface, a query, validation, error handling, a default or a config others rely on",
    "Critical: persistence or deletion, migrations, auth or permissions, secrets, money, concurrency or locks, anything not undone by a revert",
]
RISK_NAMES = ["cosmetic", "local", "shared", "critical"]
RISK_Q = {"question": "If the change in `candidate` is wrong, how much damage can it do? Rate the hunk, not the file."}
SCOPE_Q = {"question": "Is the change in `candidate` part of the task described in `query`, or needed to carry it out?",
           "criteria": {"true": "it implements, fixes, tests or documents what the task asks for, or is a direct prerequisite",
                        "false": "an unrelated change riding along: a refactor nobody asked for, a different feature, a drive-by cleanup, "
                                 "a config or dependency change the task did not need"}}
MINE_Q = {"question": "Was the failure in `candidate` caused by the code change in `query`?",
          "criteria": {"true": "the failing code path, symbol, file, query or behaviour is one the diff adds, removes or alters, directly or "
                               "through an obvious caller",
                       "false": "the failure is in code the diff does not touch, or would have failed the same way without it"}}
FLAKY_Q = {"question": "Does the failure in `candidate` look deterministic, or like flakiness?"}
FLAKY_LEVELS = [
    "Deterministic: a wrong value, a missing key, a type error, a failed assertion on computed output",
    "Possibly environmental: depends on fixtures, files or state that another test may have left",
    "Likely flaky: a timeout, a refused or reset connection, a race or ordering assumption, the wall clock, randomness, an external service",
]
FLAKY_NAMES = ["deterministic", "environmental", "flaky"]
SIFT_Q = "Is `candidate` worth reading to find `query`? Judge from its path and the excerpt."


class Usage:
    """Requests, tokens and cache hits added up across several gradings."""

    def __init__(self):
        self.requests = self.input_tokens = self.cached = 0

    def add(self, g) -> None:
        self.requests += g.requests
        self.input_tokens += g.input_tokens
        self.cached += g.cached

    def as_dict(self) -> dict:
        return {"requests": self.requests, "cached_requests": self.cached, "usage": {"input_tokens": self.input_tokens}}


# ---------------------------------------------------------------- sift

def sift_candidates(paths=(), lines=(), *, grep: bool = False, functions: bool = False, head_chars: int = 4000) -> tuple[list[dict], list[tuple[str, int]]]:
    """What to grade: files under `paths` (plus the paths in `lines`), a function chunk each with
    --functions, or, with grep, a file per group of `path:line:text` hits. Returns (candidates, meta)
    where meta is (display, estimated tokens to read the whole thing)."""
    cands: list[dict] = []
    meta: list[tuple[str, int]] = []
    if grep:
        hits: dict[str, list[str]] = {}
        for ln in lines:
            m = HIT_RE.match(ln)
            if m:
                hits.setdefault(m.group(1), []).append(f"{m.group(2)}: {m.group(3).strip()}")
            else:
                hits.setdefault(ln, [])
        for path, matches in hits.items():
            excerpt = "\n".join(matches)[:head_chars]
            cands.append({"path": path, "hits": len(matches), "matches": excerpt})
            try:
                size = os.stat(path).st_size
            except OSError:
                size = len(excerpt)
            meta.append((f"{path}  ({len(matches)} hit{'s' if len(matches) != 1 else ''})", est_tokens(size)))
        return cands, meta
    for pth in walk(list(paths) + list(lines)):
        if functions:
            txt = read_head(pth, 400_000)
            if txt is None:
                continue
            for line, name, src in split_functions(txt):
                cands.append({"path": str(pth), "line": line, "name": name, "source": src[:head_chars]})
                meta.append((f"{pth}:{line}  {name or '(top)'}", est_tokens(len(src))))
        else:
            head = read_head(pth, head_chars)
            if head is None:
                continue
            cands.append({"path": str(pth), "excerpt": head})
            meta.append((str(pth), est_tokens(pth.stat().st_size)))
    return cands, meta


def sift(client, query, cands: list[dict], meta: list[tuple[str, int]], *, instructions: str | None = None, context=None,
         top: int | None = None, min_p: float | None = None, budget_tokens: int | None = None,
         chunk_chars: int = 90_000, chunk_size: int = 250, concurrency: int = 6) -> tuple[list[dict], int | None, "Usage"]:
    if not cands:
        raise UsageError("no readable text files among the inputs")
    g = grade(client, cands, instructions or SIFT_Q, query, context=context, chunk_chars=chunk_chars, chunk_size=chunk_size, concurrency=concurrency)
    results = sorted(g.results, key=lambda r: -r["p"])
    if min_p is not None:
        results = [r for r in results if r["p"] >= min_p]
    if top:
        results = results[:top]
    rows, spent, cut_at = [], 0, None
    for k, r in enumerate(results):
        display, tokens = meta[r["i"]]
        spent += tokens
        if budget_tokens and cut_at is None and spent > budget_tokens:
            cut_at = k
        rows.append({"p": round(r["p"], 4), "tokens": tokens, "display": display, "cumulative_tokens": spent,
                     **{kk: vv for kk, vv in cands[r["i"]].items() if kk in ("path", "line", "name", "hits")}})
    u = Usage()
    u.add(g)
    return rows, cut_at, u


# ---------------------------------------------------------------- tests

def test_candidates(repo: str, changed: list[str], roots=None, extra_files=(), *, head_chars: int = 1200) -> tuple[list[dict], list[dict]]:
    files = [f for f in walk(roots or [repo]) if TEST_FILE_RE.search(str(f).replace("\\", "/"))]
    files += [Path(x) for x in extra_files if x]
    files = list(dict.fromkeys(files))
    changed_stems = {re.sub(r"\.[^.]+$", "", Path(f).name).lower() for f in changed}
    cands, meta = [], []
    for f in files:
        txt = read_head(f, 200_000)
        if txt is None:
            continue
        names = test_names(txt)
        stem = NON_TEST_STEM.sub("", re.sub(r"\.[^.]+$", "", f.name)).lower()
        rel = str(f)
        cands.append({"path": rel, "tests": names, "head": txt[:head_chars]})
        meta.append({"path": rel, "n_tests": len(names), "name_match": bool(stem) and (stem in changed_stems or any(stem in cs for cs in changed_stems)),
                     "changed": any(rel.endswith(ch) for ch in changed)})
    return cands, meta


def rank_tests(client, diff_txt: str, cands: list[dict], meta: list[dict], *, instructions: str | None = None, min_p: float | None = None,
               top: int | None = None, boost: bool = False, concurrency: int = 6) -> tuple[list[dict], "Usage"]:
    if not cands:
        raise UsageError("no test files found (tests/, __tests__/, test_*.py, *_test.*, *.test.*, *Test.php, *_spec.rb); pass --tests DIR or --tests-from -")
    q = dict(EXERCISES)
    if instructions:
        q["question"] = instructions
    g = grade(client, cands, json.dumps(q), diff_txt, chunk_chars=max(15_000, 100_000 - len(diff_txt)), concurrency=concurrency)
    results = [{"p": round(r["p"], 4), **meta[r["i"]]} for r in g.results]
    results.sort(key=lambda r: (-(r["changed"] or r["name_match"]) if boost else 0, -r["p"]))
    if min_p is not None:
        results = [r for r in results if r["p"] >= min_p or r["name_match"] or r["changed"]]
    if top:
        results = results[:top]
    u = Usage()
    u.add(g)
    return results, u


# ---------------------------------------------------------------- diff

def rate_hunks(client, hunks: list[dict], *, head_chars: int = 3000, task: str | None = None, scope_bar: float = 0.40,
               min_level: str | None = None, top: int | None = None, concurrency: int = 6) -> tuple[list[dict], "Usage"]:
    if not hunks:
        raise UsageError("no hunks with changed lines in this diff")
    cands = [{"file": h["file"], "hunk": h["text"][:head_chars]} for h in hunks]
    u = Usage()
    g = grade(client, cands, json.dumps(RISK_Q), "a code review", levels=RISK_LEVELS, concurrency=concurrency)
    u.add(g)
    scope: dict[int, float] = {}
    if task:
        s = grade(client, cands, json.dumps(SCOPE_Q), task, concurrency=concurrency)
        u.add(s)
        scope = {r["i"]: r["p"] for r in s.results}
    rows = []
    for r in g.results:
        h = hunks[r["i"]]
        lvl = min(len(RISK_LEVELS) - 1, max(0, round(r["score"])))
        row = {"file": h["file"], "line": h["line"], "risk": round(r["p"], 3), "level": RISK_NAMES[lvl], "score": round(r["score"], 2),
               "confidence": r.get("confidence"), "added": h["added"], "removed": h["removed"], "summary": h["summary"][:160]}
        if scope:
            row["in_scope"] = round(scope[r["i"]], 3)
            row["flagged"] = row["in_scope"] < scope_bar
        rows.append(row)
    rows.sort(key=lambda r: (-r["risk"], r["file"], r["line"]))
    if min_level:
        k = RISK_NAMES.index(min_level)
        rows = [r for r in rows if RISK_NAMES.index(r["level"]) >= k or r.get("flagged")]
    if top:
        rows = rows[:top]
    return rows, u


# ---------------------------------------------------------------- failures

def sort_failures(client, diff_txt: str, items: list[str], *, head_chars: int = 1500, concurrency: int = 6) -> tuple[list[dict], "Usage"]:
    if not items:
        raise UsageError("no failures after splitting; pick a --split preset or a regex")
    cands = [t[:head_chars] for t in items]
    u = Usage()
    m = grade(client, cands, json.dumps(MINE_Q), diff_txt, chunk_chars=max(15_000, 100_000 - len(diff_txt)), concurrency=concurrency)
    f = grade(client, cands, json.dumps(FLAKY_Q), "a test run", levels=FLAKY_LEVELS, concurrency=concurrency)
    u.add(m)
    u.add(f)
    flaky = {r["i"]: r for r in f.results}
    rows = []
    for r in m.results:
        fr = flaky[r["i"]]
        rows.append({"i": r["i"], "mine": round(r["p"], 3), "flaky": round(fr["p"], 3), "flaky_level": FLAKY_NAMES[min(2, max(0, round(fr["score"])))]})
    rows.sort(key=lambda r: (-r["mine"], r["flaky"]))
    return rows, u


# ---------------------------------------------------------------- cluster

def cluster(client, items: list[str], *, threshold: float = 0.70, band=None, rep: str = "first", head_chars: int = 1500,
            instructions: str | None = None, concurrency: int = 6) -> tuple[list[dict], list[tuple[int, int, float]], "Usage"]:
    """Greedy clustering against representatives: k clusters cost k requests. Returns (clusters sorted
    by size, near misses as (item, representative, p) for singletons that fell inside the band, usage)."""
    if len(items) < 2:
        raise UsageError(f"{len(items)} item(s); nothing to cluster")
    q = dict(SAME_CAUSE)
    if instructions:
        q["question"] = instructions
    qtext = json.dumps(q)
    unassigned = list(range(len(items)))
    if rep == "longest":
        unassigned.sort(key=lambda i: -len(items[i]))
    clusters: list[dict] = []
    near: dict[int, tuple[int, float]] = {}
    u = Usage()
    while unassigned:
        head = unassigned.pop(0)
        members = [(head, 1.0)]
        if unassigned:
            cands = [items[j][:head_chars] for j in unassigned]
            g = grade(client, cands, qtext, items[head][:head_chars],
                      chunk_chars=max(20_000, 95_000 - min(len(items[head]), head_chars)), concurrency=concurrency)
            u.add(g)
            p_of = {unassigned[r["i"]]: r["p"] for r in g.results}
            still = []
            for j in unassigned:
                p = p_of[j]
                if p >= threshold and not in_band(p, band):
                    members.append((j, p))
                else:
                    still.append(j)
                    if in_band(p, band) and (j not in near or near[j][1] < p):
                        near[j] = (head, p)
            unassigned = still
        clusters.append({"rep": head, "members": members})
    clusters.sort(key=lambda cl: -len(cl["members"]))
    singles = {cl["rep"] for cl in clusters if len(cl["members"]) == 1}
    notes = sorted(((j, r, p) for j, (r, p) in near.items() if j in singles), key=lambda x: -x[2])
    return clusters, notes, u

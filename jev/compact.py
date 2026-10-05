"""Compaction that keeps what the agent still needs and moves the rest to disk.

Before Claude Code or Codex compacts a conversation, every large tool result in it is judged: kept
whole, cut to its head and tail, or moved to a file with one line left in its place. Rules settle
what needs no model: a file read again or edited later, a command run again, an error a retry
fixed. Jev settles the rest, each result inside its own question, against what the person asked
for and what the agent was doing. The uncertain band is cut, never moved out whole. The person's
words, the agent's own text and the latest results are never touched.

Every judged result is saved on disk first, so nothing is lost. After the compaction a short block
repeats, verbatim, the results Jev judged the work still needs, the latest included, and says where
the others are. Without Jev it repeats none: recency alone cannot tell the file about to be changed
from the output of a task already finished. Reading one of those files later marks a result moved
out too eagerly; `jev compact --report` counts them, which is how the bars get tuned.

With Claude Code's mod the summarizer can also be handed the pruned conversation, which makes the
summary cheaper when the summarizer pays for its input. Codex compacts on its own and gets the block.
"""

from __future__ import annotations

import bisect
import json
import os
import re
import shutil
import time
from pathlib import Path

from . import ledger, settings
from .textio import est_tokens

MIN_CHARS = 1_500        # a smaller result costs less to keep than to judge
RECENT = 10              # the latest tool results stay as they are
KEEP_AT = 0.65           # p at or above: kept whole
CUT_AT = 0.35            # p from here up to KEEP_AT: cut to its head and tail; below: moved to disk
HEAD, TAIL = 900, 300    # what a cut result keeps
SEEN_HEAD, SEEN_TAIL = 1_200, 400   # what Jev reads of a result
RESTORE_TOKENS = 2_500   # the block after a compaction stays under this (Codex's default limit for hook context)
EXCERPT = 1_500          # the longest a result repeated in that block can be
STORE_DAYS = 14
LOCAL_JUDGED = 20        # on a model on this machine (seconds a question) only the newest are judged: the block repeats a few
MODEL_BARS = {"tev1": (0.48, 0.30)}  # tev1 answers nearer the middle: on a labelled set the needed sat at 0.42-0.72, the stale at 0.22-0.47
STUB = "[jev compact]"
MARK = "/compacted/session-"   # in a path: one of the files a compaction saved

CLAUDE_MARK = b'"type":"system","subtype":"compact_boundary"'
CODEX_MARK = b',"type":"compacted"'

KEEP_Q = ("Does the work described in `query` still depend on the exact text of `candidate`, a tool result from earlier in the "
          "session? Yes for code about to be changed, an error not yet fixed, a value, a path or a requirement that nothing later "
          "in the session replaced. No for exploration that led elsewhere, output already acted on, listings and logs whose "
          "conclusion the agent already wrote down, and anything cheap to read again.")

READ_TOOLS = {"Read", "read_file", "view", "NotebookRead"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit", "apply_patch", "write_file", "edit_file"}
SHELL_TOOLS = {"Bash", "shell", "exec_command", "local_shell_call", "exec", "container.exec", "shell_command"}
CODEX_CALLS = {"function_call", "custom_tool_call", "local_shell_call", "web_search_call", "tool_search_call"}
CODEX_OUTPUTS = {"function_call_output", "custom_tool_call_output", "local_shell_call_output", "tool_search_output"}
PATCH_FILE = re.compile(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$", re.M)
PATHISH = re.compile(r"(?<![\w/.-])(?:\.{0,2}/)?[\w.-]+(?:/[\w.-]+)+\.\w{1,8}\b")
WHY = {"recent": "one of the latest results", "small": "small", "read-again": "the file was read again later",
       "edited-later": "the file was edited later", "ran-again": "the command ran again later", "retried": "a retry succeeded later",
       "written": "the text is in the file it wrote", "jev": "Jev judged it no longer needed", "unjudged": "kept: Jev could not be reached"}


# ---------------------------------------------------------------- reading a session

def _text(o) -> str:
    """The text of a content value: a string, a list of blocks, or anything else as JSON."""
    if o is None:
        return ""
    if isinstance(o, str):
        return o
    if isinstance(o, list):
        parts = []
        for b in o:
            if isinstance(b, str):
                parts.append(b)
            elif isinstance(b, dict) and isinstance(b.get("text"), str):
                parts.append(b["text"])
        return "\n".join(p for p in parts if p)
    try:
        return json.dumps(o, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(o)


def _line_start(f, at: int, step: int = 1 << 20) -> int:
    pos = at
    while pos > 0:
        start = max(0, pos - step)
        f.seek(start)
        j = f.read(pos - start).rfind(b"\n")
        if j != -1:
            return start + j + 1
        pos = start
    return 0


def _tail(path: Path, marker: bytes, block: int = 8 << 20) -> bytes:
    """The file from the last line holding `marker` (the latest compaction) to its end. The rest is history
    that compaction already replaced, so a transcript of a gigabyte is read from where it still matters."""
    size = path.stat().st_size
    with open(path, "rb") as f:
        end, carry = size, b""
        while end > 0:
            start = max(0, end - block)
            f.seek(start)
            chunk = f.read(end - start)
            i = (chunk + carry).rfind(marker)  # `carry` finds a marker split across two blocks
            if i != -1:
                f.seek(_line_start(f, start + i))
                return f.read()
            carry, end = chunk[:len(marker)], start
            if size - start > (512 << 20):  # no compaction in sight: the latest part is the live one
                f.seek(size - (64 << 20))
                return f.read()
        f.seek(0)
        return f.read()


def detect(path: Path) -> str | None:
    """`claude` for a Claude Code transcript, `codex` for a Codex rollout."""
    try:
        with open(path, "rb") as f:
            head = f.read(256 << 10)
    except OSError:
        return None
    if b'"type":"session_meta"' in head or b'"type":"response_item"' in head or b'"type":"turn_context"' in head:
        return "codex"
    if b'"sessionId"' in head or b'"type":"user"' in head or b'"type":"assistant"' in head:
        return "claude"
    return None


def _claude_events(lines) -> list:
    events: list = []
    for line in lines:
        if b'"type":"user"' not in line and b'"type":"assistant"' not in line and CLAUDE_MARK not in line:
            continue
        try:
            o = json.loads(line)
        except ValueError:
            continue
        kind = o.get("type")
        if kind == "system" and o.get("subtype") == "compact_boundary":
            events = []  # what came before was replaced by the summary that follows
            continue
        if kind not in ("user", "assistant") or o.get("isSidechain"):
            continue
        content = (o.get("message") or {}).get("content")
        if kind == "user":
            real = not (o.get("isMeta") or o.get("isCompactSummary"))
            if isinstance(content, str):
                events.append(("say", "user", content, real and not content.lstrip().startswith("<")))
                continue
            for b in content or []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_result":
                    events.append(("result", str(b.get("tool_use_id") or ""), _text(b.get("content")), bool(b.get("is_error"))))
                elif b.get("type") == "text":
                    t = b.get("text") or ""
                    events.append(("say", "user", t, real and not t.lstrip().startswith("<")))
        else:
            for b in content or []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text":
                    events.append(("say", "assistant", b.get("text") or "", False))
                elif b.get("type") == "tool_use":
                    inp = b.get("input")
                    events.append(("call", str(b.get("id") or ""), str(b.get("name") or "?"), inp if isinstance(inp, dict) else {"input": inp}))
    return events


def _codex_input(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except ValueError:
            pass
        return {"input": raw}
    return {}


def _codex_item(events: list, it) -> None:
    if not isinstance(it, dict):
        return
    kind = it.get("type")
    if kind == "message":
        role = it.get("role")
        if role not in ("user", "assistant"):
            return  # developer and system text is Codex's own, sent again every turn
        t = _text(it.get("content"))
        if t:
            events.append(("say", role, t, role == "user" and not t.lstrip().startswith("<")))
    elif kind in CODEX_CALLS:
        raw = it["arguments"] if "arguments" in it else it.get("input", it.get("action"))
        events.append(("call", str(it.get("call_id") or it.get("id") or ""), str(it.get("name") or kind), _codex_input(raw)))
    elif kind in CODEX_OUTPUTS:
        out = it.get("output")
        error = isinstance(out, dict) and out.get("success") is False
        events.append(("result", str(it.get("call_id") or it.get("id") or ""), _text(out), bool(error)))


def _codex_events(lines) -> list:
    events: list = []
    for line in lines:
        if b'"type":"response_item"' not in line and CODEX_MARK not in line:
            continue
        try:
            o = json.loads(line)
        except ValueError:
            continue
        payload = o.get("payload") or {}
        if o.get("type") == "compacted":
            events = []
            for it in payload.get("replacement_history") or []:
                _codex_item(events, it)
        elif o.get("type") == "response_item":
            _codex_item(events, payload)
    return events


def load(path: Path) -> tuple[list, str]:
    """The live conversation of a transcript, as events: ("say", role, text, a real prompt?),
    ("call", id, tool, input), ("result", id, text, error). Returns (events, host)."""
    host = detect(path)
    if host == "codex":
        return _codex_events(_tail(path, CODEX_MARK).split(b"\n")), host
    return _claude_events(_tail(path, CLAUDE_MARK).split(b"\n")), host or "claude"


def from_messages(messages: list) -> list:
    """Events from Claude Code's session messages, as the mod hands them over."""
    events: list = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role, t = m.get("role"), m.get("text") or ""
        if t:
            events.append(("say", role, t, role == "user" and not t.lstrip().startswith("<")))
        for u in m.get("toolUses") or []:
            uid = str(u.get("tool_use_id") or "")
            events.append(("call", uid, str(u.get("tool") or "?"), u.get("input") if isinstance(u.get("input"), dict) else {}))
            if isinstance(u.get("text"), str):
                events.append(("result", uid, u["text"], bool(u.get("isError"))))
        for r in m.get("toolResults") or []:
            events.append(("result", str(r.get("tool_use_id") or ""), r.get("text") or "", bool(r.get("isError"))))
    return events


# ---------------------------------------------------------------- the results and what settles them

def _items(events: list) -> tuple[list[dict], list]:
    calls: dict[str, dict] = {}
    order: list[dict] = []
    says: list = []
    for pos, ev in enumerate(events):
        if ev[0] == "say":
            says.append((pos, ev[1], ev[2], ev[3]))
        elif ev[0] == "call":
            it = {"id": ev[1], "tool": ev[2], "input": ev[3] or {}, "pos": pos, "text": None, "error": False}
            calls[ev[1]] = it
            order.append(it)
        elif ev[0] == "result" and ev[1] in calls:
            calls[ev[1]]["text"], calls[ev[1]]["error"] = ev[2], ev[3]
    return [it for it in order if it["text"] is not None], says


def _cmd(inp: dict) -> str:
    c = inp.get("command", inp.get("cmd", ""))
    if not c and isinstance(inp.get("input"), str):
        c = inp["input"]
    if isinstance(c, list):
        c = " ".join(str(x) for x in c)
    return " ".join(str(c).split())


def _path(inp: dict) -> str:
    return str(inp.get("file_path") or inp.get("notebook_path") or inp.get("path") or "")


def describe(it: dict) -> str:
    """The call in one line: the command, the file and range, the pattern or the address."""
    tool, inp = it["tool"], it["input"] or {}
    if tool in SHELL_TOOLS or "command" in inp or "cmd" in inp:
        s = _cmd(inp)
    elif _path(inp):
        s = _path(inp)
        if inp.get("pattern"):
            s = f"{inp['pattern']} in {s}"
        if inp.get("offset") or inp.get("limit"):
            s += f" (from line {inp.get('offset') or 1}" + (f", {inp['limit']} lines)" if inp.get("limit") else ")")
    elif inp.get("pattern") or inp.get("url") or inp.get("query"):
        s = str(inp.get("pattern") or inp.get("url") or inp.get("query"))
    elif isinstance(inp.get("input"), str) and inp["input"].strip():
        s = inp["input"].strip().splitlines()[0]
    else:
        try:
            s = json.dumps(inp, ensure_ascii=False)
        except (TypeError, ValueError):
            s = str(inp)
    s = " ".join(s.split())
    return s[:140] + ("…" if len(s) > 140 else "")


SHELL_READER = re.compile(r"^(?:cat|nl|head|tail|less|more|bat|sed|awk)$")
FULL_READER = {"cat", "nl", "bat", "less", "more"}
_PREFIX = re.compile(r"^\s*(?:(?:cd|pushd)\s+\S+|export\s+\w+=\S*|\w+=\S*|true|set\s+-\S+)\s*(?:&&|;)\s*")
JS_CMD = re.compile(r"""\b(?:cmd|command)\s*:\s*(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)'|`([^`]*)`)""")
PATCH_ANY = re.compile(r"\*\*\* (?:Update|Add|Delete) File: ([^\n\\\"'`]+)")


def _strings(o, out: list) -> list:
    if isinstance(o, str):
        out.append(o)
    elif isinstance(o, dict):
        for v in o.values():
            _strings(v, out)
    elif isinstance(o, list):
        for v in o:
            _strings(v, out)
    return out


def _commands(it: dict) -> list[str]:
    """The shell commands a call ran: the command itself, or those named inside code mode's script."""
    inp = it["input"]
    if it["tool"] in ("exec", "js", "run") and isinstance(inp.get("input"), str):
        found = [next(g for g in m.groups() if g is not None) for m in JS_CMD.finditer(inp["input"])]
        return [" ".join(c.replace("\\n", " ").replace('\\"', '"').split()) for c in found]
    if it["tool"] in SHELL_TOOLS or it["tool"].endswith("_shell_call"):
        c = _cmd(inp)
        return [c] if c else []
    return []


def _shell_reads(cmd: str) -> list[tuple[str, str]]:
    """Files a simple shell read prints: (path, "full") for a whole file, (path, the command) for part of one."""
    prev = None
    while prev != cmd:
        prev, cmd = cmd, _PREFIX.sub("", cmd, count=1)
    first = cmd.split("|")[0]
    try:
        import shlex
        words = shlex.split(first)
    except ValueError:
        words = first.split()
    if not words or not SHELL_READER.match(words[0]):
        return []
    whole = words[0] in FULL_READER and "|" not in cmd and not any(w.startswith("-") and any(ch.isdigit() for ch in w) for w in words[1:])
    out = []
    for w in words[1:]:
        if w.startswith("-") or not ("/" in w or re.search(r"\.\w{1,8}$", w)) or re.search(r"[,{}$*]", w):
            continue
        out.append((w, "full" if whole else cmd))
    return out


def _reads(it: dict) -> list[tuple[str, object]]:
    if it["tool"] in READ_TOOLS and _path(it["input"]):
        inp = it["input"]
        rng = (inp.get("offset"), inp.get("limit"))
        return [(_path(inp), "full" if rng == (None, None) else rng)]
    out = []
    for c in _commands(it):
        out += _shell_reads(c)
    return out


def _writes(it: dict) -> list[str]:
    paths = [_path(it["input"])] if it["tool"] in WRITE_TOOLS and _path(it["input"]) else []
    for s in _strings(it["input"], []):
        if "*** " in s:
            paths += [p.strip() for p in PATCH_ANY.findall(s.replace("\\n", "\n"))]
    return [p for p in paths if p]


def _same_file(a: str, b: str) -> bool:
    """One path named two ways: absolute in one call, relative to the project in another."""
    a, b = a.strip(), b.strip()
    if a == b:
        return True
    a, b = (a, b) if len(a) >= len(b) else (b, a)
    return a.endswith("/" + b.lstrip("./"))


def _key(it: dict) -> str:
    r = _reads(it)
    return f"{it['tool']}|{r[0][0] if r else (_writes(it) or [''])[0] or _cmd(it['input'])}"


def settle(items: list[dict]) -> dict[int, str]:
    """What the rules decide with no model call: the index of each result they move out, and why."""
    by_name: dict[str, list[tuple[int, str, str, object]]] = {}  # basename -> (index, "write" | "read", path, range)
    last_cmd: dict[str, int] = {}
    last_ok: dict[str, int] = {}
    reads_of: list[list] = []
    for j, it in enumerate(items):
        for p in _writes(it):
            by_name.setdefault(os.path.basename(p), []).append((j, "write", p, None))
        rs = _reads(it)
        reads_of.append(rs)
        for p, rng in rs:
            by_name.setdefault(os.path.basename(p), []).append((j, "read", p, rng))
        if it["tool"] in SHELL_TOOLS or it["tool"] in ("exec", "js", "run"):
            c = _cmd(it["input"])
            if c:
                last_cmd[c] = j
        if not it["error"]:
            last_ok[_key(it)] = j
    out: dict[int, str] = {}
    for i, it in enumerate(items):
        why = None
        fates = []  # for each file this result printed: replaced later, and how
        for p, rng in reads_of[i]:
            fate = None
            for j, kind, q, qrng in by_name.get(os.path.basename(p), []):
                if j <= i or not _same_file(p, q):
                    continue
                if kind == "write":
                    fate = "edited-later"
                    break
                if qrng == "full" or qrng == rng:
                    fate = fate or "read-again"
            fates.append(fate)
        if fates and all(fates):  # every file it printed was edited or printed again later
            why = "edited-later" if "edited-later" in fates else "read-again"
        c = _cmd(it["input"])
        if why is None and c and (it["tool"] in SHELL_TOOLS or it["tool"] in ("exec", "js", "run")) and last_cmd.get(c, -1) > i:
            why = "ran-again"
        if why is None and it["error"] and last_ok.get(_key(it), -1) > i:
            why = "retried"
        if why:
            out[i] = why
    return out


def options() -> dict:
    """The bars, from the environment when set there (JEV_COMPACT_KEEP, _CUT, _RECENT, _MIN, _BUDGET)."""
    def pick(env: str, default, cast=float):
        v = os.environ.get(env)
        try:
            return cast(v) if v not in (None, "") else default
        except ValueError:
            return default
    keep, cut = next((b for m, b in MODEL_BARS.items() if settings.default_model().startswith(m)), (KEEP_AT, CUT_AT))
    return {"keep_at": pick("JEV_COMPACT_KEEP", keep), "cut_at": pick("JEV_COMPACT_CUT", cut),
            "recent": pick("JEV_COMPACT_RECENT", RECENT, int), "min_chars": pick("JEV_COMPACT_MIN", MIN_CHARS, int),
            "budget": pick("JEV_COMPACT_BUDGET", RESTORE_TOKENS, int)}


# ---------------------------------------------------------------- the plan

def _seen(text: str) -> str:
    return text if len(text) <= SEEN_HEAD + SEEN_TAIL + 40 else f"{text[:SEEN_HEAD]}\n…\n{text[-SEEN_TAIL:]}"


def _cut(text: str, path: str) -> str:
    if len(text) <= HEAD + TAIL + 200:
        return text
    lines = text[HEAD:len(text) - TAIL].count("\n")
    return f"{text[:HEAD]}\n… {STUB} {lines:,} lines cut here; the full text is at {path} …\n{text[-TAIL:]}"


def _moved(it: dict, path: str, why: str) -> str:
    text = it["text"]
    return (f"{STUB} {it['tool']} {describe(it)}: {text.count(chr(10)) + 1:,} lines, ~{est_tokens(len(text)):,} tokens, "
            f"moved out before a compaction ({WHY.get(why, why)}). The full text is at {path}")


def _goal(says: list) -> dict:
    from .hooks import mask_secrets
    asked = [t for _, role, t, real in says if role == "user" and real and t.strip()][-3:]
    doing = next((t for _, role, t, _r in reversed(says) if role == "assistant" and t.strip()), "")
    return {"the person asked": [mask_secrets(a[-1500:]) for a in asked], "the agent was last doing": mask_secrets(doing[-1200:])}


def _mentioned_later(items: list[dict], says: list) -> list[bool]:
    """Did the agent name something from each result (a path it found, the file it read) later on?"""
    texts = [(pos, t.lower()) for pos, role, t, _ in says if role == "assistant" and t]
    blob, starts, offs = "", [], []
    for pos, t in texts:
        starts.append(pos)
        offs.append(len(blob))
        blob += t + "\n"
    out = []
    for it in items:
        k = bisect.bisect_right(starts, it["pos"])
        if k >= len(starts):
            out.append(False)
            continue
        anchors = {a.lower() for a in PATHISH.findall(it["text"][:20_000])[:12]}
        if _path(it["input"]):
            anchors.add(os.path.basename(_path(it["input"])).lower())
        out.append(any(a and blob.find(a, offs[k]) != -1 for a in anchors))
    return out


def build(events: list, *, client=None, client_error: str | None = None, apply: bool = False, tag: str = "", keep_at: float = KEEP_AT,
          cut_at: float = CUT_AT, recent: int = RECENT, min_chars: int = MIN_CHARS, budget: int = RESTORE_TOKENS) -> dict:
    """Judge every large tool result of a conversation. With `apply`, save them on disk and write the
    block that follows the compaction; without it, report what would happen (`<saved on apply>` paths).
    Without a client (no key, `--rules-only`) the rules still decide and the rest stays whole."""
    items, says = _items(events)
    items = [it for it in items if not it["text"].startswith(STUB)]  # moved out by an earlier compaction already
    rules = settle(items)
    large = [i for i, it in enumerate(items) if len(it["text"]) >= min_chars]
    pinned = set(large[-recent:]) if recent > 0 else set()  # the latest large results: what the agent is working with
    cutoff = min(pinned) if pinned else len(items)
    rows: list[dict] = []
    cands: list[int] = []
    for i, it in enumerate(items):
        row = {"id": it["id"], "tool": it["tool"], "input": describe(it), "chars": len(it["text"]), "tokens": est_tokens(len(it["text"])),
               "error": it["error"], "action": "keep", "why": "", "p": None, "path": None}
        if len(it["text"]) < min_chars:
            row["why"] = "small"
        elif i in pinned:
            row["why"] = "recent"
            if i not in rules:
                cands.append(i)  # never touched, but judged: the block repeats only what the work still needs
        elif i in rules:
            row.update(action="move", why=rules[i])
        else:
            cands.append(i)
        rows.append(row)
    usage: dict = {"requests": 0, "cached": 0, "input_tokens": 0, **({"error": client_error} if client_error and cands else {})}
    if cands and client is not None and settings.is_local(client.base_url) and len(cands) > LOCAL_JUDGED:
        for i in cands[:-LOCAL_JUDGED]:  # the older ones stay whole and saved, unjudged
            rows[i]["why"] = rows[i]["why"] or "unjudged"
        cands = cands[-LOCAL_JUDGED:]
    if cands:
        if client is None:
            for i in cands:
                if i not in pinned:
                    rows[i]["why"] = "unjudged"
        else:
            from .grading import grade
            from .hooks import mask_secrets
            later = _mentioned_later([items[i] for i in cands], says)
            seen = [{"tool": items[i]["tool"], "input": mask_secrets(rows[i]["input"]),
                     "size": f"{items[i]['text'].count(chr(10)) + 1:,} lines, ~{rows[i]['tokens']:,} tokens",
                     "status": "error" if items[i]["error"] else "ok", "results_since": len(items) - 1 - i,
                     "mentioned_later_by_the_agent": later[n], "text": mask_secrets(_seen(items[i]["text"]))} for n, i in enumerate(cands)]
            try:
                g = grade(client, seen, KEEP_Q, _goal(says), chunk_chars=60_000)
                usage = {"requests": g.requests, "cached": g.cached, "input_tokens": g.input_tokens}
                for r in g.results:
                    i, p = cands[r["i"]], float(r["p"])
                    rows[i]["p"] = round(p, 3)
                    if i not in pinned:
                        rows[i].update(why="jev", action="keep" if p >= keep_at else "cut" if p >= cut_at else "move")
            except Exception as e:  # noqa: BLE001  what Jev could not judge stays whole
                usage["error"] = f"{type(e).__name__}: {str(e)[:160]}"
                for i in cands:
                    rows[i].update(p=None, action="keep", why="recent" if i in pinned else "unjudged")
    # A file the agent wrote: its text is on disk, so the call's own copy of it can go (the mod rebuilds the call).
    inputs = []
    for i, it in enumerate(items):
        content = it["input"].get("content") if it["tool"] == "Write" else None
        if isinstance(content, str) and len(content) >= min_chars and i < cutoff:
            inputs.append((i, len(content)))
    folder = _store_dir(tag) if apply else None
    saved = 0
    changes: list[dict] = []
    for i, (it, row) in enumerate(zip(items, rows)):
        if row["chars"] < min_chars:
            continue
        if folder is not None:
            saved += 1
            path = folder / f"{saved:03d}-{re.sub(r'[^A-Za-z0-9._-]', '_', it['tool'])[:40]}.txt"
            path.write_text(f"# {it['tool']} {row['input']}\n# {'error, ' if it['error'] else ''}{it['text'].count(chr(10)) + 1:,} lines, "
                            f"saved by jev compact before a compaction\n\n{it['text']}", encoding="utf-8", errors="replace")
            row["path"] = str(path)
        where = row["path"] or "<saved on apply>"
        if row["action"] == "move":
            changes.append({"id": it["id"], "kind": "result", "text": _moved(it, where, row["why"])})
        elif row["action"] == "cut":
            changes.append({"id": it["id"], "kind": "result", "text": _cut(it["text"], where)})
    changed = {c["id"] for c in changes}
    inputs = [(i, n) for i, n in inputs if items[i]["id"] not in changed]  # one change per call: the mod keys them by id
    for i, n in inputs:
        it = items[i]
        p = _path(it["input"])
        changes.append({"id": it["id"], "kind": "input", "input": {**it["input"], "content": f"{STUB} {n:,} characters written to {p}; read the file for its current text"}})
    after = {c["id"]: c for c in changes if c["kind"] == "result"}
    freed = sum(max(0, est_tokens(len(it["text"])) - est_tokens(len(after[it["id"]]["text"]))) for it in items if it["id"] in after)
    freed += sum(max(0, est_tokens(n) - 30) for _, n in inputs)
    counts = {"keep": 0, "cut": 0, "move": 0}
    for row in rows:
        if row["chars"] >= min_chars:
            counts[row["action"]] += 1
    rule_counts: dict[str, int] = {}
    for row in rows:
        if row["why"] in ("read-again", "edited-later", "ran-again", "retried"):
            rule_counts[row["why"]] = rule_counts.get(row["why"], 0) + 1
    plan = {"results": len(items), "decisions": rows, "changes": changes, "dir": str(folder) if folder else None,
            "stats": {"judged": sum(1 for r in rows if r["chars"] >= min_chars), "kept": counts["keep"], "cut": counts["cut"],
                      "moved": counts["move"], "recent": sum(1 for r in rows if r["why"] == "recent"),
                      "rules": rule_counts, "jev": sum(1 for r in rows if r["why"] == "jev"),
                      "inputs": len(inputs), "tokens": sum(r["tokens"] for r in rows if r["chars"] >= min_chars), "freed": freed, **usage}}
    restore, index, repeated = _restore(plan, items, budget, folder, min_chars, keep_at)
    plan["stats"]["repeated"] = repeated
    plan.update(restore=restore, index=index, restore_tokens=est_tokens(len(restore)))
    if folder is not None:
        prune_store()
    return plan


def _store_dir(tag: str) -> Path:
    name = (tag or "session:none").replace(":", "-")
    folder = settings.HOME / "compacted" / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def prune_store(days: float = STORE_DAYS) -> None:
    root = settings.HOME / "compacted"
    cutoff = time.time() - days * 86_400
    try:
        for d in root.iterdir():
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
    except OSError:
        pass


def _excerpt(text: str, path: str | None) -> str:
    if len(text) <= EXCERPT:
        return text
    return f"{text[:EXCERPT - 400]}\n… [cut here; the full text is at {path or 'the saved file'}] …\n{text[-400:]}"


def _restore(plan: dict, items: list[dict], budget: int, folder: Path | None, min_chars: int = MIN_CHARS,
             keep_at: float = KEEP_AT) -> tuple[str, str | None, int]:
    """The block that follows the compaction: the results Jev judged the work still needs, verbatim,
    then where every saved result is. Kept under `budget` tokens."""
    rows = plan["decisions"]
    saved = [(it, r) for it, r in zip(items, rows) if r["chars"] >= min_chars or r["path"]]
    if not saved:
        return "", None, 0
    index = None
    if folder is not None:
        lines = ["# Saved by jev compact before a compaction", "",
                 *[f"- {r['action']}: {r['tool']} {r['input']} → {os.path.basename(r['path'] or '')}" for _, r in saved]]
        index = str(folder / "index.md")
        Path(index).write_text("\n".join(lines) + "\n", encoding="utf-8", errors="replace")
    room = int(budget * settings.CHARS_PER_TOKEN)
    where = f"in {folder}, listed in index.md" if folder is not None else "on disk"
    lead = f"{STUB} Before this compaction, the {len(saved)} largest tool results were saved {where}."
    head = f"{lead} The ones the work still needs are repeated below, verbatim or cut; read any other from that folder when it is needed."
    out = [head]
    used = len(head)
    # What Jev judged still needed, the surest first, then the newest. A result nobody judged is not repeated.
    wanted = sorted((n for n, (_, r) in enumerate(saved) if r["p"] is not None and r["p"] >= keep_at), key=lambda n: (-saved[n][1]["p"], -n))
    listed = []
    for n in wanted:
        it, r = saved[n]
        block = f"\n\n## {r['tool']} {r['input']}\n{_excerpt(it['text'], r['path'])}"
        if used + len(block) > room * 0.75:
            continue
        out.append(block)
        used += len(block)
        listed.append(r["id"])
    if not listed:
        out[0] = f"{lead} Read any of them from that folder when the work needs it."
        used = len(out[0])
    rest = [r for _, r in reversed(saved) if r["id"] not in listed and r["path"]]
    if rest:
        title = "\n\nAlso saved, newest first:" if listed else "\n\nNewest first:"
        out.append(title)
        used += len(title)
        for r in rest:
            line = f"\n- {os.path.basename(r['path'])}: {r['tool']} {r['input'][:110]}"
            if used + len(line) > room:
                out.append(f"\n- … {len(rest) - rest.index(r)} more in the index")
                break
            out.append(line)
            used += len(line)
    return "".join(out), index, len(listed)


# ---------------------------------------------------------------- hand-over between the hooks and the mod

def _state_path(tag: str) -> Path:
    return settings.SESSIONS_DIR / f"{(tag or 'session:none').replace(':', '-')}.compact.json"


def save_state(tag: str, plan: dict, transcript: str | None, by: str) -> None:
    state = {"ts": time.time(), "by": by, "transcript": transcript, "restore": plan.get("restore") or "", "dir": plan.get("dir"),
             "index": plan.get("index"), "stats": plan.get("stats"), "consumed": False}
    try:
        settings.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        _state_path(tag).write_text(json.dumps(state))
    except OSError:
        pass


def recent_state(tag: str, max_age: float = 180) -> dict | None:
    """A plan saved for this session moments ago: the mod's, so the PreCompact hook need not judge again."""
    try:
        state = json.loads(_state_path(tag).read_text())
    except (OSError, ValueError):
        return None
    return state if time.time() - float(state.get("ts", 0)) < max_age and not state.get("consumed") else None


def take_restore(tag: str, max_age: float = 1800) -> str | None:
    """The block for the session that just compacted, once."""
    path = _state_path(tag)
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if state.get("consumed") or time.time() - float(state.get("ts", 0)) > max_age:
        return None
    state["consumed"] = True
    try:
        path.write_text(json.dumps(state))
    except OSError:
        pass
    return state.get("restore") or None


def log_fields(plan: dict) -> dict:
    s = plan["stats"]
    return {"judged": s["judged"], "kept": s["kept"], "cut": s["cut"], "moved": s["moved"], "by_rule": sum(s["rules"].values()),
            "by_jev": s["jev"], "inputs": s["inputs"], "tokens": s["tokens"], "freed": s["freed"], "restored": plan.get("restore_tokens", 0), "repeated": s.get("repeated", 0),
            "requests": s["requests"], "cached": s["cached"], "dir": plan.get("dir"), **({"jev_error": s["error"][:120]} if s.get("error") else {})}


def report(tail_bytes: int | None = 8_000_000) -> dict:
    """Past compactions from the hook log: what was judged and saved, and how often a saved result was read again."""
    rows = ledger.hook_rows(tail_bytes)
    comps = [r for r in rows if r.get("hook") == "compact" and "err" not in r]
    rereads = [r for r in rows if r.get("hook") == "compact-reread"]
    total = lambda k: sum(int(r.get(k) or 0) for r in comps)  # noqa: E731
    out_of_context = total("moved") + total("cut")
    return {"compactions": len(comps), "errors": sum(1 for r in rows if r.get("hook") == "compact" and "err" in r),
            "judged": total("judged"), "kept": total("kept"), "cut": total("cut"), "moved": total("moved"),
            "freed": total("freed"), "restored": total("restored"), "rereads": len(rereads),
            "reread_rate": (len(rereads) / out_of_context) if out_of_context else None,
            "pruned": sum(1 for r in comps if r.get("pruned")), "saved_usd": sum(float(r.get("saved_usd") or 0) for r in comps),
            "last": comps[-5:]}

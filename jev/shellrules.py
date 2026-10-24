"""What a shell command does, as far as plain rules can tell, so the guard can settle it without a model.

Three kinds are settled: a command that only reads (lists, prints, searches, shows git history), one
that writes only under a temporary folder (a scratchpad, $TMPDIR, /tmp), and an edit of files git
tracks inside the project, by `sed -i` or by a Python script on stdin that only reads and writes
files there (no deletion, no network, no other process, nothing outside the project). The last is
undone by `git checkout`. Anything the rules cannot read (a command substitution, an unknown
variable, a here-document left open, a program they do not know) goes to the model as before.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import tempfile
from pathlib import Path

READERS = {"ls", "cat", "head", "tail", "less", "more", "grep", "egrep", "fgrep", "rg", "wc", "sort", "uniq", "cut", "tr", "nl", "column",
           "jq", "stat", "du", "df", "file", "which", "type", "pwd", "date", "whoami", "id", "uname", "tree", "echo", "printf", "diff",
           "cmp", "basename", "dirname", "realpath", "readlink", "shasum", "sha256sum", "md5", "md5sum", "true", "test", "[", "sleep",
           "printenv", "ps", "pgrep", "lsof", "bat", "strings", "od", "comm", "fold", "rev", "seq"}
GIT_READERS = {"status", "log", "diff", "show", "branch", "remote", "rev-parse", "describe", "blame", "ls-files", "ls-tree", "cat-file",
               "grep", "shortlog", "reflog", "for-each-ref", "merge-base", "rev-list", "check-ignore", "config", "tag", "fetch"}
WRITERS = {"cat", "echo", "printf", "tee", "mkdir", "touch", "cp"}
PYTHONS = re.compile(r"^(?:.*/)?python(?:3(?:\.\d+)?)?$")
PY_SAFE_IMPORTS = {"re", "io", "os", "sys", "json", "pathlib", "textwrap", "collections", "itertools", "functools", "difflib", "string",
                   "math", "typing", "dataclasses", "datetime", "unicodedata", "codecs", "glob", "fnmatch"}
PY_FORBIDDEN = re.compile(
    r"\bos\s*\.\s*(remove|unlink|rmdir|removedirs|rename|renames|system|popen|exec\w*|spawn\w*|kill\w*|chmod|chown|symlink|link|truncate)\b"
    r"|\bshutil\b|\bsubprocess\b|\bsocket\b|\burllib\b|\brequests\b|\bhttp\b|\bftplib\b|\bsmtplib\b|\bparamiko\b|\bctypes\b"
    r"|\.\s*(unlink|rmdir|rename|symlink_to|hardlink_to|chmod|touch)\s*\(|__import__|\beval\s*\(|\bexec\s*\(|\bcompile\s*\(|\bimportlib\b"
    r"|\bhome\s*\(|expanduser|\benviron\b|getenv|chdir|\bcwd\s*\(|gettempdir")
PY_IMPORT = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+(?:\s*,\s*[\w.]+)*))", re.M)
PY_STRINGS = re.compile(r"""(?:'([^'\n]*)'|"([^"\n]*)")""")
HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_]\w*)\1")
ASSIGN = re.compile(r"^([A-Za-z_]\w*)=(.*)$", re.S)
VAR = re.compile(r"\$(?:\{([A-Za-z_]\w*)\}|([A-Za-z_]\w*))")
OPERATORS = {";", "&&", "||", "|", "&", "|&"}

# Commands that may change things outside a scratch folder: what the guard asks about when it cannot get a verdict.
DANGER = re.compile(r"\brm\s+-\w*[rf]|\bgit\s+(push|reset\s+--hard|clean\s+-\w*f|filter-branch|filter-repo|rebase|checkout\s+--\s|branch\s+-D)\b"
                    r"|--force\b|\b(drop|truncate)\s+(table|database|schema)\b|\bdelete\s+from\b|\bmkfs\b|\bdd\s+if=|\bsudo\b|\bssh\b|\bscp\b"
                    r"|\brsync\b[^\n]*--delete|\bkubectl\s+(delete|apply)\b|\bterraform\s+(apply|destroy)\b|\bchmod\s+-R\b|\bchown\s+-R\b"
                    r"|\bkill(all)?\s+-9\b|\bcurl\b[^\n]*-X\s*(POST|PUT|PATCH|DELETE)\b", re.I)


def dangerous(cmd: str) -> bool:
    return bool(DANGER.search(cmd or ""))


def scratch_roots() -> list[str]:
    roots = {"/tmp", "/private/tmp", os.path.realpath("/tmp"), os.path.realpath(tempfile.gettempdir()), tempfile.gettempdir().rstrip("/")}
    if os.environ.get("TMPDIR"):
        roots.add(os.environ["TMPDIR"].rstrip("/"))
        roots.add(os.path.realpath(os.environ["TMPDIR"]))
    return sorted(r for r in roots if r and r != "/")


def _under(path: str, roots: list[str]) -> bool:
    return any(path == r or path.startswith(r.rstrip("/") + "/") for r in roots)


def _split_heredocs(cmd: str) -> tuple[str, list[str]] | None:
    """The command line without its here-document bodies, and the bodies; None when one is left open."""
    lines = cmd.split("\n")
    head, bodies, i = [], [], 0
    while i < len(lines):
        line = lines[i]
        head.append(line)
        i += 1
        for m in HEREDOC.finditer(line):
            body = []
            while i < len(lines) and lines[i].strip() != m.group(2):
                body.append(lines[i])
                i += 1
            if i >= len(lines):
                return None
            i += 1
            bodies.append("\n".join(body))
    return "\n".join(head), bodies


def _tokens(head: str) -> list[str] | None:
    if "`" in head or "$(" in head or "<(" in head or ">(" in head:
        return None  # a command inside the command: the rules cannot read it
    lex = shlex.shlex(head.replace("\\\n", " ").replace("\n", " ; "), posix=True, punctuation_chars=";&|<>")
    lex.whitespace_split = True
    try:
        return list(lex)
    except ValueError:
        return None


def _expand(word: str, env: dict) -> str | None:
    def sub(m):
        name = m.group(1) or m.group(2)
        v = env.get(name, os.environ.get(name) if name in ("HOME", "TMPDIR", "PWD", "USER") else None)
        if v is None:
            raise KeyError(name)
        return v
    try:
        return os.path.expanduser(VAR.sub(sub, word))
    except KeyError:
        return None


def _abs(path: str, cwd: str) -> str:
    """Absolute and resolved: /var and /private/var are one place on macOS, and git answers with the resolved one."""
    return os.path.realpath(path if os.path.isabs(path) else os.path.join(cwd, path))


def _git_root(cwd: str) -> str | None:
    try:
        out = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=3)
        return os.path.realpath(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None
    except (OSError, subprocess.SubprocessError):
        return None


def _tracked(root: str, paths: list[str]) -> bool:
    """Every path that exists is tracked by git (a new file is fine: nothing is lost when it is written)."""
    existing = [os.path.relpath(p, root) for p in paths if os.path.exists(p)]
    if not existing:
        return True
    try:
        out = subprocess.run(["git", "-C", root, "ls-files", "--error-unmatch", "--", *existing], capture_output=True, text=True, timeout=3)
        return out.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _python_edit(script: str, cwd: str, root: str, scratch: list[str]) -> list[str] | None:
    """The files a Python script on stdin may write, when it only reads and writes files: None otherwise."""
    if PY_FORBIDDEN.search(script):
        return None
    for m in PY_IMPORT.finditer(script):
        names = [m.group(1)] if m.group(1) else [n.strip() for n in m.group(2).split(",")]
        if any(n.split(".")[0] not in PY_SAFE_IMPORTS for n in names):
            return None
    paths = []
    for m in PY_STRINGS.finditer(script):
        s = m.group(1) if m.group(1) is not None else m.group(2)
        if any(ch.isspace() for ch in s) or ("/" not in s and not re.fullmatch(r"[\w.-]+\.\w{1,8}", s)):
            continue  # not a path: prose, a pattern, a fragment of code
        if s.startswith(("http://", "https://")):
            return None
        p = _abs(os.path.expanduser(s), cwd)
        if ".." in Path(s).parts or not (_under(p, [root]) or _under(p, scratch)):
            return None
        if _under(p, [root]):
            paths.append(p)
    return paths


def settle(cmd: str, cwd: str) -> str | None:
    """"read-only", "scratch" or "project-edit" when the rules can tell what the command does and it is one of those;
    None for anything else, which goes to the model."""
    if not cmd or not cwd:
        return None
    split = _split_heredocs(cmd)
    if split is None:
        return None
    head, bodies = split
    tokens = _tokens(head)
    if not tokens:
        return None
    scratch = scratch_roots()
    env: dict[str, str] = {}
    here = cwd
    kinds: set[str] = set()
    body_iter = iter(bodies)
    segment: list[str] = []
    root: str | None = None

    def judge(seg: list[str]) -> str | None:
        nonlocal here, root
        words, targets, heredoc = [], [], None
        i = 0
        while i < len(seg):
            t = seg[i]
            if t in (">", ">>", ">|", "&>", "&>>"):
                if i + 1 >= len(seg):
                    return None
                targets.append(seg[i + 1])
                i += 2
                continue
            if t in (">&", "<&"):
                i += 2  # 2>&1
                continue
            if t == "<":
                i += 2
                continue
            if t in ("<<", "<<-"):
                heredoc = next(body_iter, None)
                i += 2
                continue
            if t.isdigit() and i + 1 < len(seg) and seg[i + 1].startswith((">", "<")):
                i += 1  # a file descriptor number before a redirection
                continue
            words.append(t)
            i += 1
        while words and ASSIGN.match(words[0]):
            name, value = ASSIGN.match(words[0]).groups()
            v = _expand(value, env)
            if v is None:
                return None
            env[name] = v
            words = words[1:]
        # A word the rules cannot expand only matters where a path does: what runs, where to, what gets written.
        expanded = [_expand(w, env) for w in words]
        outs = [_expand(t, env) for t in targets]
        if (expanded and expanded[0] is None) or any(o is None for o in outs):
            return None
        written = [_abs(o, here) for o in outs if o != "/dev/null"]
        if not expanded:
            return "read-only" if not written else ("scratch" if all(_under(w, scratch) for w in written) else None)
        prog = os.path.basename(expanded[0])
        args = [a if a is not None else "\0" for a in expanded[1:]]  # "\0": a word left as written (a pattern, say)
        in_place = prog == "sed" and any(a.startswith("-i") for a in args)
        needs_paths = prog in ("cd", "pushd", "export") or in_place or prog in WRITERS - READERS or PYTHONS.match(prog)
        if needs_paths and "\0" in args:
            return None
        if prog in ("cd", "pushd"):
            if len(args) > 1:
                return None
            here = _abs(args[0], here) if args else os.path.expanduser("~")
            return "read-only"
        if prog == "export":
            for a in args:
                if ASSIGN.match(a):
                    env[ASSIGN.match(a).group(1)] = ASSIGN.match(a).group(2)
            return "read-only"
        if written and not all(_under(w, scratch) for w in written):
            return None
        reads = None
        if prog == "git":
            rest = args
            while rest[:1] == ["-C"] and len(rest) > 1:
                rest = rest[2:]
            sub = rest[0] if rest else ""
            if sub in GIT_READERS and not (sub in ("branch", "tag", "config", "remote", "stash") and len(rest) > 1
                                           and not any(r.startswith(("-l", "--list", "-v", "-a", "--get", "--show")) for r in rest[1:3])):
                reads = "read-only"
            elif sub == "stash" and rest[1:2] in (["list"], ["show"]):
                reads = "read-only"
        elif prog == "sed" and "-i" not in args and not any(a.startswith("-i") for a in args):
            reads = "read-only" if "-n" in args or not any(a.startswith(("w ", "W ")) for a in args) else None
        elif prog == "find" and not {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprintf", "-fls"} & set(args):
            reads = "read-only"
        elif prog in READERS and not (prog in ("sort", "tree") and any(a.startswith(("-o", "--output")) for a in args)) \
                and not (prog == "uniq" and len([a for a in args if not a.startswith("-")]) > 1):
            reads = "read-only"  # sort -o, tree -o and uniq IN OUT write a file
        if reads:
            return "scratch" if written else reads
        if prog in WRITERS:
            files = [a for a in args if not a.startswith("-")]
            dests = files if prog in ("tee", "mkdir", "touch") else files[-1:] if prog == "cp" else []
            if all(_under(_abs(d, here), scratch) for d in dests):
                return "scratch"
            return None
        if prog == "sed":  # sed -i: an edit; the files must be git-tracked inside the project
            files = [_abs(a, here) for a in args[1:] if not a.startswith("-") and a != "''" and a != ""]
            files = [f for f in files if os.path.exists(f)]
            root = root or _git_root(here)
            if root and files and all(_under(f, [root]) for f in files) and _tracked(root, files):
                return "project-edit"
            return None
        if PYTHONS.match(prog) and heredoc is not None and [a for a in args if a not in ("-I", "-u", "-B")] == ["-"]:
            root = root or _git_root(here)
            if not root:
                return None
            paths = _python_edit(heredoc, here, root, scratch)
            if paths is not None and _tracked(root, paths):
                return "project-edit"
            return None
        return None

    for t in tokens + [";"]:
        if t in OPERATORS:
            if segment:
                kind = judge(segment)
                if kind is None:
                    return None
                kinds.add(kind)
            segment = []
        else:
            segment.append(t)
    if not kinds:
        return None
    for kind in ("project-edit", "scratch", "read-only"):
        if kind in kinds:
            return kind
    return None

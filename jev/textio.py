"""Reading things: stdin, files, trees, diffs, test files, runner output. No network here."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from .errors import UsageError
from .settings import CHARS_PER_TOKEN

SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "vendor", "__pycache__", ".venv", "venv", "dist", "build",
             ".next", ".nuxt", "target", ".cache", ".idea", ".vscode", "coverage"}
SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".svgz", ".pdf", ".zip", ".gz", ".tgz", ".bz2", ".xz",
            ".7z", ".rar", ".jar", ".war", ".class", ".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe", ".bin", ".o", ".a",
            ".woff", ".woff2", ".ttf", ".otf", ".eot", ".mp3", ".mp4", ".mov", ".avi", ".wav", ".ogg", ".flac", ".sqlite",
            ".db", ".lock", ".map", ".min.js", ".min.css", ".DS_Store"}

DEF_RE = re.compile(
    r"^[ \t]*(?:(?:public|private|protected|static|final|abstract|async|export|default|declare)[ \t]+)*"
    r"(?:def|class|function|fn|func|interface|trait|enum|struct|impl)[ \t]+([A-Za-z_][\w$]*)", re.M)

SPLIT_PRESETS = {
    "pytest": r"^(?:FAILED|ERROR) ",      # the short summary of `pytest -rA` / `-rfE`
    "pytest-long": r"^_{3,} .+ _{3,}$",   # long-form failure headers
    "phpunit": r"^\d+\) ",
    "jest": r"^\s*● ",
    "tap": r"^not ok ",
    "go": r"^--- FAIL: ",
    "blank": None,                        # blank-line separated blocks
    "line": None,                         # one item per line (JSONL with --text-key)
}

TEST_FILE_RE = re.compile(
    r"(^|/)(tests?|__tests__|specs?|testing)/|"
    r"(^|/)(test_[^/]+\.py|[^/]+_test\.(py|go|rb|rs|js|ts|php)|[^/]+\.(test|spec)\.(js|jsx|ts|tsx|mjs|cjs)|"
    r"[^/]+Tests?\.(php|cs|java|kt|swift|scala)|[^/]+_spec\.rb)$", re.I)
TEST_NAME_RE = re.compile(
    r"^\s*(?:(?:async\s+)?def\s+(test\w*)|(?:public\s+)?function\s+(test\w*)|(?:it|test|describe)\s*\(\s*['\"`](.{1,80}?)['\"`]|"
    r"func\s+(Test\w*)|fn\s+(test\w*)|(?:public\s+)?void\s+(test\w*)\s*\()", re.M)
NON_TEST_STEM = re.compile(r"^(test_|tests?_)|(_test|_tests|test|tests|_spec|spec)$", re.I)


def est_tokens(chars: int) -> int:
    return max(1, round(chars / CHARS_PER_TOKEN))


def implicit_stdin(timeout: float = 1.5) -> str | None:
    """Read stdin only when it is a pipe with data ready: a harness that leaves stdin open with no
    writer would otherwise hang the command. `-` forces a blocking read."""
    if sys.stdin is None or sys.stdin.isatty():
        return None
    try:
        import select
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if not ready:
            return None
    except (OSError, ValueError):
        pass
    return sys.stdin.read()


def read_source(spec: str | None, what: str = "input") -> str:
    """A file path, or '-' / None for stdin."""
    if not spec or spec == "-":
        return sys.stdin.read()
    p = Path(spec).expanduser()
    try:
        return p.read_text()
    except OSError:
        raise UsageError(f"{what} not found: {p}")


def read_head(path, limit: int) -> str | None:
    """The first `limit` bytes as text, or None for a binary or unreadable file."""
    try:
        with open(path, "rb") as f:
            raw = f.read(limit)
    except OSError:
        return None
    if b"\x00" in raw[:2000]:
        return None
    return raw.decode("utf-8", "replace")


def walk(paths, skip_dirs=SKIP_DIRS) -> list[Path]:
    """Files under the given paths, sorted, hidden and binary-looking ones skipped. A path given
    explicitly is always included."""
    out: list[Path] = []

    def rec(d: str) -> None:
        try:
            with os.scandir(d) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            return
        for e in entries:
            name = e.name
            if name.startswith("."):
                continue
            try:
                if e.is_dir(follow_symlinks=False):
                    if name not in skip_dirs:
                        rec(e.path)
                elif e.is_file(follow_symlinks=False) and os.path.splitext(name)[1].lower() not in SKIP_EXT:
                    out.append(Path(e.path))
            except OSError:
                continue

    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_dir():
            rec(str(p))
        elif p.exists():
            out.append(p)
        else:
            print(f"jev: no such path: {raw}", file=sys.stderr)
    return out


def split_functions(text: str) -> list[tuple[int, str | None, str]]:
    """(line, name, source) per definition found by regex: a filter, not a parser."""
    starts = [(m.start(), m.group(1)) for m in DEF_RE.finditer(text)]
    if not starts:
        return [(1, None, text)]
    chunks = []
    for k, (pos, name) in enumerate(starts):
        end = starts[k + 1][0] if k + 1 < len(starts) else len(text)
        chunks.append((text.count("\n", 0, pos) + 1, name, text[pos:end]))
    return chunks


def split_items(raw: str, mode: str | None, text_key: str = "text") -> list[str]:
    """Cut a file into items: one per line (JSONL rows contribute `text_key`), blank-separated
    blocks, or wherever a preset's or a regex's first line matches."""
    if mode in (None, "line"):
        items = []
        for ln in raw.splitlines():
            if not ln.strip():
                continue
            if ln.lstrip().startswith("{"):
                try:
                    obj = json.loads(ln)
                    if isinstance(obj, dict) and text_key in obj:
                        items.append(str(obj[text_key]))
                        continue
                except ValueError:
                    pass
            items.append(ln)
        return items
    if mode == "blank":
        return [b.strip() for b in re.split(r"\n\s*\n", raw) if b.strip()]
    rx = re.compile(SPLIT_PRESETS.get(mode) or mode, re.M)
    starts = [m.start() for m in rx.finditer(raw)]
    if not starts:
        return [raw.strip()] if raw.strip() else []
    bounds = zip(starts, starts[1:] + [len(raw)])
    return [c for c in (raw[a:b].strip() for a, b in bounds) if c]


def first_line(text: str, width: int) -> str:
    """The first non-empty line without a runner's decoration (pytest pads headers with underscores)."""
    for x in text.splitlines():
        s = x.strip().strip("_=-— ").strip()
        if s:
            return s if len(s) <= width else s[: width - 1] + "…"
    return ""


def git_diff(repo: str, staged: bool = False, ref: str | None = None, diff_file: str | None = None) -> tuple[str, list[str]]:
    """(raw diff, changed paths). From a file / stdin, or `git diff HEAD` (falling back to the work tree)."""
    if diff_file:
        raw = read_source(diff_file, "diff")
    else:
        import subprocess
        base = ["git", "-C", repo, "diff", "--no-color", "--no-ext-diff"]
        cmd = base + (["--staged"] if staged else [ref] if ref else ["HEAD"])
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 and not staged and not ref:
            r = subprocess.run(base, capture_output=True, text=True)
        if r.returncode != 0:
            raise UsageError(f"git diff failed: {r.stderr.strip()[:200]}")
        raw = r.stdout
    return raw, re.findall(r"^\+\+\+ b/(.+)$", raw, re.M)


def compact_diff(raw: str, limit: int) -> str:
    """Headers, hunk markers and changed lines first; context lines only while there is room."""
    keep, ctx = [], []
    for ln in raw.splitlines():
        if ln.startswith(("diff --git", "+++", "---", "@@")) or (ln[:1] in "+-"):
            keep.append(ln)
        else:
            ctx.append(ln)
    text = "\n".join(keep)
    if len(text) > limit:
        return text[: limit - 40] + "\n… [diff truncated to fit the request]"
    full = "\n".join(keep + ctx)
    return full if len(full) <= limit else text


def split_hunks(raw: str) -> list[dict]:
    hunks: list[dict] = []
    path, cur = None, None
    for ln in raw.splitlines():
        if ln.startswith("diff --git"):
            m = re.search(r" b/(.+)$", ln)
            path = m.group(1) if m else ln[11:]
            cur = None
        elif ln.startswith("@@"):
            m = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)", ln)
            cur = {"file": path or "?", "line": int(m.group(1)) if m else 0, "text": ln, "added": 0, "removed": 0, "summary": ""}
            hunks.append(cur)
        elif cur is not None and not ln.startswith(("+++", "---")):
            cur["text"] += "\n" + ln
            if ln.startswith("+"):
                cur["added"] += 1
                cur["summary"] = cur["summary"] or ln[1:].strip()
            elif ln.startswith("-"):
                cur["removed"] += 1
                cur["summary"] = cur["summary"] or ln[1:].strip()
    return [h for h in hunks if h["added"] or h["removed"]]


def test_names(text: str, cap: int = 25) -> list[str]:
    names = []
    for m in TEST_NAME_RE.finditer(text):
        nm = next((g for g in m.groups() if g), None)
        if nm:
            names.append(nm)
            if len(names) >= cap:
                break
    return names

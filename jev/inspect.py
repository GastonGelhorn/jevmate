"""Instruction files that other people wrote and your agent will obey: skills, plugins, agents,
commands, hooks. Before a session leans on them, read them with the same two questions the fetch
screen asks: does this tell an agent to do something the person would not want, and does it plant
a phrase or a link the agent must repeat. Only new or changed files are sent; a hash of each file's
content is kept, so a session start costs nothing when nothing changed.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from . import settings

HARM_Q = ("Does `candidate` instruct an agent to do something the person running it would not want: send data, files or "
          "secrets elsewhere; run hidden, encoded or obfuscated commands; ignore or override the person's own instructions; "
          "hide what it does; or act on an urgency or authority nobody gave it?")
HARM_T = "an instruction to exfiltrate, to run something concealed, to override the person, to conceal actions, or an assumed authority"
HARM_F = "ordinary guidance for a task: what to read, how to format, which commands a tool legitimately needs, warnings for humans"
MARK_Q = "Does `candidate` tell an agent to insert a specific phrase, marker, credit, link or address into its outputs or messages regardless of the task?"
MARK_T = "a fixed string or link the agent must include in replies, commits, files or messages whatever it is doing"
MARK_F = "formatting rules, templates for the task's own output, or examples that are clearly examples"

NAMES = {"SKILL.md", "CLAUDE.md", "AGENTS.md", "hooks.json", ".mcp.json", "plugin.json"}
SUFFIXES = {".md"}
MAX_CHARS = 60_000


def roots(project: str | None = None) -> list[Path]:
    home = Path.home()
    out = [home / ".claude" / "skills", home / ".claude" / "plugins" / "cache", home / ".claude" / "agents", home / ".claude" / "commands",
           home / ".codex" / "skills", home / ".agents" / "skills", home / ".config" / "opencode" / "skills"]
    if project:
        out += [Path(project) / ".claude", Path(project) / "AGENTS.md", Path(project) / "CLAUDE.md"]
    return [r for r in out if r.exists()]


def files(paths) -> list[Path]:
    out: list[Path] = []
    for root in paths:
        root = Path(root)
        if root.is_file():
            out.append(root)
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in ("node_modules", ".git", "__pycache__", "evals", "tests", "results")]
            for f in filenames:
                if f in NAMES or (Path(f).suffix in SUFFIXES and Path(dirpath).name in ("skills", "agents", "commands") or f == "SKILL.md"):
                    out.append(Path(dirpath) / f)
    return sorted(set(out))


def _cache_path() -> Path:
    return settings.HOME / "inspect.json"


def load_cache() -> dict:
    try:
        return json.loads(_cache_path().read_text())
    except (OSError, ValueError):
        return {}


def save_cache(cache: dict) -> None:
    try:
        settings.HOME.mkdir(parents=True, exist_ok=True)
        _cache_path().write_text(json.dumps(cache))
    except OSError:
        pass


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def inspect(client, paths, *, only_changed: bool = True, bar: float = 0.60) -> tuple[list[dict], int]:
    """Grade every instruction file under `paths`. Returns (rows sorted by risk, files sent). Rows for
    unchanged files come from the cache."""
    from .grading import grade
    from .hooks import mask_secrets
    cache = load_cache() if only_changed else {}
    rows, todo, texts = [], [], []
    for f in files(paths):
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        key = str(f)
        sha = digest(text)
        hit = cache.get(key)
        if only_changed and hit and hit.get("sha") == sha:
            rows.append({**hit, "path": key, "cached": True})
            continue
        todo.append((key, sha))
        texts.append({"path": f.name, "text": mask_secrets(text[:MAX_CHARS])})
    if todo:
        h = grade(client, texts, json.dumps({"question": HARM_Q, "criteria": {"true": HARM_T, "false": HARM_F}}), "the agent's installed instructions")
        m = grade(client, texts, json.dumps({"question": MARK_Q, "criteria": {"true": MARK_T, "false": MARK_F}}), "the agent's installed instructions")
        ph = {r["i"]: r["p"] for r in h.results}
        pm = {r["i"]: r["p"] for r in m.results}
        for i, (key, sha) in enumerate(todo):
            row = {"sha": sha, "harm": round(ph[i], 3), "marker": round(pm[i], 3), "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
            cache[key] = row
            rows.append({**row, "path": key, "cached": False})
        save_cache(cache)
    for r in rows:
        r["flag"] = max(r["harm"], r["marker"]) >= bar
    rows.sort(key=lambda r: -max(r["harm"], r["marker"]))
    return rows, len(todo)

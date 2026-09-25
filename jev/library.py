"""Saved questions: a phrasing, its threshold and band, the model it was tuned on.

`jev tune` measures a question once; a saved question makes that measurement reusable from any
command (`jev yes --q refund`, `jev rank --q refund`) and from Python (`decide_many(rows,
library.load("refund"))`). A project keeps its own under `.jev/questions/` so it travels with
the repository; personal ones live in the jev home.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import settings
from .errors import UsageError

NAME_RE = re.compile(r"^[A-Za-z0-9][\w.-]{0,63}$")
FIELDS = ("question", "true", "false", "threshold", "band", "model", "query", "context", "kind", "measured", "note")


def _check(name: str) -> str:
    if not NAME_RE.match(name or ""):
        raise UsageError(f"a question name is letters, digits, `_`, `-` or `.` (got {name!r})")
    return name


def path_for(name: str, project: bool = False) -> Path:
    _check(name)
    dirs = settings.questions_dirs()
    if project:
        return dirs[0] / f"{name}.json"
    for d in dirs:
        if (d / f"{name}.json").exists():
            return d / f"{name}.json"
    return dirs[-1] / f"{name}.json"


def load(name: str) -> dict:
    p = path_for(name)
    try:
        spec = json.loads(p.read_text())
    except FileNotFoundError:
        raise UsageError(f"no saved question {name!r} (jev q list)")
    except ValueError as e:
        raise UsageError(f"{p}: invalid JSON: {e}")
    if not isinstance(spec, dict) or not spec.get("question"):
        raise UsageError(f"{p}: a saved question needs at least a `question`")
    spec["name"] = name
    spec["path"] = str(p)
    return spec


def save(name: str, spec: dict, project: bool = False) -> Path:
    _check(name)
    clean = {k: spec[k] for k in FIELDS if spec.get(k) is not None}
    if not clean.get("question"):
        raise UsageError("a saved question needs a `question`")
    if "band" in clean:
        lo, hi = clean["band"]
        clean["band"] = [float(lo), float(hi)]
    p = path_for(name, project)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(clean, indent=2, ensure_ascii=False) + "\n")
    return p


def remove(name: str) -> Path:
    p = path_for(name)
    try:
        p.unlink()
    except FileNotFoundError:
        raise UsageError(f"no saved question {name!r}")
    return p


def list_all() -> list[dict]:
    out, seen = [], set()
    for scope, d in zip(("project", "home"), settings.questions_dirs()):
        try:
            files = sorted(d.glob("*.json"))
        except OSError:
            continue
        for f in files:
            if f.stem in seen:
                continue
            try:
                spec = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(spec, dict) and spec.get("question"):
                seen.add(f.stem)
                out.append({"name": f.stem, "scope": scope, "path": str(f), **spec})
    return out


PACKS_DIR = Path(__file__).resolve().parent / "packs"


def packs() -> list[dict]:
    out = []
    for f in sorted(PACKS_DIR.glob("*.json")):
        try:
            pack = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(pack, dict) and isinstance(pack.get("questions"), dict):
            out.append({"name": pack.get("name") or f.stem, "description": pack.get("description", ""), "questions": pack["questions"], "path": str(f)})
    return out


def install_pack(name: str, project: bool = False, force: bool = False) -> tuple[list[str], list[str]]:
    """Copy a shipped pack's questions into the library. Returns (installed, skipped because present)."""
    pack = next((p for p in packs() if p["name"] == name), None)
    if pack is None:
        raise UsageError(f"no pack {name!r}; jev q packs lists them")
    installed, skipped = [], []
    for qname, spec in pack["questions"].items():
        target = path_for(qname, project)
        if target.exists() and not force:
            skipped.append(qname)
            continue
        save(qname, dict(spec, note=spec.get("note") or (spec.get("measured") or {}).get("note")), project)
        installed.append(qname)
    return installed, skipped


def apply(spec: dict, args, *, question_attr: str = "instructions") -> None:
    """Fill the CLI's arguments from a saved question wherever the person gave nothing."""
    if getattr(args, question_attr, None) in (None, [], ""):
        setattr(args, question_attr, spec["question"])
    for attr, key in (("true", "true"), ("false", "false"), ("query", "query"), ("context", "context")):
        if hasattr(args, attr) and getattr(args, attr) is None and spec.get(key) is not None:
            setattr(args, attr, spec[key])
    if hasattr(args, "threshold") and getattr(args, "threshold") == 0.5 and spec.get("threshold") is not None:
        args.threshold = float(spec["threshold"])
    if hasattr(args, "min") and getattr(args, "min") is None and spec.get("threshold") is not None and not spec.get("band"):
        args.min = float(spec["threshold"])
    for attr in ("band", "abstain"):
        if hasattr(args, attr) and getattr(args, attr) is None and spec.get("band"):
            setattr(args, attr, [float(x) for x in spec["band"]])
    if hasattr(args, "model") and getattr(args, "model") is None and spec.get("model"):
        args.model = spec["model"]

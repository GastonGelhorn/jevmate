"""Claude Code's auto-compact window: the context size at which it compacts on its own.

Every request re-reads the whole conversation, so the size the context reaches before a compaction is
most of what a long session costs. `jev compact --simulate` replays recent sessions at smaller
windows; on a week of coding sessions 400K kept most of the saving with a few compactions a day, and
Claude Code computes the summary ahead of time, so a compaction at the window does not hold the work.
jev writes `autoCompactWindow` into Claude Code's user settings when it is not set, and remembers the
value it wrote, so a value the person set themselves is never changed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import settings

DEFAULT = 400_000
MINIMUM = 100_000
KEY = "autoCompactWindow"


def settings_path() -> Path:
    return Path(os.environ.get("JEV_CLAUDE_SETTINGS") or Path.home() / ".claude" / "settings.json")


def parse(value) -> int | None:
    """400000, "400K", "0.4M" → tokens; "off" → None."""
    s = str(value).strip().lower().replace(",", "").replace("_", "")
    if s in ("off", "none", "false", "0", "no"):
        return None
    scale = 1_000 if s.endswith("k") else 1_000_000 if s.endswith("m") else 1
    try:
        return max(MINIMUM, int(float(s.rstrip("km")) * scale))
    except ValueError:
        return DEFAULT


def wanted() -> int | None:
    """The window jev keeps: JEV_COMPACT_WINDOW, the plugin's option, `jev config set compact_window`, else 400K."""
    for v in (os.environ.get("JEV_COMPACT_WINDOW"), os.environ.get("CLAUDE_PLUGIN_OPTION_COMPACT_WINDOW"),
              settings.config().get("compact_window")):
        if v not in (None, ""):
            return parse(v)
    return DEFAULT


def _read(path: Path) -> dict | None:
    try:
        cfg = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError):
        return None
    return cfg if isinstance(cfg, dict) else None


def _write(path: Path, cfg: dict) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp-jev")
        tmp.write_text(json.dumps(cfg, indent=2) + "\n")
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def _remember(value: int | None) -> None:
    cfg = dict(settings.config())
    if value is None:
        cfg.pop("compact_window_written", None)
    else:
        cfg["compact_window_written"] = value
    settings.save_config(cfg)


def current(path: Path | None = None) -> tuple[int | None, str]:
    """Claude Code's window now, and whose it is: "jev", "yours" or "default"."""
    cfg = _read(path or settings_path()) or {}
    have = cfg.get(KEY)
    if have is None:
        return None, "default"
    return have, "jev" if have == settings.config().get("compact_window_written") else "yours"


def apply(path: Path | None = None) -> str:
    """Bring Claude Code's setting in line with jev's: write it when absent, update or remove one jev wrote, leave
    the person's own. Returns "set", "updated", "removed", "kept", "unchanged" or "skipped" (settings unreadable)."""
    path = path or settings_path()
    want = wanted()
    cfg = _read(path)
    if cfg is None:
        return "skipped"
    have = cfg.get(KEY)
    mine = settings.config().get("compact_window_written")
    if have is None:
        if want is None:
            return "unchanged"
        cfg[KEY] = want
        if not _write(path, cfg):
            return "skipped"
        _remember(want)
        return "set"
    if mine is None or have != mine:
        return "kept"
    if want == have:
        return "unchanged"
    if want is None:
        cfg.pop(KEY, None)
    else:
        cfg[KEY] = want
    if not _write(path, cfg):
        return "skipped"
    _remember(want)
    return "removed" if want is None else "updated"

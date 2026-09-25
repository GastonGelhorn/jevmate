"""Every answer is kept, keyed by the exact request bytes.

Identical requests do not return identical numbers: six sent one after another to one concrete
model came back 0.88 0.89 0.90 0.89 0.89 0.88. A re-run buys a fresh sample of that jitter and
nothing else, so a stored answer is as good as a new one. Iterating on a question (`tune`) is
the same rows again and again; that is where the cache pays. Aliases such as `jev-latest` can
move underneath a stored answer, which is what the ttl and `jev cache clear` are for.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time

from . import settings

SUFFIX = ".json"


def enabled() -> bool:
    if not settings.RUNTIME.cache or os.environ.get("JEV_NO_CACHE"):
        return False
    return str(settings.config().get("cache", "on")).lower() not in ("off", "false", "0", "no")


def ttl_days() -> float:
    v = settings.config().get("cache_ttl_days")
    return float(v) if v is not None else settings.CACHE_TTL_DAYS


def key_for(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def get(key: str) -> dict | None:
    path = settings.CACHE_DIR / (key + SUFFIX)
    try:
        if time.time() - path.stat().st_mtime > ttl_days() * 86400:
            return None
        with open(path, "rb") as f:
            return json.loads(f.read())
    except (OSError, ValueError):
        return None


def put(key: str, resp: dict) -> None:
    try:
        settings.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = settings.CACHE_DIR / (key + SUFFIX)
        tmp = path.with_name(f".{key[:16]}.{os.getpid()}.{threading.get_ident()}.tmp")
        with open(tmp, "w") as f:
            json.dump(resp, f)
        os.replace(tmp, path)  # atomic: rank, batch and tune write from several threads
    except OSError:
        pass


def _entries():
    try:
        with os.scandir(settings.CACHE_DIR) as it:
            for e in it:
                if e.name.endswith(SUFFIX) and e.is_file():
                    yield e
    except OSError:
        return


def stats() -> dict:
    n = size = stale = 0
    horizon = time.time() - ttl_days() * 86400
    for e in _entries():
        st = e.stat()
        n += 1
        size += st.st_size
        stale += st.st_mtime < horizon
    return {"dir": str(settings.CACHE_DIR), "enabled": enabled(), "ttl_days": ttl_days(), "entries": n, "bytes": size, "stale": stale}


def clear(stale_only: bool = False) -> int:
    horizon = time.time() - ttl_days() * 86400
    removed = 0
    for e in list(_entries()):
        if stale_only and e.stat().st_mtime >= horizon:
            continue
        try:
            os.unlink(e.path)
            removed += 1
        except OSError:
            pass
    return removed

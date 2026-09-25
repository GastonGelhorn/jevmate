"""HTTP for the API: one connection per thread, kept alive across requests.

A fresh TCP + TLS handshake per request is a large share of the ~250 ms a decision costs, and
`rank` over twenty chunks on six workers paid twenty of them. Each worker thread now keeps one
connection and reuses it; a connection the server has meanwhile closed is reopened once,
transparently. Proxies come from HTTPS_PROXY / https_proxy (NO_PROXY honoured by host suffix).
"""

from __future__ import annotations

import http.client
import os
import ssl
import threading
import time
from urllib.parse import urlsplit

_RECONNECT = (http.client.RemoteDisconnected, http.client.BadStatusLine, http.client.CannotSendRequest,
              http.client.ResponseNotReady, ConnectionResetError, ConnectionAbortedError, BrokenPipeError, ssl.SSLEOFError)


class Throttle:
    """Process-wide minimum spacing between requests, so the published ceiling is never found by
    collecting 429s and paying for them in backoff. Retries go through it too."""

    def __init__(self, rpm: int):
        self.min_interval = 60.0 / rpm if rpm > 0 else 0.0
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next_at)
            self._next_at = slot + self.min_interval
        if slot > now:
            time.sleep(slot - now)


def _proxy_for(host: str) -> tuple[str, int] | None:
    url = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if not url:
        return None
    for skip in (os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or "").split(","):
        skip = skip.strip().lstrip(".")
        if skip and (skip == "*" or host == skip or host.endswith("." + skip)):
            return None
    u = urlsplit(url if "://" in url else "http://" + url)
    return u.hostname or "", u.port or (443 if u.scheme == "https" else 80)


class Transport:
    def __init__(self, base_url: str, timeout: float = 30.0):
        u = urlsplit(base_url)
        self.secure = u.scheme != "http"
        self.host = u.hostname or ""
        self.port = u.port or (443 if self.secure else 80)
        self.prefix = u.path.rstrip("/")
        self.timeout = timeout
        self._local = threading.local()
        self._ctx = ssl.create_default_context() if self.secure else None
        self._proxy = _proxy_for(self.host)

    def _open(self):
        if self._proxy:
            ph, pp = self._proxy
            conn = http.client.HTTPSConnection(ph, pp, timeout=self.timeout, context=self._ctx) if self.secure \
                else http.client.HTTPConnection(ph, pp, timeout=self.timeout)
            conn.set_tunnel(self.host, self.port)
            return conn
        if self.secure:
            return http.client.HTTPSConnection(self.host, self.port, timeout=self.timeout, context=self._ctx)
        return http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)

    def _drop(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass
            self._local.conn = None

    def request(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None) -> tuple[int, dict, bytes]:
        """(status, lower-cased headers, body). Raises OSError subclasses on transport failure."""
        for last in (False, True):
            conn = getattr(self._local, "conn", None)
            if conn is None:
                conn = self._local.conn = self._open()
            try:
                conn.request(method, self.prefix + path, body=body, headers=headers or {})
                resp = conn.getresponse()
                data = resp.read()
            except _RECONNECT:
                self._drop()
                if last:
                    raise
                continue
            except OSError:
                self._drop()
                raise
            hdrs = {k.lower(): v for k, v in resp.getheaders()}
            if resp.will_close or hdrs.get("connection", "").lower() == "close":
                self._drop()
            return resp.status, hdrs, data
        raise ConnectionError("unreachable")

    def close(self) -> None:
        self._drop()

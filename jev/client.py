"""The client: one call, `ask`, with validation, dry run, cache, retries and the ledger around it."""

from __future__ import annotations

import json
import time

from . import cache, ledger, settings
from ._version import VERSION
from .errors import AuthError, DryRun, JevError, NetworkError, UsageError
from .questions import validate

_THROTTLE = None


def _throttle():
    global _THROTTLE
    if _THROTTLE is None:
        from .transport import Throttle
        _THROTTLE = Throttle(settings.rpm_limit())
    return _THROTTLE


def _backoff(attempt: int) -> float:
    return min(8.0, 0.5 * 2 ** (attempt - 1))


def serialize(body: dict) -> bytes:
    """One canonical encoding, used both as the cache key and as the bytes on the wire: sorted
    keys, no whitespace, UTF-8 rather than \\u escapes (a third fewer bytes for accented text)."""
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class Client:
    def __init__(self, api_key: str | None = None, model: str | None = None, timeout: float = 30.0, retries: int = 5,
                 base_url: str | None = None, record: bool = True, label: str = "lib", transport=None):
        self.base_url = (base_url or settings.base_url()).rstrip("/")
        try:
            self.api_key, self.key_source = settings.resolve_key(api_key)
        except AuthError:
            if not settings.RUNTIME.dry_run:
                raise
            self.api_key, self.key_source = "", "(dry run, no key)"  # nothing leaves the machine
        self.model = model or settings.default_model()
        self.timeout = timeout
        self.retries = retries
        self.record = record
        self.label = label
        self.last_ms = 0.0
        self.last_request_id = ""
        self.last_attempts = 0
        self._transport = transport

    @property
    def transport(self):
        if self._transport is None:
            from .transport import Transport  # ssl and http.client load only when a request actually goes out
            self._transport = Transport(self.base_url, self.timeout)
        return self._transport

    def ask(self, state, questions: dict, model: str | None = None) -> dict:
        """POST the state and the questions; return the raw response (plus `cached` on a hit)."""
        if state is None or state == "" or state == {} or state == []:
            raise UsageError("state is empty")
        validate(questions)
        body = {"model": model or self.model, "questions": questions, "state": state}
        payload = serialize(body)
        if settings.RUNTIME.dry_run:
            raise DryRun(self.base_url + settings.ENDPOINT, body)
        use_cache = cache.enabled()
        key = cache.key_for(payload) if use_cache else ""
        if use_cache:
            hit = cache.get(key)
            if hit is not None:
                resp = dict(hit)
                resp["cached"] = True
                resp["cached_usage"] = resp.get("usage")
                resp["usage"] = {"input_tokens": 0, "output_tokens": 0}  # nothing was billed this run
                self.last_ms, self.last_request_id, self.last_attempts = 0.0, "", 0
                self._record(resp, len(questions), cached=True)
                return resp
        try:
            resp = self._call("POST", settings.ENDPOINT, payload)
        except JevError as e:
            self._record(None, len(questions), err=e)
            raise
        self._record(resp, len(questions))
        if use_cache and isinstance(resp.get("answers"), dict):
            cache.put(key, resp)
        return resp

    def models(self) -> dict:
        return self._call("GET", "/v1/models")

    def _record(self, resp, n: int, cached: bool = False, err: Exception | None = None) -> None:
        if self.record:
            ledger.record(ledger.usage_row(self.label, n, self.last_ms, resp, self.last_request_id, self.last_attempts, cached, err))

    def _call(self, method: str, path: str, payload: bytes | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json; charset=utf-8",
                   "Accept": "application/json", "User-Agent": f"jev/{VERSION}"}
        attempt = 0
        while True:
            attempt += 1
            _throttle().wait()
            t0 = time.monotonic()
            try:
                status, hdrs, data = self.transport.request(method, path, payload, headers)
            except OSError as e:
                self.last_ms = (time.monotonic() - t0) * 1000
                if attempt <= self.retries:
                    time.sleep(_backoff(attempt))
                    continue
                raise NetworkError(f"network error after {attempt} attempt(s): {e}")
            self.last_ms = (time.monotonic() - t0) * 1000
            self.last_attempts = attempt
            self.last_request_id = hdrs.get("x-typesafe-request-id") or hdrs.get("x-request-id") or ""
            if 200 <= status < 300:
                try:
                    return json.loads(data.decode("utf-8"))
                except ValueError:
                    raise JevError(f"HTTP {status}: the response was not JSON: {data[:200]!r}")
            text = data.decode("utf-8", "replace")
            if status in (401, 403):
                raise AuthError(f"HTTP {status}: the API key was rejected ({self.key_source}). {text[:300]}")
            if status in (429, 529) or status >= 500:
                if attempt <= self.retries:
                    import random
                    ra = hdrs.get("retry-after", "")
                    delay = float(ra) if ra.replace(".", "", 1).isdigit() else _backoff(attempt)
                    time.sleep(delay + random.uniform(0, 0.25))
                    continue
                raise JevError(f"HTTP {status} after {attempt} attempts: {text[:300]}")
            raise JevError(f"HTTP {status}: {text[:600]}")


def ask(state, questions: dict, **kw) -> dict:
    """`jev.ask(state, {...})` for a script that does not need to hold a client."""
    return Client(**kw).ask(state, questions)

"""The client: one call, `ask`, with validation, dry run, cache, retries and the ledger around it."""

from __future__ import annotations

import json
import time

from . import cache, ledger, settings
from ._version import VERSION
from .errors import AuthError, DryRun, JevError, NetworkError, TooSlow, UsageError
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


def _fit(state, excess: int):
    """The state with its longest text shortened by `excess` bytes and a margin, cut in the middle."""
    def longest(o, path=()):
        best = (len(o.encode("utf-8")), path) if isinstance(o, str) else (0, None)
        items = o.items() if isinstance(o, dict) else enumerate(o) if isinstance(o, list) else ()
        for k, v in items:
            cand = longest(v, path + (k,))
            if cand[0] > best[0]:
                best = cand
        return best

    size, path = longest(state)
    if path is None:
        return state
    text = state
    for k in path:
        text = text[k]
    keep = max(0, len(text) - int((excess + 400) * 1.1))
    cut = f"{text[:keep * 2 // 3]}\n… [{len(text) - keep:,} characters cut to fit the backend] …\n{text[len(text) - keep // 3:]}"
    if not path:
        return cut
    out = json.loads(json.dumps(state))
    node = out
    for k in path[:-1]:
        node = node[k]
    node[path[-1]] = cut
    return out


def serialize(body: dict) -> bytes:
    """One canonical encoding, used both as the cache key and as the bytes on the wire: sorted
    keys, no whitespace, UTF-8 rather than \\u escapes (a third fewer bytes for accented text)."""
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class Client:
    def __init__(self, api_key: str | None = None, model: str | None = None, timeout: float = 30.0, retries: int = 5,
                 base_url: str | None = None, record: bool = True, label: str = "lib", transport=None, on_call=None):
        self.base_url = (base_url or settings.base_url()).rstrip("/")
        try:
            if settings.is_local(self.base_url) and api_key is None:
                # A key belongs to the hosted backend it was made for; a server on this machine gets none unless passed one.
                self.api_key, self.key_source = "", "(local backend, no key)"
            else:
                self.api_key, self.key_source = settings.resolve_key(api_key)
        except AuthError:
            if settings.RUNTIME.dry_run:
                self.api_key, self.key_source = "", "(dry run, no key)"  # nothing leaves the machine
            elif settings.is_local(self.base_url):
                self.api_key, self.key_source = "", "(local backend, no key)"
            else:
                raise
        self.model = model or settings.default_model()
        self.timeout = timeout
        self.retries = retries
        self.record = record
        self.label = label
        self.last_ms = 0.0
        self.last_request_id = ""
        self.last_attempts = 0
        self._transport = transport
        self.on_call = on_call  # told of every request that went out: None when it was answered, the error when it failed

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
        if settings.is_local(self.base_url) and not settings.RUNTIME.dry_run:
            need = len(questions) * settings.local_seconds_per_question()
            if need > self.timeout:
                raise TooSlow(f"{len(questions)} questions would take about {need:.0f} s on the local model, more than the {self.timeout:.0f} s this call has")
        cap_q, cap_b = settings.max_questions(), settings.max_body()
        body = {"model": model or self.model, "questions": questions, "state": state}
        payload = serialize(body)
        if (cap_q and len(questions) > cap_q) or (cap_b and len(payload) > cap_b and len(questions) > 1):
            return self._in_batches(state, questions, model, cap_q, cap_b)
        if cap_b and len(payload) > cap_b:  # one question, and the state alone does not fit: its longest text is cut in the middle
            body["state"] = _fit(state, len(payload) - cap_b)
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
            if self.on_call:
                self.on_call(e)
            raise
        self._record(resp, len(questions))
        if self.on_call:
            self.on_call(None)
        if use_cache and isinstance(resp.get("answers"), dict):
            cache.put(key, resp)
        return resp

    def _in_batches(self, state, questions: dict, model: str | None, cap_q: int, cap_b: int) -> dict:
        """A request with more questions, or more bytes, than the backend takes, sent as several and answered as one."""
        base = len(serialize({"model": model or self.model, "questions": {}, "state": state}))
        batches: list[list[str]] = [[]]
        size = base
        for name, q in questions.items():
            n = len(serialize({name: q}))
            if batches[-1] and ((cap_q and len(batches[-1]) >= cap_q) or (cap_b and size + n > cap_b)):
                batches.append([])
                size = base
            batches[-1].append(name)
            size += n
        answers: dict = {}
        usage = {"input_tokens": 0, "output_tokens": 0}
        cached = True
        resp: dict = {}
        workers = min(settings.parallel(), len(batches))
        if workers > 1:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(workers) as pool:
                results = list(pool.map(lambda names: self.ask(state, {n: questions[n] for n in names}, model), batches))
        else:
            results = [self.ask(state, {n: questions[n] for n in names}, model) for names in batches]
        for resp in results:
            answers.update(resp.get("answers") or {})
            for k in usage:
                usage[k] += int((resp.get("usage") or {}).get(k) or 0)
            cached = cached and bool(resp.get("cached"))
        return {**resp, "answers": answers, "usage": usage, **({"cached": True} if cached else {})}

    def models(self) -> dict:
        return self._call("GET", "/v1/models")

    def _record(self, resp, n: int, cached: bool = False, err: Exception | None = None) -> None:
        if self.record:
            ledger.record(ledger.usage_row(self.label, n, self.last_ms, resp, self.last_request_id, self.last_attempts, cached, err,
                                           local=settings.is_local(self.base_url)))

    def _call(self, method: str, path: str, payload: bytes | None = None) -> dict:
        headers = {"Content-Type": "application/json; charset=utf-8", "Accept": "application/json", "User-Agent": f"jev/{VERSION}"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
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

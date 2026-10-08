"""setup: where jev's judge runs, chosen and checked end to end.

Ollama on this machine, Ollama on another machine (a Mac on your Tailscale network, a box on the
LAN), or a hosted API with a key. For an Ollama it checks the version, finds or makes the 32K tev1
and marks the server, so it gets Ollama's limits wherever it runs. install.sh runs it when a person
is at the terminal; `--judge` (with `--yes`) runs it without questions.
"""

from __future__ import annotations

import getpass
import json
import re
import sys
from urllib.parse import urlsplit

from .. import settings
from ..errors import AuthError, UsageError
from ..transport import Transport
from ._common import add_common, client_for
from .admin import roundtrip, store_key, use_backend

OLLAMA_PORT = 11434
MIN_OLLAMA = (0, 35)
BASE_MODEL, MODEL, WINDOW = "tev1", "tev1-32k", 32768
JUDGES = {
    "local": "Ollama on this machine: free, private, no key (Ollama 0.35+)",
    "remote": "Ollama on another machine you reach (Tailscale, your LAN): the same, by its address",
    "typesafe": "TypeSafe's hosted API: the fastest; a key, and the text jev judges leaves this machine",
    "openrouter": "the same model through OpenRouter: an sk-or- key",
    "keep": "leave it as it is and check it",
}
LOCAL_HINT = "Is Ollama running here? It comes from https://ollama.com; `ollama serve` starts it."
REMOTE_HINT = ("Check the address and that the machine is on and reachable. Ollama listens only on 127.0.0.1 unless "
               "that machine sets OLLAMA_HOST=0.0.0.0 (on a Mac: `launchctl setenv OLLAMA_HOST 0.0.0.0`, then restart "
               "Ollama). It has no password, so open it only on a private network such as Tailscale.")


def register(sub) -> None:
    s = sub.add_parser("setup", help="where the judge runs: Ollama here or on another machine, or an API key; checked end to end",
                       description="Asks where jev's judge runs, prepares it and makes one real decision through it. "
                                   "install.sh runs it when someone is at the terminal.")
    s.add_argument("--judge", choices=list(JUDGES), help="skip the question: " + "; ".join(f"{k}: {v}" for k, v in JUDGES.items()))
    s.add_argument("--url", help=f"remote: the machine running Ollama, as host, host:port or http://host:port (port {OLLAMA_PORT} when none is given)")
    s.add_argument("--pull", action="store_true", help=f"download {BASE_MODEL} on that Ollama when it is missing (4.5 GB)")
    s.add_argument("--yes", action="store_true", help=f"no questions: make {MODEL} from {BASE_MODEL} when it is missing (no download); a download still needs --pull")
    add_common(s)
    s.set_defaults(fn=cmd_setup)


def cmd_setup(args) -> int:
    interactive = sys.stdin.isatty() and not args.yes
    print(f"Where jev's judge runs. Now: {_now()}")
    judge = args.judge
    if not judge:
        if not interactive:
            raise UsageError(f"jev setup --judge {'|'.join(JUDGES)}  (there is no terminal to ask in)")
        for k, v in JUDGES.items():
            print(f"  {k:<11}{v}")
        judge = _choose("Which judge", list(JUDGES), "keep" if _configured() else "local")
    if judge in ("local", "remote"):
        ready = _ollama(judge, args, interactive)
    elif judge in ("typesafe", "openrouter"):
        ready = _hosted(judge, args, interactive)
    else:
        ready = True
    if not ready:
        print(f"jev's backend is unchanged: {_now()}")
        return 4
    try:
        print(f"ok    POST {settings.ENDPOINT}: {roundtrip(client_for(args, 'setup', record=False))}")
    except Exception as e:  # noqa: BLE001  any failure here is the answer to "does it work"
        print(f"FAIL  POST {settings.ENDPOINT}: {e}")
        print("      `jev doctor` checks each piece; `jev setup` again chooses another judge")
        return 4
    print(f"ready: {_now()}")
    return 0


def _ollama(judge: str, args, interactive: bool) -> bool:
    if judge == "local":
        url = settings.BACKENDS["ollama"][0]
    else:
        addr = args.url or (_ask("Address of the machine running Ollama (host, host:port or URL)") if interactive else "")
        if not addr:
            raise UsageError("jev setup --judge remote --url HOST[:PORT]")
        url = ollama_url(addr)
    try:
        version = str(_api(url, "GET", "/api/version").get("version", ""))
    except (OSError, ValueError) as e:
        print(f"FAIL  Ollama at {url}: {e}")
        print("      " + (REMOTE_HINT if judge == "remote" else LOCAL_HINT))
        return False
    if _version(version) < MIN_OLLAMA:
        print(f"FAIL  Ollama {version or '?'} at {url}: decision models need Ollama 0.35 or newer; update it there")
        return False
    print(f"ok    Ollama {version} at {url}")
    names = {_bare(m.get("name", "")) for m in _api(url, "GET", "/api/tags").get("models", [])}
    model = _bare(args.model) if args.model else None
    if model and model not in names:
        print(f"FAIL  no model {model} at {url}; it has {', '.join(sorted(names)) or 'none'}")
        return False
    if not model and MODEL in names:
        model = MODEL
    if not model:
        if BASE_MODEL not in names:
            if not (args.pull or (interactive and _confirm(f"{BASE_MODEL} is not on that Ollama. Download it there (4.5 GB)?", False))):
                print(f"FAIL  no {BASE_MODEL} at {url}: `ollama pull {BASE_MODEL}` on that machine, or `jev setup --pull`")
                return False
            print(f"      downloading {BASE_MODEL} at {url} (4.5 GB) …", flush=True)
            _api(url, "POST", "/api/pull", {"model": BASE_MODEL, "stream": False}, timeout=3600)
            print(f"ok    {BASE_MODEL} downloaded")
        if not (args.yes or (interactive and _confirm(f"Make {MODEL} there, {BASE_MODEL} with a {WINDOW:,}-token window (no download)?", True))):
            print(f"FAIL  {BASE_MODEL}'s own window is 2,050 tokens, too small for most requests: `jev setup --yes` makes {MODEL}")
            return False
        _api(url, "POST", "/api/create", {"model": MODEL, "from": BASE_MODEL, "parameters": {"num_ctx": WINDOW}, "stream": False}, timeout=600)
        print(f"ok    made {MODEL} at {url}")
        model = MODEL
    if judge == "local":
        use_backend("ollama", url, model)
    else:
        use_backend("", url, model, server="ollama")
    print(f"ok    backend ollama: {url} · model {model} · no key, one question per request, four at a time"
          + ("" if judge == "local" else "; the first answer loads the model there, a few seconds"))
    return True


def _hosted(judge: str, args, interactive: bool) -> bool:
    try:
        key = settings.resolve_key(args.api_key)[0]
    except AuthError:
        key = ""
    if interactive:
        label = "OpenRouter key (sk-or-…)" if judge == "openrouter" else f"TypeSafe key ({settings.KEYS_URL})"
        typed = getpass.getpass(f"{label}" + (f", empty keeps {settings.mask(key)}" if key else "") + ": ").strip()
        if typed:
            store_key(typed)
            key = typed
    if not key:
        print(f"FAIL  no API key: `jev auth set <key>` in a terminal, then `jev setup --judge {judge}`")
        return False
    if (judge == "openrouter") != key.startswith("sk-or-"):
        print(f"FAIL  {'an OpenRouter key starts with sk-or-' if judge == 'openrouter' else 'that is an OpenRouter key: choose openrouter'}")
        return False
    use_backend(judge)
    url, model = settings.BACKENDS[judge]
    print(f"ok    backend {judge}: {url} · model {model} · the text jev judges is sent there")
    return True


def ollama_url(addr: str) -> str:
    """host, host:port or a URL -> http://host:port, the port 11434 when a bare host names none."""
    a = addr.strip().rstrip("/")
    bare = "://" not in a
    u = urlsplit("http://" + a if bare else a)
    try:
        port = u.port
    except ValueError:
        raise UsageError(f"not an address: {addr}")
    if not u.hostname or u.scheme not in ("http", "https"):
        raise UsageError(f"not an address: {addr}")
    host = f"[{u.hostname}]" if ":" in u.hostname else u.hostname
    if port is None and bare:
        port = OLLAMA_PORT
    return f"{u.scheme}://{host}" + (f":{port}" if port else "")


def _api(url: str, method: str, path: str, body: dict | None = None, timeout: float = 10.0) -> dict:
    t = Transport(url, timeout=timeout)
    try:
        status, _, data = t.request(method, path, json.dumps(body).encode() if body is not None else None,
                                    {"Content-Type": "application/json"} if body is not None else {})
    finally:
        t.close()
    if status != 200:
        raise OSError(f"HTTP {status} on {path}: {data[:200].decode('utf-8', 'replace').strip()}")
    return json.loads(data or b"{}")


def _now() -> str:
    return f"{settings.backend_name()} · {settings.base_url()} · model {settings.default_model()}"


def _configured() -> bool:
    """Has this machine been pointed somewhere already: a config file, or a key for the hosted API."""
    if settings.CONFIG_FILE.exists():
        return True
    try:
        return bool(settings.resolve_key()[0])
    except AuthError:
        return False


def _version(v: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", v)[:2]) or (0,)


def _bare(name: str) -> str:
    return name[:-len(":latest")] if name.endswith(":latest") else name


def _ask(prompt: str, default: str = "") -> str:
    try:
        ans = input(prompt + (f" [{default}]" if default else "") + ": ").strip()
    except EOFError:
        ans = ""
    return ans or default


def _choose(prompt: str, options: list[str], default: str) -> str:
    while True:
        ans = _ask(prompt, default)
        if ans in options:
            return ans
        print(f"  one of: {', '.join(options)}")


def _confirm(prompt: str, default: bool) -> bool:
    ans = _ask(prompt + (" [Y/n]" if default else " [y/N]")).lower()
    return default if not ans else ans in ("y", "yes")

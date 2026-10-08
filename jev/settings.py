"""Where jev keeps things, and the few numbers everything else prices with.

Resolution order for the backend and the key: explicit argument, environment, the config
file, the vendor default. The config file matters because an agent's tool shell, a cron line
and a launchd job start without a profile: a backend that lives only in `.zshrc` does not
exist there, and the request goes to the wrong host with the wrong key.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

VENDOR_URL = "https://api.typesafe.ai"
DOCS_URL = "https://docs.typesafe.ai"
KEYS_URL = "https://console.typesafe.ai/keys"
ENDPOINT = "/v1/systemone"

PRICE_PER_MTOK_IN = 0.042      # USD per million input tokens; output tokens are free
DEFAULT_AGENT_PRICE = 10.0     # USD per million input tokens of the agent jev decides for
RPM_LIMIT = 1200               # published request ceiling per minute
TPS_LIMIT = 250_000            # published tokens per second
MAX_CHOICE_OPTIONS = 255
MAX_SCORE_LEVELS = 10
STATE_CHAR_CEILING = 107_500   # last state the API accepted (32,388 tokens); above it: 400 max_tokens_exceeded
CHARS_PER_TOKEN = 3.32         # English prose, measured against the API's own token count
CACHE_TTL_DAYS = 30.0

LIB_DIR = Path.home() / ".local" / "share" / "jev"   # where install.sh puts the package; `import jev` works from here

BACKENDS = {  # name -> (base url, default model). Any server that answers POST /v1/systemone works; these are the known ones.
    "typesafe": (VENDOR_URL, "jev-latest"),
    "openrouter": ("https://openrouter.ai/api", "~typesafe/jev-latest"),
    "ollaya": ("http://localhost:11435", "laya"),       # local, open decision models, no key needed
    "ollama": ("http://localhost:11434", "tev1"),       # local, Ollama 0.35+ serves decision models (tev1, nimble), no key needed
    "von": ("http://localhost:8000", "von-1.3.0"),       # local, open System One model, no key needed
}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal"}

# --dry-run and --no-cache are per process; the CLI flips them before dispatching.
RUNTIME = SimpleNamespace(dry_run=False, cache=True)

_LEGACY_HOME = Path.home() / ".config" / "typesafe"


def _pick_home() -> Path:
    env = os.environ.get("JEV_HOME")
    if env:
        return Path(env).expanduser()
    new = Path.home() / ".config" / "jev"
    if not new.exists() and _LEGACY_HOME.exists():
        return _LEGACY_HOME
    return new


HOME: Path
KEY_FILE: Path
CONFIG_FILE: Path
USAGE_FILE: Path
HOOKS_LOG: Path
CACHE_DIR: Path
SESSIONS_DIR: Path
_CONFIG: dict | None = None


def set_home(path: Path | str) -> None:
    """Point every path at `path`. Called once at import; tests call it with a temp dir."""
    global HOME, KEY_FILE, CONFIG_FILE, USAGE_FILE, HOOKS_LOG, CACHE_DIR, SESSIONS_DIR, _CONFIG
    HOME = Path(path)
    KEY_FILE = HOME / "api_key"
    CONFIG_FILE = HOME / "config.json"
    USAGE_FILE = HOME / "usage.jsonl"
    HOOKS_LOG = HOME / "hooks.log"
    CACHE_DIR = HOME / "cache"
    SESSIONS_DIR = HOME / "sessions"
    _CONFIG = None


set_home(_pick_home())


def config() -> dict:
    """The config file, read once per process. `save_config` keeps the copy current."""
    global _CONFIG
    if _CONFIG is None:
        try:
            _CONFIG = json.loads(CONFIG_FILE.read_text())
            if not isinstance(_CONFIG, dict):
                _CONFIG = {}
        except (OSError, ValueError):
            _CONFIG = {}
    return _CONFIG


def save_config(cfg: dict) -> None:
    global _CONFIG
    HOME.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n")
    os.replace(tmp, CONFIG_FILE)
    _CONFIG = dict(cfg)


def base_url() -> str:
    return (os.environ.get("TYPESAFE_BASE_URL") or config().get("base_url") or VENDOR_URL).rstrip("/")


def default_model() -> str:
    return os.environ.get("TYPESAFE_DEFAULT_MODEL") or config().get("model") or "jev-latest"


LOCAL_TIMEOUT = 300.0  # a model on this machine reads a long request slowly; a command waits for it
LOCAL_SECONDS_PER_QUESTION = 2.0  # tev1 on an M4 Pro, measured: a call that cannot finish in its time is not sent


def local_seconds_per_question() -> float:
    v = config().get("local_seconds_per_question")
    return float(v) if v else LOCAL_SECONDS_PER_QUESTION


def max_questions() -> int:
    """Questions per request, 0 for no limit. Ollama takes 64 but reads the whole request again for each one,
    so a request of one question, several at a time, is the fastest there and keeps each item apart."""
    v = config().get("max_questions")
    if v:
        return int(v)
    return 1 if backend_name() == "ollama" else 0


def parallel() -> int:
    """Requests in flight when one is sent in batches: 4 on Ollama, which serves several at once."""
    v = config().get("parallel")
    if v:
        return max(1, int(v))
    return 4 if backend_name() == "ollama" else 1


def max_body() -> int:
    """Bytes per request body the backend takes, 0 for no limit: Ollama refuses a body over 64 KiB."""
    v = config().get("max_body")
    if v:
        return int(v)
    return 60_000 if backend_name() == "ollama" else 0


def price_per_mtok() -> float:
    v = config().get("price_per_mtok_in")
    return float(v) if v else PRICE_PER_MTOK_IN


def agent_price() -> float:
    v = config().get("agent_price_per_mtok_in")
    return float(v) if v else DEFAULT_AGENT_PRICE


def price_usd(input_tokens: int) -> float:
    """Tokens at the hosted list price, whatever answers now: for rows the ledger says were billed."""
    return input_tokens / 1_000_000 * price_per_mtok()


def billed() -> bool:
    """Do requests made now cost money? Not on a server on this machine."""
    return not is_local(base_url())


def cost_usd(input_tokens: int) -> float:
    """What a request made now costs: nothing on a local backend."""
    return price_usd(input_tokens) if billed() else 0.0


def rpm_limit() -> int:
    v = config().get("rpm")
    return int(v) if v else RPM_LIMIT


def is_local(url: str) -> bool:
    """A server on this machine: plain http, or a loopback host. No key is required to talk to it."""
    from urllib.parse import urlsplit
    u = urlsplit(url)
    return u.scheme == "http" or (u.hostname or "") in LOCAL_HOSTS


def backend_name() -> str:
    url = base_url()
    known = next((k for k, (u, _) in BACKENDS.items() if u.rstrip("/") == url.rstrip("/")), None)
    if known:
        return known
    # An Ollama on another machine (`jev setup` marks it) gets Ollama's limits too: one question per
    # request, four at a time, bodies under 64 KiB. The mark only holds for the URL it was set with.
    cfg = config()
    if cfg.get("server") == "ollama" and (cfg.get("base_url") or "").rstrip("/") == url:
        return "ollama"
    return "local" if is_local(url) else "custom"


def option(key: str, env: str | None = None, default=None):
    """A setting that may come from the environment (`env`), from the plugin's user configuration
    (Claude Code exports each `userConfig` value as CLAUDE_PLUGIN_OPTION_<KEY> to hooks and MCP
    servers), or a default; in that order."""
    if env:
        v = os.environ.get(env)
        if v not in (None, ""):
            return v
    v = os.environ.get(f"CLAUDE_PLUGIN_OPTION_{key.upper()}")
    return v if v not in (None, "") else default


def questions_dirs() -> list[Path]:
    """Where saved questions live: the project's `.jev/questions/` first, then the home one."""
    return [Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()) / ".jev" / "questions", HOME / "questions"]


def resolve_key(explicit: str | None = None) -> tuple[str, str]:
    """(key, where it came from). Explicit, then $TYPESAFE_API_KEY, then the key file."""
    from .errors import AuthError

    if explicit:
        return explicit.strip(), "--api-key"
    env = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if env:
        return env, "$TYPESAFE_API_KEY"
    try:
        key = KEY_FILE.read_text().strip()
    except OSError:
        key = ""
    if key:
        return key, str(KEY_FILE)
    raise AuthError(f"no API key. `jev auth set <key>` stores one in {KEY_FILE} (mode 0600); "
                    f"or export TYPESAFE_API_KEY. Keys: {KEYS_URL}")


def mask(key: str) -> str:
    return f"{key[:10]}…{key[-4:]}" if len(key) > 16 else "…"

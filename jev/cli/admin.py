"""auth, doctor, models, config, cost, cache, schema, usage, version."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from .. import cache, ledger, settings
from .._version import VERSION
from ..errors import AuthError, UsageError
from ..questions import noul, score
from ..render import dump, fmt_k
from ..settings import cost_usd, price_per_mtok
from ._common import add_common, add_state_args, build_state, client_for

CONFIG_KEYS = {
    "credits": ("credits_usd", float, "prepaid balance in USD, read off the vendor console; `jev usage` counts down from it"),
    "credits_as_of": ("credits_as_of", str, "ISO timestamp the balance was read (default: when you set it)"),
    "budget": ("budget_usd_month", float, "monthly budget in USD; `jev usage` shows the share used and a month-end projection"),
    "price": ("price_per_mtok_in", float, f"USD per million input tokens, if the list price changes (default {settings.PRICE_PER_MTOK_IN})"),
    "agent_price": ("agent_price_per_mtok_in", float, f"USD per million input tokens of the agent jev decides for (default {settings.DEFAULT_AGENT_PRICE:g}); prices the text it kept out of the context"),
    "base_url": ("base_url", str, "API base URL when it is not the vendor's, e.g. https://openrouter.ai/api (env TYPESAFE_BASE_URL wins)"),
    "model": ("model", str, "default model id, e.g. ~typesafe/jev-latest on OpenRouter (env TYPESAFE_DEFAULT_MODEL wins)"),
    "cache": ("cache", str, "on | off: the local answer cache (default on; --no-cache skips it once)"),
    "cache_ttl_days": ("cache_ttl_days", float, f"days a cached answer stays valid (default {settings.CACHE_TTL_DAYS:g})"),
    "rpm": ("rpm", int, f"requests per minute the client spaces itself to (default {settings.RPM_LIMIT})"),
    "backend": ("backend", str, "typesafe | openrouter | ollama | ollaya | von | a URL of any server that answers /v1/systemone (local ones need no key)"),
    "server": ("server", str, "ollama: the backend URL is an Ollama on another machine, so it gets Ollama's limits (`jev setup` sets it; changing the backend clears it)"),
    "max_questions": ("max_questions", int, "questions per request; more go in batches (default 1 on Ollama, which reads the whole request again for each; no limit elsewhere)"),
    "parallel": ("parallel", int, "batches in flight at once (default 4 on Ollama, 1 elsewhere)"),
    "local_seconds_per_question": ("local_seconds_per_question", float, f"a local model's time per question; a call that would take longer than its timeout is skipped (default {settings.LOCAL_SECONDS_PER_QUESTION:g})"),
    "compact": ("compact_mode", str, "on | auto | off: judge the large tool results before Claude Code or Codex compacts; auto also lets the plugin's mod start one in Claude Code (the plugin's compact_mode wins)"),
}


def register(sub) -> None:
    au = sub.add_parser("auth", help="set | status | clear the API key")
    au.add_argument("action", choices=["set", "status", "clear"], nargs="?", default="status")
    au.add_argument("key", nargs="?")
    au.add_argument("--backend", choices=list(settings.BACKENDS), help="set: also configure this backend (default: OpenRouter for an sk-or- key, otherwise unchanged)")
    add_common(au)
    au.set_defaults(fn=cmd_auth)

    d = sub.add_parser("doctor", help="key, backend, one round trip, the pieces on this machine")
    add_common(d)
    d.set_defaults(fn=cmd_doctor)

    m = sub.add_parser("models", help="list the models the backend offers")
    add_common(m)
    m.set_defaults(fn=cmd_models)

    cf = sub.add_parser("config", help="show | set | unset credits, budget, prices, backend, cache",
                        description=f"Values live in {settings.CONFIG_FILE} (no secrets). Keys: {', '.join(CONFIG_KEYS)}.")
    cf.add_argument("action", nargs="?", choices=["show", "set", "unset"], default="show")
    cf.add_argument("key", nargs="?")
    cf.add_argument("value", nargs="?")
    cf.add_argument("--as-of", help="ISO timestamp the credits balance was read (with `set credits`)")
    add_common(cf)
    cf.set_defaults(fn=cmd_config)

    co = sub.add_parser("cost", help="estimate tokens and cost for a job before running it",
                        description=f"Estimates from characters ({settings.CHARS_PER_TOKEN} per token, measured) or measures the state with one "
                                    "tiny real call (--exact). --items N and --per-item model a rank- or batch-shaped job.")
    co.add_argument("--questions", type=int, default=1, help="questions per request (default 1)")
    co.add_argument("--question-chars", type=int, default=120, help="average characters per question, criteria included (default 120)")
    co.add_argument("--items", type=int, help="items in a rank- or batch-shaped job")
    co.add_argument("--per-item", action="store_true", help="each item is its own request carrying the full state (batch shape)")
    co.add_argument("--exact", action="store_true", help="measure the state's real token count with one tiny call")
    add_state_args(co)
    add_common(co)
    co.set_defaults(fn=cmd_cost)

    ca = sub.add_parser("cache", help="stats | clear the local answer cache")
    ca.add_argument("action", nargs="?", choices=["stats", "clear"], default="stats")
    ca.add_argument("--stale", action="store_true", help="clear: only entries past the ttl")
    ca.set_defaults(fn=cmd_cache)

    s = sub.add_parser("schema", help="a request skeleton with all three question shapes")
    s.set_defaults(fn=cmd_schema)

    u = sub.add_parser("usage", help="spend, tokens, decisions, latency, throughput, credits and budget, from the local ledger",
                       description=f"Every request appends one row of metadata to {settings.USAGE_FILE}. The default view is the summary; "
                                   "--by breaks it down; --tail lists recent requests.")
    u.add_argument("--by", choices=["day", "hour", "cmd", "agent", "session", "model"], help="a breakdown table")
    u.add_argument("--days", type=int, default=14, help="days for --by day (default 14)")
    u.add_argument("--tail", type=int, metavar="N", help="the last N requests, with request ids")
    u.add_argument("--since", help="rows at or after YYYY-MM-DD or an ISO timestamp")
    u.add_argument("--label", help="rows with this label (command name, or the label a script passed)")
    u.add_argument("--agent", help="rows whose agent tag contains this")
    add_common(u)
    u.set_defaults(fn=cmd_usage)

    v = sub.add_parser("version")
    v.set_defaults(fn=lambda a: print(f"jev {VERSION}") or 0)


def store_key(key: str) -> None:
    settings.HOME.mkdir(parents=True, exist_ok=True)
    fd = os.open(settings.KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key.strip() + "\n")
    os.chmod(settings.KEY_FILE, 0o600)
    print(f"stored {settings.mask(key)} in {settings.KEY_FILE} (mode 0600)")


def use_backend(name: str, url: str | None = None, model: str | None = None, server: str | None = None) -> None:
    """Point jev at a known backend, or at `url` with `model`. `server` marks what answers there (ollama)."""
    known_url, known_model = settings.BACKENDS.get(name, (None, None))
    cfg = dict(settings.config())
    cfg["base_url"] = (url or known_url).rstrip("/")
    if model or known_model:
        cfg["model"] = model or known_model
    cfg.pop("server", None)
    if server:
        cfg["server"] = server
    settings.save_config(cfg)


def roundtrip(c) -> str:
    """One small real decision: what doctor and setup report as the backend working."""
    r = c.ask("The export button crashes the settings page in Safari but works in Chrome.",
              {"is_bug": noul("Does this describe a software bug?"),
               "severity": score("How severe?", ["Cosmetic", "Degraded, a workaround exists", "Blocking"])})
    a = r["answers"]
    via = "cache" if r.get("cached") else f"{c.last_ms:.0f} ms, {r['usage']['input_tokens']} tokens"
    return f"model={r.get('model')} is_bug={a['is_bug']['noul']:.2f} severity={a['severity']['score']:.2f} ({via})"


def cmd_auth(args) -> int:
    if args.action == "set":
        key = args.key or (sys.stdin.readline().strip() if not sys.stdin.isatty() else "")
        if not key:
            raise UsageError("jev auth set <key>  (or pipe the key on stdin)")
        store_key(key)
        backend = args.backend or ("openrouter" if key.startswith("sk-or-") and not settings.config().get("base_url") else None)
        if backend:
            use_backend(backend)
            url, model = settings.BACKENDS[backend]
            print(f"backend {backend}: {url} · model {model}  (jev config set backend typesafe|openrouter to change)")
        return 0
    if args.action == "clear":
        try:
            settings.KEY_FILE.unlink()
            print(f"removed {settings.KEY_FILE}")
        except FileNotFoundError:
            print("no key file")
        return 0
    try:
        key, src = settings.resolve_key(args.api_key)
    except AuthError as e:
        print(f"no key: {e}")
        return 3
    print(f"key {settings.mask(key)} from {src}")
    if src == str(settings.KEY_FILE):
        mode = settings.KEY_FILE.stat().st_mode & 0o777
        print(f"file mode {mode:o}" + ("" if mode == 0o600 else "  <- should be 600"))
    return 0


def cmd_doctor(args) -> int:
    ok = True

    def step(name, fn, optional=False):
        nonlocal ok
        try:
            print(f"ok    {name}: {fn()}")
        except Exception as e:  # noqa: BLE001
            ok = ok and optional
            print(f"{'skip' if optional else 'FAIL'}  {name}: {e}")

    step("python", lambda: sys.version.split()[0])
    step("home", lambda: f"{settings.HOME}" + (" (legacy location)" if settings.HOME.name == "typesafe" else ""))
    base = settings.base_url()
    if settings.is_local(base):
        step("key", lambda: "{} from {}".format(*(lambda k, s: (settings.mask(k), s))(*settings.resolve_key(args.api_key))), optional=True)
    else:
        step("key", lambda: "{} from {}".format(*(lambda k, s: (settings.mask(k), s))(*settings.resolve_key(args.api_key))))
    step("backend", lambda: f"{settings.backend_name()} · {base} · model {args.model or settings.default_model()}" + (" · local, no key needed" if settings.is_local(base) else ""))
    c = client_for(args, "doctor", record=False)
    step("GET /v1/models", lambda: f"{[m.get('name') for m in c.models().get('models', [])]} in {c.last_ms:.0f} ms", optional=True)
    step(f"POST {settings.ENDPOINT}", lambda: roundtrip(c))
    step("ledger", lambda: f"{settings.USAGE_FILE} ({settings.USAGE_FILE.stat().st_size if settings.USAGE_FILE.exists() else 0:,} bytes)")
    st = cache.stats()
    step("cache", lambda: f"{'on' if st['enabled'] else 'off'} · {st['entries']} entries · {st['bytes'] / 1024:.0f} KiB · {settings.CACHE_DIR}")
    links = [p for p in (Path.home() / ".claude/skills/jev", Path.home() / ".agents/skills/jev", Path.home() / ".codex/skills/jev",
                         Path.home() / ".config/opencode/skills/jev") if p.exists()]
    step("skill", lambda: ", ".join(map(str, links)) if links else "not linked (run install.sh, or see `jev guide install`)")
    print("healthy" if ok else "problems found")
    return 0 if ok else 4


def cmd_models(args) -> int:
    c = client_for(args, "models", record=False)
    resp = c.models()
    if args.json:
        dump(args, resp)
        return 0
    for m in resp.get("models", []):
        print(f"{str(m.get('name')):<14} {str(m.get('release_date', ''))[:10]}  {m.get('description', '')}")
    if not resp.get("models"):
        print("(the backend lists no models here; the decisions endpoint may still work: jev doctor)")
    return 0


def cmd_config(args) -> int:
    cfg = dict(settings.config())
    if args.action == "show":
        if args.json:
            dump(args, cfg)
            return 0
        print(f"{settings.CONFIG_FILE}" + ("" if settings.CONFIG_FILE.exists() else " (not created yet)"))
        for k, (real, _, desc) in CONFIG_KEYS.items():
            shown = settings.backend_name() if k == "backend" else str(cfg.get(real, "-"))
            print(f"  {k:<15} {shown:<28} {desc}")
        extra = {k: v for k, v in cfg.items() if k not in {r for r, _, _ in CONFIG_KEYS.values()}}
        if extra:
            print(f"  other keys      {', '.join(extra)}")
        return 0
    if not args.key or args.key not in CONFIG_KEYS:
        raise UsageError(f"jev config {args.action} <{'|'.join(CONFIG_KEYS)}>" + (" <value>" if args.action == "set" else ""))
    if args.key == "backend":
        if args.action == "unset":
            for k in ("base_url", "model", "server"):
                cfg.pop(k, None)
            settings.save_config(cfg)
            print("backend reset to the vendor's host")
            return 0
        if args.value in settings.BACKENDS:
            use_backend(args.value)
            url, model = settings.BACKENDS[args.value]
            print(f"backend {args.value}: {url} · model {model}" + ("  (local: no key needed)" if settings.is_local(url) else ""))
            return 0
        if args.value.startswith(("http://", "https://")):
            use_backend("", args.value)
            print(f"backend {settings.backend_name()}: {settings.config()['base_url']} · model {settings.default_model()}  (jev config set model … if the server names it differently"
                  + ("; no key needed" if settings.is_local(args.value) else "") + "; for an Ollama there, `jev setup` sets its limits too)")
            return 0
        raise UsageError(f"jev config set backend <{'|'.join(settings.BACKENDS)}|http(s)://host[:port]>")
    if args.key == "server" and args.action == "set" and args.value != "ollama":
        raise UsageError("jev config set server ollama  (or unset server)")
    real, typ, _ = CONFIG_KEYS[args.key]
    if args.action == "unset":
        cfg.pop(real, None)
        if args.key == "credits":
            cfg.pop("credits_as_of", None)
        settings.save_config(cfg)
        print(f"unset {args.key}")
        return 0
    if args.value is None:
        raise UsageError(f"jev config set {args.key} <value>")
    try:
        val = typ(args.value)
    except ValueError:
        raise UsageError(f"{args.key} expects a {typ.__name__}")
    if args.key == "credits_as_of":
        try:
            datetime.fromisoformat(val)
        except ValueError:
            raise UsageError("credits_as_of expects an ISO timestamp, e.g. 2026-09-19T14:00")
    cfg[real] = val
    if args.key == "credits":
        cfg["credits_as_of"] = args.as_of or ledger.now_iso()
    settings.save_config(cfg)
    print(f"set {args.key} = {val}" + (f" (as of {cfg['credits_as_of']})" if args.key == "credits" else ""))
    return 0


def cmd_cost(args) -> int:
    state = build_state(args)
    text = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)
    est_state = int(len(text) / settings.CHARS_PER_TOKEN)
    q_tokens = max(1, int(args.question_chars / settings.CHARS_PER_TOKEN))
    exact = None
    if args.exact:
        c = client_for(args, "cost")
        r = c.ask(state, {"probe": noul("Is this text written in English?")})
        exact = r["usage"]["input_tokens"]
        est_state = max(0, exact - q_tokens - 40)
    per_request = est_state + args.questions * q_tokens
    fits = per_request <= 64_000 and est_state + q_tokens <= 32_000
    n_items = args.items or 1
    if args.items and args.per_item:
        requests, total = n_items, per_request * n_items
    elif args.items:
        per_req_items = max(1, min(250, (64_000 - est_state) // q_tokens))
        requests = -(-n_items // per_req_items)
        total = requests * est_state + n_items * q_tokens
    else:
        requests, total = 1, per_request
    out = {"state_tokens": est_state, "exact": exact is not None, "question_tokens_each": q_tokens, "questions": args.questions,
           "tokens_per_request": per_request, "fits_request_budget": fits, "items": n_items, "requests": requests,
           "total_input_tokens": total, "usd": round(cost_usd(total), 6), "price_per_mtok_in": price_per_mtok()}
    if args.json:
        dump(args, out)
        return 0
    how = "measured" if exact is not None else f"estimated at {settings.CHARS_PER_TOKEN} chars/token (--exact measures)"
    print(f"state        {est_state:,} tokens ({how})")
    print(f"per request  {per_request:,} tokens = state + {args.questions} question(s) × ~{q_tokens} · {'fits' if fits else 'EXCEEDS'} the 64k / 32k request budget")
    if args.items:
        print(f"job          {n_items:,} items -> {requests:,} request(s), {total:,} input tokens")
    print(f"cost         ${cost_usd(total):.6f} at ${price_per_mtok()}/Mtok" + (f" (${cost_usd(total) / n_items:.7f} per item)" if args.items else ""))
    return 0


def cmd_cache(args) -> int:
    if args.action == "clear":
        n = cache.clear(stale_only=args.stale)
        print(f"cleared {n} {'stale ' if args.stale else ''}cached answer(s) from {settings.CACHE_DIR}")
        return 0
    st = cache.stats()
    rows = [r for r in ledger.rows() if "err" not in r]
    hits = sum(1 for r in rows if r.get("cached"))
    print(f"{st['dir']} · {'on' if st['enabled'] else 'OFF'} · ttl {st['ttl_days']:g} days")
    print(f"  {st['entries']} entries · {st['bytes'] / 1024:.0f} KiB · {st['stale']} past the ttl (jev cache clear --stale)")
    print(f"  ledger: {hits} hits of {len(rows)} requests" + (f" ({hits / len(rows):.0%})" if rows else ""))
    print("  a hit is a previous answer at zero cost. Asking again does not sharpen it: identical requests jitter about ±0.02,\n"
          "  so the stored value is as good as a fresh one. --no-cache skips the cache for one run; `jev config set cache off` for good.")
    return 0


def cmd_schema(args) -> int:
    print(json.dumps({
        "state": "string | object | array — what to judge. Prefer an object with named fields and name them in the questions: `ticket.message`",
        "model": settings.default_model(),
        "questions": {
            "is_urgent": {"type": "noul", "instructions": "Does `ticket.message` need a reply today?",
                          "criteria": {"true": "optional: what yes means", "false": "optional: what no means"}},
            "team": {"type": "choice", "instructions": "Which team should handle `ticket.message`?",
                     "criteria": {"billing": "charges, invoices, refunds", "technical": "bugs, outages, integrations", "other": None}},
            "anger": {"type": "score", "instructions": "How angry is the writer of `ticket.message`?",
                      "criteria": ["calm and matter-of-fact", "frustrated but civil", "furious or threatening to leave"]},
        },
    }, indent=2))
    print("\nAnswers come back under the same ids: noul -> {noul}; choice -> {choice, probabilities, confidence};\n"
          "score -> {score, legend, probabilities, confidence}. Instructions and criteria may be strings or JSON (jev guide structure).")
    return 0


def _since_iso(s: str) -> str:
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        raise UsageError("--since expects YYYY-MM-DD or an ISO timestamp")
    return dt.isoformat(timespec="seconds")


def cmd_usage(args) -> int:
    rows = ledger.rows(since=_since_iso(args.since) if args.since else None)
    if args.label:
        rows = [r for r in rows if r.get("cmd") == args.label]
    if args.agent:
        rows = [r for r in rows if args.agent in (r.get("agent") or "")]
    if not rows:
        print("no usage recorded" + (" for that filter" if (args.label or args.agent or args.since) else " yet"))
        return 0
    now = datetime.now()
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    iso = lambda dt: dt.isoformat(timespec="seconds")  # noqa: E731
    cfg = settings.config()
    name_of = lambda r: r.get("agent") or "(no agent)"  # noqa: E731

    if args.tail:
        sel = rows[-args.tail:]
        if args.json:
            dump(args, sel)
            return 0
        print(f"{'ts':<19} {'label':<12} {'agent':<22} {'q':>4} {'in':>7} {'ms':>5}  rid / err")
        for r in sel:
            tail = f"ERR {r['err']}" if r.get("err") else "cache" if r.get("cached") else r.get("rid", "")
            print(f"{r['ts']:<19} {str(r.get('cmd', '?'))[:12]:<12} {name_of(r)[:22]:<22} {r.get('q', 0):>4} {r.get('in', 0):>7} {r.get('ms', 0):>5}  {tail}")
        return 0

    if args.by:
        groups: dict[str, list[dict]] = {}
        if args.by == "day":
            lo = iso(day0 - timedelta(days=args.days - 1))
            for r in rows:
                if r["ts"] >= lo:
                    groups.setdefault(r["ts"][:10], []).append(r)
        elif args.by == "hour":
            lo = iso(now - timedelta(hours=24))
            for r in rows:
                if r["ts"] >= lo:
                    groups.setdefault(r["ts"][:13] + ":00", []).append(r)
        elif args.by == "agent":
            for r in rows:
                groups.setdefault(name_of(r), []).append(r)
        elif args.by == "session":
            for r in rows:
                tag = str(r.get("agent") or "")
                groups.setdefault(tag if tag.startswith("session:") else "(no session tag)", []).append(r)
        else:
            for r in rows:
                groups.setdefault(str(r.get(args.by) or "?"), []).append(r)
        table = {k: ledger.aggregate(v) for k, v in groups.items()}
        order = sorted(table) if args.by in ("day", "hour") else sorted(table, key=lambda k: -table[k]["input_tokens"])
        if args.json:
            dump(args, {k: table[k] for k in order})
            return 0
        print(f"{args.by:<24} {'requests':>8} {'errors':>6} {'decisions':>9} {'in tokens':>10} {'usd':>9} {'avg ms':>6}")
        for k in order:
            a = table[k]
            print(f"{k[:24]:<24} {a['requests']:>8} {a['errors']:>6} {a['questions']:>9} {a['input_tokens']:>10,} {a['usd']:>9.4f} {a['avg_ms']:>6}")
        return 0

    windows = {"today": (iso(day0), None), "yesterday": (iso(day0 - timedelta(days=1)), iso(day0)), "7d": (iso(now - timedelta(days=7)), None),
               "30d": (iso(now - timedelta(days=30)), None), "month": (iso(day0.replace(day=1)), None), "all": ("", None)}
    summary = {name: ledger.aggregate([r for r in rows if r["ts"] >= lo and (hi is None or r["ts"] < hi)]) for name, (lo, hi) in windows.items()}
    first_ts = datetime.fromisoformat(rows[0]["ts"])
    days_observed = max(1e-9, (now - first_ts).total_seconds() / 86400)
    rate7 = summary["7d"]["usd"] / min(7, days_observed)
    rate30 = summary["30d"]["usd"] / min(30, days_observed)
    forecast = {"usd_per_day_7d": round(rate7, 6), "usd_per_day_30d": round(rate30, 6), "usd_per_month_at_7d_rate": round(rate7 * 30, 4),
                "usd_per_month_at_30d_rate": round(rate30 * 30, 4), "days_observed": round(days_observed, 2)}
    credits = budget = None
    if cfg.get("credits_usd") is not None:
        as_of = cfg.get("credits_as_of") or rows[0]["ts"]
        spent = settings.price_usd(sum(r.get("in", 0) for r in rows if r["ts"] >= as_of and not r.get("local")))
        remaining = float(cfg["credits_usd"]) - spent
        credits = {"credits_usd": float(cfg["credits_usd"]), "as_of": as_of, "spent_since": round(spent, 6), "remaining_usd": round(remaining, 6),
                   "remaining_mtok": round(remaining / price_per_mtok(), 3) if remaining > 0 else 0.0,
                   "days_left_at_7d_rate": round(remaining / rate7, 1) if rate7 > 0 and remaining > 0 else None}
    if cfg.get("budget_usd_month"):
        b = float(cfg["budget_usd_month"])
        mtd = summary["month"]["usd"]
        dim = ((day0.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)).day
        budget = {"budget_usd_month": b, "mtd_usd": mtd, "pct_used": round(100 * mtd / b, 1), "projected_month_end_usd": round(mtd + rate7 * (dim - now.day), 4),
                  "days_left_in_month": dim - now.day}
    lo7 = windows["7d"][0]
    errors: dict[str, int] = {}
    for r in rows:
        if "err" in r and r["ts"] >= lo7:
            errors[r["err"]] = errors.get(r["err"], 0) + 1
    by_cmd: dict[str, list] = {}
    by_agent: dict[str, list] = {}
    by_model: dict[str, list] = {}
    for r in rows:
        by_cmd.setdefault(str(r.get("cmd") or "?"), []).append(r)
        by_agent.setdefault(name_of(r), []).append(r)
        if r.get("model"):
            by_model.setdefault(r["model"], []).append(r)
    peaks = ledger.peaks([r for r in rows if r["ts"] >= windows["30d"][0]])
    out = {"price_per_mtok_in": price_per_mtok(), "ledger": str(settings.USAGE_FILE), "rows": len(rows), "first": rows[0]["ts"], "last": rows[-1]["ts"],
           "windows": summary, "by_cmd": {k: ledger.aggregate(v) for k, v in by_cmd.items()}, "by_agent": {k: ledger.aggregate(v) for k, v in by_agent.items()},
           "by_model": {k: ledger.aggregate(v) for k, v in by_model.items()}, "errors_7d": errors, "throughput_30d": peaks, "forecast": forecast,
           "credits": credits, "budget": budget}
    if args.json:
        dump(args, out)
        return 0

    cached_n = sum(1 for r in rows if r.get("cached"))
    print(f"jev usage · ${price_per_mtok()}/Mtok in, output free · {len(rows)} requests ({cached_n} from cache) since {rows[0]['ts'][:16]} · {settings.USAGE_FILE}")
    print(f"\n{'window':<10} {'requests':>8} {'errors':>6} {'decisions':>9} {'in tokens':>10} {'out':>7} {'usd':>9} {'avg ms':>6}")
    for name, a in summary.items():
        print(f"{name:<10} {a['requests']:>8} {a['errors']:>6} {a['questions']:>9} {a['input_tokens']:>10,} {a['output_tokens']:>7,} {a['usd']:>9.4f} {a['avg_ms']:>6}")
    a7 = summary["7d"]
    live7 = max(1, a7["requests"] - a7["errors"] - a7["cached"])
    print(f"\nlatency 7d   p50 {a7['p50_ms']} ms · p95 {a7['p95_ms']} ms · max {a7['max_ms']} ms · {a7['questions'] / max(1, a7['requests'] - a7['errors']):.1f} decisions/request"
          f" · {a7['input_tokens'] / live7:,.0f} tokens/request")
    print(f"throughput   peak {peaks['peak_rpm']} req/min ({100 * peaks['peak_rpm'] / peaks['rpm_limit']:.1f}% of {peaks['rpm_limit']}) at {peaks['peak_rpm_at']}"
          f" · peak {peaks['peak_tps']:,} tok/s ({100 * peaks['peak_tps'] / peaks['tps_limit']:.2f}% of {peaks['tps_limit']:,}) · 30-day window")
    print("errors 7d    " + (", ".join(f"{k} ×{v}" for k, v in sorted(errors.items(), key=lambda kv: -kv[1])) if errors else "none"))
    ap = settings.agent_price()
    for name in ("today", "7d", "all"):
        a = summary[name]
        if a["not_read_tokens"]:
            print(f"kept out     {name:<6} {a['not_read_tokens']:>10,} tokens of text decided by jev instead of read by the agent · {a['questions']:,} decisions"
                  f" · ~${a['not_read_tokens'] / 1e6 * ap:.2f} of agent input at ${ap:g}/Mtok, for ${a['usd']:.4f} paid  (a ceiling: the uncertain band still gets read)")
    print(f"forecast     ${forecast['usd_per_day_7d']:.4f}/day at the 7-day rate -> ${forecast['usd_per_month_at_7d_rate']:.2f}/month · ${forecast['usd_per_month_at_30d_rate']:.2f}/month at the 30-day rate")
    if credits:
        left = f" · ~{credits['days_left_at_7d_rate']} days left at the 7-day rate" if credits["days_left_at_7d_rate"] else ""
        print(f"credits      ${credits['remaining_usd']:.4f} of ${credits['credits_usd']:.2f} remaining (set {credits['as_of'][:16]}; {credits['remaining_mtok']:.1f} Mtok){left}")
    else:
        print("credits      not configured: the API exposes no balance; read it off the console and `jev config set credits <usd>`")
    if budget:
        print(f"budget       ${budget['mtd_usd']:.4f} of ${budget['budget_usd_month']:.2f} this month ({budget['pct_used']}%) · projected month end ${budget['projected_month_end_usd']:.2f}")
    for title, table in (("by label", by_cmd), ("by agent", by_agent)):
        print(f"\n{title:<24} {'requests':>8} {'decisions':>9} {'in tokens':>10} {'usd':>9}")
        for k, v in sorted(table.items(), key=lambda kv: -sum(r.get("in", 0) for r in kv[1]))[:12]:
            a = ledger.aggregate(v)
            print(f"{k[:24]:<24} {a['requests']:>8} {a['questions']:>9} {a['input_tokens']:>10,} {a['usd']:>9.4f}")
    if len(by_model) > 1:
        print("\nby model: " + ", ".join(f"{k} {len(v)}" for k, v in by_model.items()))
    print(f"\nmore: --by day|hour|cmd|agent|model · --tail N · --since DATE · --label X · --agent X · --json · jev cost · jev config · cache {fmt_k(cached_n)} hits")
    return 0

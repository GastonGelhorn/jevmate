# Using jev from Python
live: /sdk/python

    import sys; sys.path.insert(0, "~/.local/share/jev")      # `jev scaffold --libpath` prints it
    from jev import Client, noul, choice, score, decide, decide_many

    r = Client(label="mail_triage").ask({"email": {"body": body}},
             {"receipt": noul("Is `email.body` a purchase receipt?"),
              "vendor": choice("Which vendor sent `email.body`?", {"amazon": None, "apple": None, "other": None})})
    r["answers"]["receipt"]["noul"]                            # 0..1
    r["answers"]["vendor"]["choice"], r["answers"]["vendor"]["confidence"]

    d = decide({"email": body}, "Does `email` ask for money back?", threshold=0.7, band=(0.6, 0.8))
    if d.yes: ...                                              # d.no / d.unsure; `if d:` raises on purpose

    ds = decide_many(texts, "Is `candidate` a refund request?", threshold=0.42, band=(0.32, 0.52))
    refunds = [t for t, d in zip(texts, ds) if d.yes]
    review  = [t for t, d in zip(texts, ds) if d.unsure]       # never silently filed as "no"

`Client(api_key=None, model=None, timeout=30, retries=5, base_url=None, record=True, label="lib")`.
`ask(state, questions)` returns the raw response and adds `cached: true` on a cache hit. `label`
shows in `jev usage`; `record=False` keeps a call out of the ledger. `decide_many` packs about
250 items per request with each item inside its own question. `jev.grade(client, items, question,
query, levels=…)` is the engine underneath, when you need scores or the per-item p.

Errors: `JevError` (API, exit 4), `AuthError` (3), `NetworkError` (5), `UsageError` (2). The
transport keeps one HTTPS connection per thread alive across calls; a pipeline that asks a
thousand times pays one handshake per worker, not per request.

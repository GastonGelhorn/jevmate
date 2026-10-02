# Usage, cost, credits and rate limits
live: /models

Every request appends one line of metadata to the ledger (never the state or the answers):
when, which label, which agent or session, how many decisions, how many tokens, how long, the
request id. `jev usage` reads it:

    jev usage                      windows (today, yesterday, 7d, 30d, month, all), latency, throughput, forecast
    jev usage --by day|hour|cmd|agent|model
    jev usage --tail 20            recent requests with request ids (quote one to the vendor)
    jev usage --since 2026-09-01 --label rank --json

**The metric this tool exists for** is the "kept out" line: tokens of text jev decided on instead
of the agent reading them, and the number of decisions that was, priced at the agent's input rate
(`jev config set agent_price 10`) next to what jev was paid. It is a ceiling: the uncertain band
still gets read, and test runs count too.

Pricing: $0.042 per million input tokens, output free (`jev config set price` if it changes).
Rate limits: 1,200 requests per minute and 250,000 tokens per second published; the client spaces
its own requests under the first (`jev config set rpm`) and backs off on 429.

There is no balance endpoint. Read the balance off the vendor console and `jev config set credits
<usd>`; `jev usage` counts down from it and estimates days left. `jev config set budget <usd>`
adds a month-end projection. `jev cost --state-file f --questions N --items M` prices a job
before it runs.

`jev cache stats` shows how much of the ledger was served from the cache at zero cost.

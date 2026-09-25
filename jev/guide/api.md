# The HTTP API, limits, pricing
live: /api

    POST {base}/v1/systemone
    Authorization: Bearer <key>
    {"model": "jev-latest", "state": <string | object | array>, "questions": {<id>: <question>, …}}

    -> {"model": "jev-1.13…", "answers": {<id>: {…}}, "usage": {"input_tokens": n, "output_tokens": 0}}

Question shapes: `{"type": "noul", "instructions", "criteria": {"true", "false"}?}`,
`{"type": "choice", "instructions", "criteria": {option: description|null}}` (2 to 255 options),
`{"type": "score", "instructions", "criteria": [level, …]}` (2 to 10 levels, low to high).
Answers: noul `{noul}`; choice `{choice, probabilities, confidence}`; score `{score, legend,
probabilities, confidence}`. `jev schema` prints a complete skeleton; `jev ask --dry-run` prints
the exact request any command would send.

Limits as measured and published: about 107,500 characters of state per request (32k tokens for
state plus the longest question; 64k for the whole request), 1,200 requests per minute, 250,000
tokens per second. Price $0.042 per million input tokens; output is free.

Backends: the vendor's host, or any host that serves the same path. OpenRouter does
(`jev config set base_url https://openrouter.ai/api`, `jev config set model '~typesafe/jev-latest'`);
its `/v1/models` answers an empty list, which `jev doctor` treats as optional. `TYPESAFE_BASE_URL`
and `TYPESAFE_DEFAULT_MODEL` in the environment win over the config file; `--model` wins over both.

The client sends compact UTF-8 JSON (no `\u` escapes), keeps one HTTPS connection per thread alive,
retries 429 / 529 / 5xx with backoff (honouring Retry-After), and identifies itself as `jev/<version>`.

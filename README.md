# jev

Calibrated yes/no, pick-one and rubric decisions for coding agents, from the command line and
from Python. One request, about 250 ms, $0.042 per million input tokens, answers cached locally.

An agent is an expensive, inconsistent judge of repeated small questions, and it cannot tell you
how sure it is. jev hands those questions to a decision model that returns a real probability,
so the agent never reads a thousand items into its context window to sort them, never trusts its
own confidence to gate an action, and never pays twice for the same answer. It ships with the
skill file that teaches Claude Code (and Codex, OpenCode) when to reach for it.

```
$ jev yes 'Does `text` ask for money back?' --field text=@mail.txt
0.831 yes

$ jev sift --query "where failed deliveries are retried" src/ --top 3
0.941    2,140  src/queue/worker.py
0.877    1,102  src/queue/outbox.py
0.312      618  src/http/client.py

$ jev tests --ref main --top 3 --paths-only | xargs vendor/bin/phpunit
```

| Question | Asks | CLI | Returns |
|---|---|---|---|
| noul | is this true? | `jev yes` | P(yes), 0..1 |
| choice | which one of these? | `jev pick` | option, P per option, confidence |
| score | where on this rubric? | `jev rate` | position between your levels, confidence |

`jev ask` mixes any number of them in one request. The list commands (`rank`, `batch`, `sift`,
`tests`, `diff`, `failures`, `cluster`, `stream`) grade hundreds of items per request with each
item embedded in its own question. `jev tune` picks the phrasing and threshold that measure best
on rows you labelled, and `--abstain` hands the band it cannot decide to a reader.

## Install

As a Claude Code plugin (the repository is its own marketplace):

```bash
claude plugin marketplace add OWNER/jev-cli
claude plugin install jev@jev-cli          # asks for the key and the backend; hooks, skills, MCP tools and `jev` on PATH
```

Or as a plain CLI, for Codex, OpenCode, scripts and cron:

```bash
git clone <this repo> && cd jev-cli && ./install.sh     # verifies SHA256SUMS; ~/.local/bin/jev, ~/.local/share/jev, the skill symlinks
jev auth set <key>                                     # stored with mode 0600; an sk-or- key configures OpenRouter
jev doctor                                             # key, backend, one round trip
```

Or `pip install .` for the package and the `jev` entry point alone. Python 3.10 or newer, standard
library only, no dependencies.

The model is TypeSafe's Jev (docs.typesafe.ai). Keys come from their console, or from OpenRouter,
which serves the same endpoint:

```bash
jev config set base_url https://openrouter.ai/api
jev config set model '~typesafe/jev-latest'
```

## What the measurements say

Everything in the defaults was measured; `docs/MEASUREMENTS.md` has the numbers and dates.

- **A threshold is not 0.5.** One question on 80 labelled commits: 63.8% at 0.50, 76.2% at its
  best threshold. Four phrasings of the judgment spanned ten points; averaging them gained 1.2
  points for four times the cost. `jev tune` finds the threshold; a held-out check transferred
  it to fresh rows at the same accuracy.
- **Positions in long arrays are unreliable.** `items[i]` was wrong 86 times in 320 at 150 items
  per request; an item embedded in its own question was wrong 0 times at 320 per request. Every
  list command embeds.
- **Identical requests are not identical answers.** Six in a row: 0.88 0.89 0.90 0.89 0.89 0.88.
  The cache keeps the first; `jev tune` reports how many rows sit within ±0.02 of the bar.
- **The band is where the errors live.** Confident answers from a 76% question were 100% right;
  the hybrid of jev deciding the confident tail and the agent reading the band scored 85% while
  the agent read 36% of the rows.
- **Where it pays.** Filtering 400 items costs the agent zero context and jev a fraction of a
  cent; a hook decision costs 300 ms. Where it does not: a long session's spend is dominated by
  the conversation re-sent every turn, which no filter touches. `jev watch` shows both numbers.

## In Claude Code

The plugin brings, besides the skill Claude reaches for on its own:

| piece | what it does |
|---|---|
| `/jev:sift`, `/jev:tests`, `/jev:review`, `/jev:pr`, `/jev:triage` | the workflows as slash skills, for the person |
| `/jev:stats` | this session's three measured rows, in the chat: went through jev · would have cost · saved |
| `/jev:setup` | key, backend, health check, without the key ever entering the chat |
| `jev:band-reader`, `jev:reviewer` | a Haiku agent that labels the uncertain band, a Sonnet agent that reads the risky hunks: neither costs the main context anything |
| hooks | a guard on Bash that asks and never allows, learns what you let through and takes project rules; a red-suite triage the moment a test command fails; an injection screen on WebFetch and on curled content; a routing hint per prompt; an opt-in honesty check before a reply claims checks passed |
| question packs | `jev q install core`: ten questions with their measured or starting thresholds, ready for `--q` |
| MCP tools | `decide`, `rank`, `sift`, `tests`, `diff`, `cluster`, `session` as typed tool calls, one long-lived process |
| saved questions | `jev tune … --save refund`, then `jev rank --q refund`: the measured threshold and band travel with the question |

The desktop app does not render status lines, so there the numbers live in `/jev:stats` and in
`jev watch` (Terminal panel); the terminal CLI also gets `jev statusline install`. `docs/PLUGIN.md`
has every detail, `SECURITY.md` what leaves the machine (the state and the questions, nothing else).

## From Python

```python
import sys; sys.path.insert(0, "~/.local/share/jev")   # or pip install .
from jev import Client, decide_many, noul

ds = decide_many(texts, "Is `candidate` a refund request?", threshold=0.42, band=(0.32, 0.52))
review = [t for t, d in zip(texts, ds) if d.unsure]      # three outcomes; `if d:` raises on purpose
r = Client(label="triage").ask({"email": body}, {"receipt": noul("Is `email` a purchase receipt?")})
```

## Layout

```
jev/            the package: settings, questions, transport (keep-alive), cache, ledger, client, grading, decide, textio,
                analysis (sift, tests, diff, failures, cluster), metrics (the session view), library (saved questions), mcp, hooks
jev/cli/        one module per command family, imported only when its command runs
jev/guide/      the playbook (`jev guide`) and recipes (`jev examples`), as Markdown
bin/jev         the launcher (on PATH while the plugin is enabled)
skills/         the skill Claude invokes, and the /jev:… ones the person invokes
agents/         jev:band-reader
hooks/ .mcp.json .claude-plugin/   the plugin wiring; the repo is its own marketplace
evals/          six cases for `claude plugin eval`
tests/          python3 -m unittest discover -s tests
docs/           MEASUREMENTS.md, PLUGIN.md
```

Everything the API sees is the state and the questions you pass; the ledger stores metadata
only. `--dry-run` prints the exact request without a key. Nothing fetches code from the network.

MIT.

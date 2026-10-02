# jevmate

Jev, from the shell, for coding agents. A CLI, a Claude Code plugin and an MCP server around
[TypeSafe's Jev](https://docs.typesafe.ai): a small, fast model that answers yes/no, pick-one and
"where on this scale" questions with a calibrated probability instead of a paragraph.

I wrote this for my own Claude Code sessions. The agent kept reading forty files to find the three
that mattered, or running a whole test suite to learn which tests a change could break. Those are
judgment calls that repeat, and a frontier model is an expensive way to make them. Jev makes them
in about 250 ms for $0.04 per million tokens (or for nothing, on a local server), and this repo wraps
it so the agent reaches for it on its own.

The threshold is measured before a question decides anything (`jev tune`), and the measurement
travels with the question (`jev q`). The session tells you what Jev kept out of the context and what
reading it would have cost (`jev session`, `/jevmate:stats`). The hooks never widen what you allowed:
the Bash guard asks or stays quiet, it never answers "allow", and it learns from what you let through.

```
$ jev yes 'Does `text` ask for money back?' --field text=@mail.txt
0.831 yes

$ jev sift --query "where failed deliveries are retried" src/ --top 3
0.941    2,140  src/queue/worker.py
0.877    1,102  src/queue/outbox.py
0.312      618  src/http/client.py

$ jev tests --ref main --top 3 --paths-only | xargs vendor/bin/phpunit
```

## Install

As a Claude Code plugin. The repo is its own marketplace:

```bash
claude plugin marketplace add GastonGelhorn/jevmate
claude plugin install jevmate@gastongelhorn
```

That brings the skill, the slash commands, the hooks, the MCP tools and `jev` on the PATH of the
Bash tool. You will be asked for a key (TypeSafe or OpenRouter) and a backend; both can wait.

As a plain CLI, for Codex, OpenCode, scripts or cron:

```bash
git clone https://github.com/GastonGelhorn/jevmate && cd jevmate && ./install.sh
jev auth set <key>      # an sk-or- key configures OpenRouter by itself
jev doctor
```

`pip install .` works too. Python 3.10 or newer, standard library only.

## What it does

| question | asks | command | returns |
|---|---|---|---|
| noul | is this true? | `jev yes` | P(yes) |
| choice | which one? | `jev pick` | the option, a probability per option, a confidence |
| score | where on this scale? | `jev rate` | a position between your levels, a confidence |

`jev ask` puts several questions in one request. The list commands grade hundreds of items per
request, each item inside its own question: `rank` and `batch` for anything, `sift` for files and
grep hits, `tests` for the test files a diff exercises, `diff` for hunks by risk, `failures` and
`cluster` for a red suite, `stream` for a log you are tailing.

Before a question decides anything at volume, `jev tune` runs a few phrasings over rows you labelled
and reports the threshold, the confusion matrix, how many rows sit close enough to the bar to flip
between runs, and the band to hand to a reader. `--save` keeps the winner; `--q NAME` uses it anywhere.
`jev q install core` brings ten questions with the thresholds they shipped with.

## In Claude Code

| piece | what it is for |
|---|---|
| `/jevmate:sift`, `tests`, `review`, `pr`, `triage` | the same workflows as slash commands |
| `/jevmate:stats` | what went through Jev this session, what reading it would have cost, what that saved |
| `/jevmate:setup` | key, backend and a health check, without the key ever entering the chat |
| `jevmate:band-reader`, `jevmate:reviewer` | a Haiku agent that labels the uncertain band, a Sonnet agent that reads the risky hunks, so the main context pays for neither |
| hooks | below |
| MCP tools | `decide`, `rank`, `sift`, `tests`, `diff`, `cluster`, `session`, for when a typed call beats a shell command |

The hooks run without being asked. Before a Bash command: a guard that asks when the command looks
destructive, takes project rules from `.jev/guard.json`, remembers what you let through in the
session and knows what you last asked for. After a Bash command: long output trimmed to the parts
that carry information (the full output stays on disk), a red test run grouped by cause with the
quick ones named first, and content fetched with curl or gh screened for text aimed at an agent.
After WebFetch: the same screen. At session start: skills and plugins that are new or changed are
read for instructions aimed at an agent. Two more are off by default: a hint when a prompt reads as
routine work, and a check before a reply claims tests passed when none ran. Everything fails open
and logs one line per decision (`jev hooks status`, `jev hooks tune`).

The desktop app does not render status lines, so there the numbers live in `/jevmate:stats` and in
`jev watch` in the Terminal panel. The terminal CLI also gets `jev statusline install`.

## Backends

TypeSafe's hosted API, OpenRouter, or any server that answers the same `/v1/systemone` endpoint.
`jev config set backend ollaya` or `von` points at the open models running on your machine, with no
key; `jev config set backend http://host:port` points anywhere else. Thresholds are per model, so run
`jev tune` again after switching.

## Numbers

Everything in the defaults was measured; `docs/MEASUREMENTS.md` has the tables and dates. The short
version: one question went from 63.8% to 76.2% accuracy by moving the threshold alone; items addressed
by position in a long list were wrong 27% of the time and items embedded in their own question 0%;
identical requests jitter by about ±0.02; the confident answers of a 76% question were all right and
the errors all sat in the band. A 1,300-line `npm install` came out of the trim hook as 47 lines with
the warning and the summary intact.

## Layout

```
jev/        the package: client, cache, ledger, grading, decide, analysis, metrics, library, hooks, mcp
jev/cli/    one module per command family, loaded only when its command runs
jev/guide/  the playbook (`jev guide`) and the recipes (`jev examples`)
skills/     the skill Claude invokes, and the /jevmate:… ones you invoke
agents/     band-reader and reviewer
hooks/ .mcp.json .claude-plugin/   the plugin wiring; the repo is its own marketplace
evals/      six cases for `claude plugin eval`
tests/      python3 -m unittest discover -s tests
```

What leaves the machine is the state and the questions you pass, to the backend you chose; the
ledger keeps metadata only. `SECURITY.md` has the details. MIT.

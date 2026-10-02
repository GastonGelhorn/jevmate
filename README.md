# jevmate

Jev, from the shell, for coding agents. A CLI, a Claude Code plugin and an MCP server around
[TypeSafe's Jev](https://docs.typesafe.ai): a small, fast model that answers yes/no, pick-one and
"where on this scale" questions with a calibrated probability instead of a paragraph.

I wrote this for my own Claude Code sessions. The agent kept reading forty files to find the three
that mattered, or running a whole test suite to learn which tests a change could break. Those are
judgment calls that repeat, and a frontier model is an expensive way to make them. Jev makes them
in about 250 ms for $0.04 per million tokens (or for nothing, on a local server), and this repo wraps
it so the agent reaches for it on its own.

![The line above the prompt in Claude Code: saved 31.9% of the 5-hour window and 5.9% of the week, guard asked 8 times](docs/img/line-above-the-prompt.jpg)

The line above the prompt, after a session in which jev sifted 836 files three times: 2.3 million
tokens judged for ten cents, about $23 of reading at API prices, and on a subscription a third of a
5-hour window kept free.

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
| the line above the prompt, `/jevmate` | the same numbers live while you work, and the full table in a pane |
| `/jevmate:stats` | what went through Jev this session, what reading it would have cost, what that saved, in the chat |
| `/jevmate:setup` | key, backend and a health check, without the key ever entering the chat |
| `jevmate:band-reader`, `jevmate:reviewer` | a Haiku agent that labels the uncertain band, a Sonnet agent that reads the risky hunks, so the main context pays for neither |
| hooks | below |
| MCP tools | `decide`, `rank`, `sift`, `tests`, `diff`, `cluster`, `session`, for when a typed call beats a shell command |

![The details pane: saved $23.09 at most, broken down into reading, trim, subagents and low effort; the 5-hour window at 64% used with 31.9% kept free by jev; the week at 35% with 5.9% kept free; safety: guard asked 8 times, 12 pages flagged, 395 checks; context 89% in use, each turn re-reads it for about $0.22](docs/img/details-pane.jpg)

The `details` pane of the same session. The saving is broken down by lever, and each lever says
whether it is on. On a subscription no token is billed, so the dollars are an API equivalent and the
two plan windows show what jev kept free. The context row says what re-reading the conversation
costs on every turn: in a long session that is the largest cost, and it is what `/compact` reclaims.

The hooks run without being asked. Before a Bash command: a guard that asks when the command looks
destructive, takes project rules from `.jev/guard.json`, remembers what you let through in the
session and knows what you last asked for. After a Bash command: long output trimmed to the parts
that carry information (the full output stays on disk), a red test run grouped by cause with the
quick ones named first, and content fetched with curl or gh screened for text aimed at an agent.
After WebFetch: the same screen. At session start: skills and plugins that are new or changed are
read for instructions aimed at an agent. Everything fails open and logs one line per decision
(`jev hooks status`, `jev hooks tune`).

On Claude Code 2.1.287 and later the plugin also ships a mod, a module that runs inside Claude Code
itself and does what a hook command cannot:

- a line above the prompt with this session's numbers, in your theme's colors, in the terminal and
  in the desktop app. It leads with what jev did in its own units: a saving when there is one worth
  showing (on a subscription, as a share of your 5-hour window and of the week), and otherwise what
  the hooks caught. `details` (or `/jevmate`) opens the full table in a pane; `hide` folds it to a
  small chip, and `show` (or `/jevmate show`) opens it again;
- in bypassPermissions mode, where no permission prompt can appear, the guard used to deny the
  clearly destructive commands outright. Now the mod asks you in Claude's own question dialog and
  the command runs only if you say so. It reuses the hook's verdict, so there is no second model
  call, and it still never answers "allow" on its own;
- a line under a reply that says tests or a build passed when no test, build or lint command ran
  that turn. No model call and nothing blocked;
- reading subagents on a cheaper model, if you turn it on: with `subagent_model` set to `sonnet` or
  `haiku`, a subagent Claude starts without choosing a model, whose task jev reads as reading,
  searching or summarizing, runs on that model. A subagent has its own context, so the main
  conversation's cache is untouched, and its saving is priced from its own usage;
- low effort on routine turns, if you turn it on (`route_mode: effort`). It first checks, on a small
  context, whether lowering effort keeps the prompt cache on your Claude Code build, and stops for
  good if it does not. The main turn's model is never switched: a model's cache does not carry to
  another, so switching would re-write the whole conversation at the new model's price;
- when another plugin's mod loads, one line if it reads a credential and reaches the network, can
  answer permission checks or writes environment variables.

On older versions the hooks work alone and the numbers live in `/jevmate:stats`, `jev watch` (the
desktop app's Terminal panel) and `jev statusline install` (the terminal CLI).

## What it sends, and where

Everything goes to the one backend you configured (TypeSafe's API, OpenRouter, or a server on your
machine) and nowhere else. Keys, tokens and passwords are masked before anything leaves, and the
local ledger keeps metadata only. What each part sends:

| part | when | what it sends |
|---|---|---|
| your `jev` commands, the MCP tools | when the agent or you run them | the text and the questions you pass |
| Bash guard | before a command that is not read-only | the command, the working directory and your last prompt |
| trim | after a command whose output passes ~4,000 tokens | the command and the chunks of output not kept by rule |
| triage | after a failing test run | the failures and, if there is one, the current diff |
| screen | after WebFetch, WebSearch, curl, wget or gh | the fetched text and its address |
| inspect | at session start, for new or changed files only | the text of installed skills, agents, plugin and hook files, CLAUDE.md and AGENTS.md |
| routing (off by default) | on each prompt you type | the prompt |
| honesty check (off by default) | when a reply claims a check passed and none ran | the reply and the commands of that turn |
| subagent routing (off by default) | when Claude starts a subagent without choosing its model | the subagent's task |

The mod itself sends nothing: it runs the local `jev` command, which reads the ledger and the
session's transcript on disk. Every part has an off switch in the plugin's settings. `SECURITY.md`
has the rest.

## Codex, OpenCode and scripts

`jev` is an ordinary command, so any agent that runs shell commands can use it. `install.sh` links
the skill into `~/.codex/skills`, `~/.agents/skills` and `~/.config/opencode/skills` when those
folders exist. For Codex the MCP server takes three lines in `~/.codex/config.toml`:

```toml
[mcp_servers.jev]
command = "jev"
args = ["mcp"]
```

What stays in Claude Code: the hooks, the mod, the slash commands and the two agents. `jev session`
and `jev watch` read Claude Code's transcripts; elsewhere `jev usage` and `jev cost` read the same
ledger.

## Backends

TypeSafe's hosted API, OpenRouter, or any server that answers the same `/v1/systemone` endpoint.
`jev config set backend ollaya` or `von` points at the open models running on your machine, with no
key; `jev config set backend http://host:port` points anywhere else. Thresholds are per model, so run
`jev tune` again after switching.

## Where it pays off

jev keeps text out of the context, so it pays off wherever there is text to keep out: large
searches, logs, test suites, long diffs, hundreds of items to sort. Three `jev sift` runs over a
WordPress repository of 836 files judged 2.3 million tokens for ten cents; reading them would have
cost about $23 at API prices, a third of a 5-hour window on a subscription. In a session spent
writing code there is less to keep out, and the line above the prompt then shows what the hooks
caught rather than inventing a saving.

Two more levers reach the costs that reading does not: reading subagents on a cheaper model
(`subagent_model`) and low effort on routine turns (`route_mode: effort`). Both are opt-in and
measured as they run.

## Numbers

Everything in the defaults was measured; `docs/MEASUREMENTS.md` has the tables and dates. The short
version: one question went from 63.8% to 76.2% accuracy by moving the threshold alone; items addressed
by position in a long list were wrong 27% of the time and items embedded in their own question 0%;
identical requests jitter by about ±0.02; the confident answers of a 76% question were all right and
the errors all sat in the band. A 1,300-line `npm install` came out of the trim hook as 47 lines with
the warning and the summary intact; with trim starting at 4,000 tokens, 40 real outputs lost 2 of
their 828 lines that carry a signal, both of them documentation prose.

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

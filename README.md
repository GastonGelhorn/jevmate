# jevmate

Coding agents spend expensive context on cheap decisions. jevmate makes those decisions first with
[TypeSafe's Jev](https://docs.typesafe.ai), a small calibrated model, and hands only the uncertain
ones back to the agent.

I wrote it for my own Claude Code sessions. The agent kept reading forty files to find the three
that mattered, or running a whole test suite to learn which tests a change could break. Those are
judgment calls that repeat, and a frontier model is an expensive way to make them. Jev answers
yes/no, pick-one and "where on this scale" questions with a calibrated probability instead of a
paragraph, in about 250 ms, for $0.04 per million tokens, or for nothing on a local model.

```
"Which of these 400 commits fix a bug?"

400 commits ─► jev ─┬─ clear yes ─┐
                    ├─ clear no  ─┴─► decided by jev
                    └─ unsure ──────► the agent judges only these
```

```
$ jev yes 'Does `text` ask for money back?' --field text=@mail.txt
0.831 yes

$ jev sift --query "where failed deliveries are retried" src/ --top 3
0.941    2,140  src/queue/worker.py
0.877    1,102  src/queue/outbox.py
0.312      618  src/http/client.py

$ jev tests --ref main --top 3 --paths-only | xargs vendor/bin/phpunit
```

jevmate is a command line, an MCP server, and a plugin for Claude Code and for Codex. The command line
works with any agent that runs shell commands, the MCP server with any agent that speaks MCP, and the
plugins add hooks; in Claude Code also the session's numbers above the prompt.

![The line above the prompt in Claude Code: saved 31.9% of the 5-hour window and 5.9% of the week, guard asked 8 times](docs/img/line-above-the-prompt.jpg)

The line above the prompt, after a session in which jev sifted 836 files three times: 2.3 million
tokens judged for ten cents, about $23 of reading at API prices, and on a subscription a third of a
5-hour window kept free. Savings follow the workload: they are largest when an agent would
otherwise read a large set of candidates.

## Install

As a Claude Code plugin. The repo is its own marketplace:

```bash
claude plugin marketplace add GastonGelhorn/jevmate
claude plugin install jevmate@gastongelhorn
```

That brings the skill, the slash commands, the hooks, the MCP tools and `jev` on the PATH of the
Bash tool. You will be asked for a key (TypeSafe or OpenRouter) and a backend; both can wait.

As a Codex plugin, from the same repo (more under [In Codex](#in-codex)):

```bash
codex plugin marketplace add GastonGelhorn/jevmate
```

As a plain CLI, for Codex, OpenCode, scripts or cron:

```bash
git clone https://github.com/GastonGelhorn/jevmate && cd jevmate && ./install.sh
jev auth set <key>      # an sk-or- key configures OpenRouter by itself
jev doctor
```

`pip install .` works too. Python 3.10 or newer, standard library only.

## What it decides

| question | asks | command | returns |
|---|---|---|---|
| noul | is this true? | `jev yes` | P(yes) |
| choice | which one? | `jev pick` | the option, a probability per option, a confidence |
| score | where on this scale? | `jev rate` | a position between your levels, a confidence |

`jev ask` puts several questions in one request. The list commands grade hundreds of items per
request, each item inside its own question: `rank` and `batch` for anything, `sift` for files and
grep hits, `tests` for the test files a diff exercises, `diff` for hunks by risk, `failures` and
`cluster` for a red suite, `stream` for a log you are tailing.

What jev cannot decide comes back as a band: `--abstain LO HI --uncertain-out review.txt` sets the
rows near the bar aside, for the agent, a cheaper agent or a person to read.

Before a question decides anything at volume, `jev tune` runs a few phrasings over rows you labelled
and reports the threshold, the confusion matrix, how many rows sit close enough to the bar to flip
between runs, and the band to hand to a reader. `--save` keeps the winner; `--q NAME` uses it
anywhere. `jev q install core` brings ten questions with the thresholds they shipped with.

The same decisions are MCP tools (`jev mcp`): `decide`, `ask`, `rank`, `sift`, `tests`, `diff`,
`cluster` and `session`, for an agent that prefers a typed call to a shell command.

## In Claude Code

The plugin brings the skill Claude reaches for on its own, and around it:

| piece | what it is for |
|---|---|
| `/jevmate:sift`, `tests`, `review`, `pr`, `triage` | the same workflows as slash commands |
| `jevmate:band-reader`, `jevmate:reviewer` | a Haiku agent that labels the uncertain band, a Sonnet agent that reads the risky hunks, so the main context pays for neither |
| the line above the prompt, `/jevmate` | the session's numbers live while you work, and the full table in a pane |
| `/jevmate:stats` | the same numbers in the chat |
| `/jevmate:setup` | key, backend and a health check, without the key ever entering the chat |

![The details pane: saved $23.09 at most, broken down into reading, trim, subagents and low effort; the 5-hour window at 64% used with 31.9% kept free by jev; the week at 35% with 5.9% kept free; safety: guard asked 8 times, 12 pages flagged, 395 checks; context 89% in use, each turn re-reads it for about $0.22](docs/img/details-pane.jpg)

The `details` pane of the same session. The saving is broken down by lever, and each lever says
whether it is on. On a subscription no token is billed, so the dollars are an API equivalent and the
two plan windows show what jev kept free. The context row says what re-reading the conversation
costs on every turn: in a long session that is the largest cost, and it is what `/compact` reclaims.

**Hooks** run without being asked. Two keep text out of the context: long command output is trimmed
to the parts that carry information, with the full output kept on disk, and a red test run is
grouped by cause, the quick causes named first. Three are safety signals: a guard before Bash
commands that look destructive, which remembers what you let through and knows what you last asked
for; a screen on fetched content for text aimed at an agent; and an inspection of new or changed
skills and plugins at session start. They are a second opinion on top of Claude Code's permissions,
not a security boundary: they fail open, the guard can ask or refuse but never allow, and the screen
marks text without blocking it. Each logs one line per decision (`jev hooks status`).

**Compaction**, opt-in with `compact_mode`. Before Claude Code compacts the conversation, every large
tool result in it is judged: rules settle a file read again or edited later, a command run again and
an error a retry fixed, and Jev judges the rest against what you asked. Each one is saved on disk.
After the compaction a short block repeats the results the work still needs and lists where the
others are, so nothing the summary drops is lost. Once jev has measured that the summarizer pays for
its whole input, it also hands the summarizer the conversation with the stale results moved out.
Your words, Claude's and the latest results are never touched; `jev compact` shows what it would do
to any session, Claude Code's or Codex's.

**The mod**, on Claude Code 2.1.287 and later, runs inside Claude Code itself. It draws the line
above the prompt and the pane, asks you in Claude's own dialog before a destructive command in
bypassPermissions mode, where a hook alone could only refuse, and adds a line under a reply that
claims a check passed when none ran. It also runs two opt-in levers on the costs that reading does
not reach, both measured as they run: reading subagents on a cheaper model (`subagent_model`) and
low effort on routine turns (`route_mode: effort`). On older versions the hooks work alone.
`docs/PLUGIN.md` has every hook, bar and setting.

## In Codex

The repo is a Codex marketplace too. After `codex plugin marketplace add GastonGelhorn/jevmate`,
install jevmate from the plugin list, review and trust its hooks in `/hooks`, and put `jev` on the
PATH (`./install.sh` or `pip install`) so the agent can run it; `jev auth set <key>` sets the key.

Codex runs the same hooks: the guard, trim, triage, the screen on curl and gh, routing and the
honesty check, and compaction after `jev config set compact on`. Codex's hooks cannot ask before a
command runs, so the guard warns you where Claude Code would ask, and refuses only where no prompt
can appear. A hook cannot change what a Codex compaction keeps either, so jev saves the large results
before it and hands back the block after it. The slash skills run only when you name them (`$sift`).

What stays in Claude Code: the mod (the line above the prompt, the pane, the summarizer reading the
pruned conversation), the two agents, and `jev session` and `jev watch`, which read Claude Code's
transcripts. Elsewhere `jev usage` and `jev cost` read the same ledger.

## OpenCode and scripts

`jev` is an ordinary command, so any agent that runs shell commands can use it. `install.sh` links
the skill into `~/.codex/skills`, `~/.agents/skills` and `~/.config/opencode/skills` when those
folders exist. The MCP server takes three lines in `~/.codex/config.toml`, and the same command
anywhere else:

```toml
[mcp_servers.jev]
command = "jev"
args = ["mcp"]
```

## In CI

jev needs no agent in the loop. Install it with
`pip install git+https://github.com/GastonGelhorn/jevmate`, put a TypeSafe key in
`TYPESAFE_API_KEY` from a secret, and check out the history the diff needs (`fetch-depth: 0`):

```bash
jev tests --ref origin/main --top 10 --paths-only | xargs pytest -q       # the tests the change touches, first
pytest -q > out.txt 2>&1 || { jev cluster -i out.txt --split pytest-long; exit 1; }   # a red suite, by cause
jev diff --ref origin/main --min-level shared                             # the risky hunks of the branch
```

`jev yes` exits 0 for yes and 1 for no or unsure, so a decision can gate a step.

## Backends

jev runs against TypeSafe's hosted API, OpenRouter, or any server that answers the same
`/v1/systemone` endpoint, so no single host is a dependency. `jev config set backend ollaya` or
`von` points at the open models running on your machine, with no key; `jev config set backend
http://host:port` points anywhere else. Thresholds are per model, so run `jev tune` again after
switching.

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
| compaction (off by default) | before Claude Code or Codex compacts | the start and end of each large tool result, your last prompts and the agent's last message |

The mod itself sends nothing: it runs the local `jev` command, which reads the ledger and the
session's transcript on disk. Every part has an off switch in the plugin's settings. `SECURITY.md`
has the rest.

## Where it pays off

jev keeps text out of the context, so it pays off wherever there is text to keep out: large
searches, logs, test suites, long diffs, hundreds of items to sort. Three `jev sift` runs over a
WordPress repository of 836 files judged 2.3 million tokens for ten cents; reading them would have
cost about $23 at API prices, a third of a 5-hour window on a subscription. In a session spent
writing code there is less to keep out, and the line above the prompt then shows what the hooks
caught.

## Numbers

The defaults were measured; `docs/MEASUREMENTS.md` has the tables and dates, and says which bars are
still being tuned. The short version: one question went from 63.8% to 76.2% accuracy by moving the threshold alone; items addressed
by position in a long list were wrong 27% of the time and items embedded in their own question 0%;
identical requests jitter by about ±0.02; the confident answers of a 76% question were all right and
the errors all sat in the band. A 1,300-line `npm install` came out of the trim hook as 47 lines with
the warning and the summary intact; with trim starting at 4,000 tokens, 40 real outputs lost 2 of
their 828 lines that carry a signal, both of them documentation prose. Before a compaction the rules
alone settle 7 to 8% of the large tool results with no model call, and the latest compaction of a
1.4 GB Codex transcript is found in 0.04 s.

## Layout

```
jev/        the package: client, cache, ledger, grading, decide, analysis, metrics, library, hooks, compact, mcp
jev/cli/    one module per command family, loaded only when its command runs
jev/guide/  the playbook (`jev guide`) and the recipes (`jev examples`)
skills/     the skill Claude invokes, and the /jevmate:… ones you invoke
agents/     band-reader and reviewer
hooks/ .mcp.json .claude-plugin/ .codex-plugin/ .agents/   the plugin wiring for Claude Code and Codex; the repo is a marketplace for both
evals/      six cases for `claude plugin eval`
tests/      python3 -m unittest discover -s tests
```

MIT license.

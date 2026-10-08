# jevmate

Coding agents spend expensive context on cheap decisions. jevmate makes those decisions first with
[TypeSafe's Jev](https://docs.typesafe.ai), a small calibrated model: jev decides the clear cases and
hands only the uncertain band back to the agent, so the expensive model reads only what matters. It
is for anyone whose agent makes the same judgment call over many files, tests or items, through a
command line, an MCP server, or a plugin for Claude Code and for Codex.

## Install

As a Claude Code plugin. The repo is its own marketplace:

```bash
claude plugin marketplace add GastonGelhorn/jevmate
claude plugin install jevmate@gastongelhorn
```

That brings the skill, the slash commands, the hooks, the MCP tools and `jev` on the PATH of the
Bash tool. You will be asked for a key (TypeSafe or OpenRouter) and a backend; both can wait, and
`/jevmate:setup` also offers Ollama with no key, on this machine or on another one you reach.

As a Codex plugin, from the same repo (more under [In Codex](#in-codex)):

```bash
codex plugin marketplace add GastonGelhorn/jevmate
```

As a plain CLI, for Codex, OpenCode, scripts or cron:

```bash
git clone https://github.com/GastonGelhorn/jevmate && cd jevmate && ./install.sh
```

`install.sh` ends by asking where the judge runs (`jev setup`): Ollama on this machine, Ollama on
another one you reach (a Mac on your Tailscale network), or a key for TypeSafe or OpenRouter. It
checks the answer with one real decision; `jev setup` asks again any time.

`pip install .` works too. Python 3.10 or newer, standard library only.

## Three things it does

I wrote it for my own Claude Code sessions. The agent kept reading forty files to find the three
that mattered, or running a whole test suite to learn which tests a change could break. Those are
judgment calls that repeat, and a frontier model is an expensive way to make them. Jev answers
yes/no, pick-one and "where on this scale" questions with a calibrated probability instead of a
paragraph, in about 250 ms, for $0.04 per million tokens, or for nothing on a local model. jev keeps
text out of the context, so savings follow the workload: they are largest when an agent would
otherwise read a large set of candidates (large searches, logs, test suites, long diffs, hundreds of
items to sort), smaller in a session spent writing code, where there is less to keep out.

### 1. Before reading many files: `jev sift`

When a search turns up more files or grep hits than the agent should read, `jev sift` ranks them by
how likely each is to hold what you asked for, with the tokens it would cost to read:

```
$ jev sift --query "where failed deliveries are retried" src/ --top 3
0.941    2,140  src/queue/worker.py
0.877    1,102  src/queue/outbox.py
0.312      618  src/http/client.py
```

`--functions` ranks functions and `--grep` a file's grep hits; `--budget-tokens` marks where reading
down the list would pass a budget. jev decides what the agent reads first; the agent still reads it.
In Claude Code the skill reaches for it on its own; `/jevmate:sift` and the MCP tool `sift` do the
same.

Measured: three `jev sift` runs over a WordPress repository of 836 files judged 2.3 million tokens
for ten cents; reading them would have cost about $23 at API prices, a third of a 5-hour window on a
subscription (one session, pictured under [Experimental](#experimental)). The question sift asks is
a starting point, not yet measured on labelled rows.

### 2. On a change: `jev tests`, then `jev diff`

When a change is ready, run first the tests it can break, and read first the hunks where being wrong
costs most:

```bash
jev tests --ref main --top 3 --paths-only | xargs vendor/bin/phpunit   # the test files this diff exercises
jev diff --ref main --task "what the change was supposed to do"       # every hunk by risk, and by scope
```

`tests` ranks every test file against the diff and flags name matches and test files the diff
touched for free. `diff` rates every hunk cosmetic, local, shared or critical, and flags the hunks
that do not belong to `--task`. The full suite still runs before you call it done. In Claude Code:
`/jevmate:tests`, `/jevmate:review`, `/jevmate:pr <number>`, the MCP tools `tests` and `diff`, and
the `jevmate:reviewer` agent (Sonnet), which reads the risky hunks so the main context does not.

- A red suite: `pytest -q 2>&1 | jev cluster --split pytest-long` groups the failures by cause (MCP
  tool `cluster`), and `jev failures` says which ones the diff caused and which look flaky.

Measured: on a 12-file PHPUnit suite the right test file ranked 0.98 and 0.95 on two different
changes, with everything else at or under 0.17 and 0.59, in one request each, about 450 ms,
$0.0004. 12 pytest failures with three planted causes came back as 4 clusters in 3 requests, 1.0 s,
$0.00024: 11 of 12 grouped right, the twelfth flagged as a near miss
([both](docs/MEASUREMENTS.md#clustering-and-test-selection-2026-09-24)). `jev diff` and
`jev failures` are not measured yet.

### 3. Many items to sort: `jev rank`, with a band for the agent

When the same judgment repeats over more items than the agent should read (tickets, notes, commits,
log lines), jev decides the clear ones and sets the rest aside:

```
"Which of these 400 commits fix a bug?"

400 commits ─► jev ─┬─ clear yes ─┐
                    ├─ clear no  ─┴─► decided by jev
                    └─ unsure ──────► the agent judges only these

$ jev rank --query "a bug fix" --candidates-file commits.txt --abstain LO HI --uncertain-out review.txt
```

`jev rank` grades every item inside its own question, hundreds per request; `--abstain` writes the
rows near the bar to `review.txt`, for the agent, a cheaper agent or a person to read. Before a
question decides anything at volume, `jev tune` runs a few phrasings over rows you labelled and
reports the threshold, the confusion matrix, how many rows sit close enough to the bar to flip
between runs, and the band to hand to a reader. `jev batch` asks the same questions of every row of
a JSONL; `jev ask` puts several questions in one request. In Claude Code: `/jevmate:triage`, the MCP
tools `rank` and `ask`, and the `jevmate:band-reader` agent (Haiku), which labels the band so the
main context does not.

Measured on 80 labelled commits: moving the threshold alone took one question from 63.8% to 76.2%
accuracy; its confident answers were all right, and every error sat in the band. On 80 fresh rows,
with that threshold unchanged, the agent alone (Fable 5.1) was 90.0% accurate and read every row;
jev alone, 78.8% and none; jev deciding outside the band with the agent reading the band, 85.0%,
with 36% of the rows read ([details](docs/MEASUREMENTS.md#held-out-transfer-and-the-hybrid-2026-09-24)).

## In Claude Code

The plugin brings the skill Claude reaches for on its own, the slash commands, agents and MCP tools
named in each journey, and `/jevmate:setup`: key, backend and a health check, without the key ever
entering the chat. It also adds hooks and, on Claude Code 2.1.287 and later, a mod that shows the
session's numbers above the prompt (`/jevmate:stats` shows them in the chat on any version); all
three are under [Advanced and experimental](#advanced-and-experimental).

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
folders exist. The same decisions are MCP tools (`jev mcp`): `decide`, `ask`, `rank`, `sift`,
`tests`, `diff`, `cluster` and `session`, for an agent that prefers a typed call to a shell command.
The MCP server works with any agent that speaks MCP. It takes three lines in `~/.codex/config.toml`,
and the same command anywhere else:

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
`/v1/systemone` endpoint, so no single host is a dependency. `jev config set backend ollama` (Ollama
0.35+ and its decision models, `nimble` or `tev1`), `ollaya` or `von` points at the open models
running on your machine, with no key; `jev config set backend http://host:port` points anywhere
else. Thresholds are per model, so run `jev tune` again after switching.

On your own machine, Ollama's `tev1` runs all of it. Its Modelfile sets a 2,050-token window, so
give it 32K, which takes any request Ollama accepts (it refuses bodies over 64 KiB):

```sh
ollama pull tev1
printf 'FROM tev1\nPARAMETER num_ctx 32768\n' > tev1-32k.Modelfile && ollama create tev1-32k -f tev1-32k.Modelfile
jev config set backend ollama && jev config set model tev1-32k
```

`jev setup --judge local` does all of that and checks it. `jev setup --judge remote --url HOST`
points at an Ollama on another machine, such as a Mac with more memory on your Tailscale network,
and marks it so it gets the same limits. That machine has to listen beyond itself
(`OLLAMA_HOST=0.0.0.0`), and Ollama has no password, so do that only on a private network.

jev sends it one question per request, four at a time, and skips a call that could not finish in
its time. The guard's checks take about a second; a sift or a compaction over hundreds of items is
slow there, and a hook goes on without them. Local answers cost nothing, and the key you set for a
hosted backend never reaches them.
[docs/MEASUREMENTS.md](docs/MEASUREMENTS.md#tev1-on-ollama-against-jev-2026-10-05) has the numbers.

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
| compaction `auto` | after a turn, on a context over 200k tokens | that turn's prompt, the three before it and the agent's last reply |

The mod itself sends nothing: it runs the local `jev` command, which reads the ledger and the
session's transcript on disk. Every part has an off switch in the plugin's settings.
[SECURITY.md](SECURITY.md) has the rest.

## Memory across sessions: Shelflife

jevmate decides within a session; it doesn't remember between them. [Shelflife](https://github.com/GastonGelhorn/shelflife-context), listed in the same marketplace, is the memory: it keeps what you told the agent and what your repository's decision records say. When a fact changes, it tells the agent that an earlier recommendation needs another look. It uses `jev` for its small judgments (is this worth keeping, is it relevant, did that advice rest on it), so it needs jev but not this plugin.

```bash
claude plugin install shelflife-context@gastongelhorn
```

On the same continuity script over three runs, with no plugin, jevmate alone, the kernel alone and both, the user explained a fact again 8, 9, 3 and 0 times out of 9. Stale recommendations went unflagged 3, 3, 2 and 0 times out of 6. The kernel with jev also cost about 5 s and 28% more per session. jevmate alone changed nothing on that script; its gains are in the tasks above. [Shelflife's verification](https://github.com/GastonGelhorn/shelflife-context/blob/main/docs/verification.md) has the details.

## Advanced and experimental

Everything here works today and sits outside the three journeys. **Advanced** means stable, with a
measurement behind its defaults; **experimental**, not yet measured or not yet used outside my own
sessions, and it may change. [docs/PLUGIN.md](docs/PLUGIN.md) has every hook, bar and setting.

The hooks run without being asked. Two keep text out of the context (trim, triage); three are safety
signals (the guard, the screen, inspect). They are a second opinion on top of Claude Code's
permissions, not a security boundary: they fail open, and when Jev cannot answer they say so, once
as a warning and on the line above the prompt until it answers again. Each logs one line per
decision (`jev hooks status`). The mod runs inside Claude Code 2.1.287 and later; before that the
hooks work alone.

### Advanced

- **One decision at a time**: `jev yes` answers a noul ("is this true?") with P(yes); `jev pick` a
  choice ("which one?") with the option, a probability per option and a confidence; `jev rate` a
  score ("where on this scale?") with a position between your levels and a confidence. The MCP tool
  is `decide`. ``jev yes 'Does `text` ask for money back?' --field text=@mail.txt`` prints `0.831 yes`.
- **Guard** (hook): asks before a Bash command that looks destructive, remembers what you let
  through and knows what you last asked for; it can ask or refuse but never allow. In
  bypassPermissions mode, where a hook alone could only refuse, the mod asks in Claude's own dialog.
  [Its bars](docs/MEASUREMENTS.md#the-guards-bars-2026-09-24).
- **Screen** (hook): marks fetched content that holds text aimed at an agent, without blocking it.
  [Its bar](docs/MEASUREMENTS.md#the-screens-bar-2026-09-24).
- **Trim** (hook): long command output is trimmed to the parts that carry information, with the full
  output kept on disk. [Measured](docs/MEASUREMENTS.md#output-sizes-and-the-trim-floor-2026-10-02).
- **Triage** (hook): a red test run is grouped by cause, the quick causes named first. The grouping
  is `jev cluster`, measured in journey 2.
- **Saved questions**: `jev tune --save` keeps the winner and `--q NAME` uses it anywhere.
  `jev q install core` brings ten questions with the thresholds they shipped with, four of them
  measured.

### Experimental

- **Inspect** (hook): at session start, new or changed skills and plugins are read for instructions
  aimed at an agent; `jev inspect` runs it by hand.
- **Compaction** (`compact_mode`, off by default): before Claude Code or Codex compacts, rules and
  then Jev judge every large tool result and save it on disk; a short block after the compaction
  repeats what the work still needs and lists where the rest is. Your words, Claude's and the latest
  results are never touched; `jev compact` shows the plan for any session. The rules are measured,
  Jev's bars there [not yet](docs/MEASUREMENTS.md#compaction-what-the-rules-settle-2026-10-04).
- **Automatic compaction** (`compact_mode: auto`): past 200k tokens of context, jev also compacts in
  the background after 55 minutes without a request, while the prompt cache still holds the
  conversation, and right after a turn whose prompt Jev judges to start other work; the line above
  the prompt counts what that saves, less what the summary cost ([the mod](docs/PLUGIN.md#the-mod)).
- **Routing** (`route_mode`, off by default): Jev rates each prompt you type; a routine one gets a
  hint toward a cheaper subagent or lower effort, or, with `route_mode: effort` and the mod, a turn
  at low effort.
- **Cheaper reading subagents** (`subagent_model`, off by default): a subagent Claude starts without
  choosing its model runs on a cheaper one when Jev reads its task as reading. This and low effort
  are the two levers on the costs that reading does not reach, both measured as they run.
- **Honesty checks**: the mod adds a line under a reply that claims a check passed when none ran;
  with `honesty_mode` (off by default) a hook asks Claude to run the check before stopping.
- **`jev stream`**: a semantic grep over a log you are tailing, one yes/no per line.
- **`jev scaffold`** writes a script that sorts the rows of a JSONL into yes, no and review, and
  **`jev label`** builds the labelled set `jev tune` reads, the uncertain rows first.
- **Session accounting**: with the mod, the line above the prompt shows the session's numbers while
  you work and `/jevmate` opens the full table in a pane. `/jevmate:stats` puts them in the chat,
  `jev session` prints them, `jev watch` shows them live in the desktop app's Terminal panel and
  `jev statusline install` under the prompt in the terminal CLI. The saving is an estimate and a
  ceiling ([how it is counted](docs/PLUGIN.md#the-metric)).

![The line above the prompt in Claude Code: saved 31.9% of the 5-hour window and 5.9% of the week, guard asked 8 times](docs/img/line-above-the-prompt.jpg)

![The details pane: saved $23.09 at most, broken down into reading, trim, subagents and low effort; the 5-hour window at 64% used with 31.9% kept free by jev; the week at 35% with 5.9% kept free; safety: guard asked 8 times, 12 pages flagged, 395 checks; context 89% in use, each turn re-reads it for about $0.22](docs/img/details-pane.jpg)

The line above the prompt after the sift session of journey 1, and the `details` pane of the same
session: the saving by lever, each saying whether it is on; on a subscription, where no token is
billed and the dollars are an API equivalent, what jev kept free of the two plan windows; and what
re-reading the conversation costs on every turn, the largest cost in a long session and what
`/compact` reclaims. When a session has little to keep out, the line shows what the hooks caught
instead.

## Numbers

The defaults were measured; [docs/MEASUREMENTS.md](docs/MEASUREMENTS.md) has the tables and dates,
and says which bars are still being tuned. The short version: one question went from 63.8% to 76.2%
accuracy by moving the threshold alone; items addressed by position in a long list were wrong 27% of
the time and items embedded in their own question 0%; identical requests jitter by about ±0.02; the
confident answers of a 76% question were all right and the errors all sat in the band. A 1,300-line
`npm install` came out of the trim hook as 47 lines with the warning and the summary intact; with
trim starting at 4,000 tokens, 40 real outputs lost 2 of their 828 lines that carry a signal, both of
them documentation prose. Before a compaction the rules alone settle 7 to 8% of the large tool
results with no model call, and the latest compaction of a 1.4 GB Codex transcript is found in 0.04 s.

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

## Scope

The core is the three journeys above. New mechanisms are frozen until the core has external users;
issues and measurements about the core come first.

MIT license.

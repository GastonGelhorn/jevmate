# The plugin, piece by piece

```
.claude-plugin/plugin.json      manifest, userConfig (key, backend, guard, screen)
.claude-plugin/marketplace.json this repo is its own marketplace: add it, install `jevmate@gastongelhorn`
skills/jev/SKILL.md             the skill Claude invokes on its own
skills/{sift,tests,review,pr,triage,stats,setup}/SKILL.md   /jevmate:… for the person; never auto-invoked
agents/band-reader.md           jevmate:band-reader, a Haiku agent that labels the uncertain band in its own context
agents/reviewer.md              jevmate:reviewer, a Sonnet agent that reads the hunks jev diff rated risky
hooks/hooks.json                SessionStart, UserPromptSubmit (route), PreToolUse Bash (guard), PostToolUse Bash (after-bash) and WebFetch|WebSearch (screen), PostToolUseFailure Bash, Stop (opt-in)
jev/packs/core.json             ten questions with their thresholds: jev q install core
.mcp.json                       `jev mcp`: decide, rank, sift, tests, diff, cluster, session as tools
bin/jev                         on the Bash tool's PATH while the plugin is enabled
evals/                          six cases for `claude plugin eval`
```

## Install

```bash
claude plugin marketplace add GastonGelhorn/jevmate      # once
claude plugin install jevmate@gastongelhorn                # prompts for the key and the backend
claude plugin update jevmate@gastongelhorn                 # later
```

Or, without the plugin system: `./install.sh` puts the CLI on PATH and links the skill; `jev hooks
install` and `jev statusline install` wire the hooks and the status line into `settings.json`.
Do not run both: the plugin already wires its hooks.

## What each hook does

Six events. All fail open; the bars come from the plugin's settings (`/config`) or the environment.

- **SessionStart**: writes `JEV_SESSION=session:<id>` into `CLAUDE_ENV_FILE`, so every `jev` call
  the agent makes from Bash is attributed to the session in the ledger; turns the plugin's key and
  backend settings into the key file and config, once; remembers the session's transcript path so
  `jev session` finds it; **inspects** skills, plugins, agents and hook files that are new or changed
  since the last look (two questions: does it tell an agent to do something the person would not
  want, does it plant a phrase or link) and speaks only when a new file flags; on a fresh start, adds
  one line of context saying jev is available. `jev inspect` runs the same by hand.
- **PreToolUse on Bash** (`guard`): read-only commands skip the call. Otherwise three yes/no in one
  request: destructive, outside the project, and part of what the person last asked for (the request
  is read from the transcript). `ask` at p(destructive) >= 0.60, and also at p >= 0.45 when the
  command is not part of the request (p <= 0.25); never `allow`; `deny` at p >= 0.90 only in
  bypassPermissions mode or with `guard_mode: deny`. Keys, tokens and passwords in a command are
  masked before it is sent or logged.
- **PostToolUse on WebFetch / WebSearch** (`screen`): one yes/no over the returned text; at
  p >= 0.55 one line of context says the text reads like instructions aimed at an agent.
- **PostToolUse and PostToolUseFailure on Bash** (`after-bash`): records that the command ran (the
  guard's memory: an identical command is not asked about twice in a session; `jev hooks tune`
  reads the pairs). **Trim**: output above ~8,000 tokens (`JEV_TRIM_MIN`) from a command that is not
  a read (cat, grep, git diff …) and not JSON is cut to what carries information: the first and last
  chunks and any chunk with an error or warning stay by rule, the rest is judged one chunk at a time,
  dropped runs become one marker line, the full output is saved to disk and the first line says
  where; the dropped tokens count in `jev session` as text kept out of the context. **Triage**: when
  a test command fails, the quick causes come first with no model call (a missing dependency or
  command, a transient network error, and "the same failures as the previous N runs", which is a
  loop), the rest is grouped by cause (`jev cluster`) and, if there is a diff, split into caused by
  it or not and flaky or not, in one line, with the output saved for `jev cluster -i`. **Screen**:
  content fetched with curl, wget or gh is screened like WebFetch.
- **UserPromptSubmit** (`route`, opt-in via `route_mode`): rates the prompt on a four-level rubric
  (lookup, routine, judgment, hard). For a routine prompt at confidence >= 0.80 (`JEV_ROUTE_CONF`)
  it adds one line suggesting a cheaper subagent or lower effort. A plugin cannot switch the
  session's model; this is a calibrated hint. Prompts under 40 characters, slash commands and
  attachments are skipped. It ships off: measured on 101 prompts written in Spanish over five days,
  a 0.55 bar hinted on half of them and several were design decisions or multi-step tasks; at 0.80
  it would have hinted on a quarter, and the sampled ones were routine. Turn it on in `/config`.
- **Stop** (`stop`, opt-in via `honesty_mode`): when the reply reads like "the tests pass" and the
  transcript shows a test, build or lint command ran this turn, nothing happens and nothing is sent;
  when none ran, the model is asked whether the reply really claims a passed check (p >= 0.70) and,
  if so, Claude is asked to run it before stopping. Never twice in a row (`stop_hook_active`).

Project rules: `.jev/guard.json` with `{"safe": [regex…], "ask": [regex…]}`. `safe` skips the
guard's call; `ask` asks without one. `jev hooks tune` reads the guard's asks and what followed
(allowed, declined) and proposes this machine's ask bar once it has twenty pairs.

The bars: `guard_mode`, `screen_mode`, `triage_mode`, `trim_mode`, `inspect_mode`, `route_mode`, `honesty_mode` in the plugin's
settings (`/config`); `JEV_GUARD_ASK`, `JEV_GUARD_DENY`, `JEV_SCREEN_WARN` in the environment. Every
decision is one JSON line in `hooks.log` (`jev hooks status`). On Windows the hook commands fall
back to `py -3` when `python3` is not on the PATH.

## Backends

Any server that answers `POST /v1/systemone` works: `jev config set backend typesafe|openrouter|ollaya|von`,
or `jev config set backend http://host:port` for another one. A local server (plain http, or a loopback
host) needs no key; `jev doctor` says which backend is in use. ollaya serves open decision models on
`localhost:11435`; von serves its open System One model on `localhost:8000` (docker). Thresholds tuned
on one model do not carry to another: run `jev tune` again after switching.

## MCP tools

Server `plugin:jevmate:jev`; tool names `mcp__plugin_jevmate_jev__<tool>`: `decide`, `ask`, `rank`,
`sift`, `tests`, `diff`, `cluster`, `session`. All read-only on the machine. The server process
lives for the session, so the HTTPS connection is reused and nothing is recompiled per call.

## The metric

`jev session` (and `/jevmate:stats`, and `jev watch` for the desktop app's Terminal panel) shows the
session's three measured rows: what went through jev, what that text would have cost the agent to
read (once as input, plus its re-read on later turns), and the difference, next to the session's
model spend estimated from the transcript at list price. Attribution is exact for hook calls and,
with the plugin, for the agent's own `jev` commands (the SessionStart hook tags them).

## Evals

```bash
claude plugin eval . --allow-tools "Bash(jev *)"          # with and without the plugin, three runs each
claude plugin eval . --runs 1 --ablation none --case single-item-no-jev
```

Cases: triage-many-rows, which-tests-first, sift-before-reading, cluster-red-suite,
review-by-risk (each expects a `jev …` command and a right answer), and single-item-no-jev
(expects no jev call at all). `Bash(jev *)` has to be granted on the command line; eval runs
never prompt. Requires a Claude Code that has `claude plugin eval`.

## Files the plugin writes

Only under the jev home (`~/.config/jev`, or `JEV_HOME`): the key, `config.json`, the ledger,
`hooks.log`, `cache/`, `sessions/`, `questions/`. Nothing under `${CLAUDE_PLUGIN_ROOT}`, which
changes on every update.

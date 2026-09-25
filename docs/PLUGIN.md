# The plugin, piece by piece

```
.claude-plugin/plugin.json      manifest, userConfig (key, backend, guard, screen)
.claude-plugin/marketplace.json this repo is its own marketplace: add it, install `jev@jev-cli`
skills/jev/SKILL.md             the skill Claude invokes on its own
skills/{sift,tests,review,triage,stats,setup}/SKILL.md   /jev:… for the person; never auto-invoked
agents/band-reader.md           jev:band-reader, a Haiku agent that labels the uncertain band in its own context
hooks/hooks.json                SessionStart, PreToolUse Bash (guard), PostToolUse WebFetch|WebSearch (screen)
.mcp.json                       `jev mcp`: decide, rank, sift, tests, diff, cluster, session as tools
bin/jev                         on the Bash tool's PATH while the plugin is enabled
evals/                          six cases for `claude plugin eval`
```

## Install

```bash
claude plugin marketplace add OWNER/jev-cli      # once
claude plugin install jev@jev-cli                # prompts for the key and the backend
claude plugin update jev@jev-cli                 # later
```

Or, without the plugin system: `./install.sh` puts the CLI on PATH and links the skill; `jev hooks
install` and `jev statusline install` wire the hooks and the status line into `settings.json`.
Do not run both: the plugin already wires its hooks.

## What each hook does

- **SessionStart**: writes `JEV_SESSION=session:<id>` into `CLAUDE_ENV_FILE`, so every `jev` call
  the agent makes from Bash is attributed to the session in the ledger; turns the plugin's key and
  backend settings into the key file and config, once; remembers the session's transcript path so
  `jev session` finds it; on a fresh start, adds one line of context saying jev is available.
- **PreToolUse on Bash** (`guard`): read-only commands skip the call. Otherwise two yes/no in one
  request: destructive, and outside the project. `ask` at p >= 0.60; never `allow`; `deny` at
  p >= 0.90 only in bypassPermissions mode or with `guard_mode: deny`.
- **PostToolUse on WebFetch / WebSearch** (`screen`): one yes/no over the returned text; at
  p >= 0.55 one line of context says the text reads like instructions aimed at an agent.

The bars: `guard_mode` and `screen_mode` in the plugin's settings (`/config`); `JEV_GUARD_ASK`,
`JEV_GUARD_DENY`, `JEV_SCREEN_WARN` in the environment. Every decision is one JSON line in
`hooks.log` (`jev hooks status`).

## MCP tools

Server `plugin:jev:jev`; tool names `mcp__plugin_jev_jev__<tool>`: `decide`, `ask`, `rank`,
`sift`, `tests`, `diff`, `cluster`, `session`. All read-only on the machine. The server process
lives for the session, so the HTTPS connection is reused and nothing is recompiled per call.

## The metric

`jev session` (and `/jev:stats`, and `jev watch` for the desktop app's Terminal panel) shows the
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

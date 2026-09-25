# Changelog

## 1.2.1

The triage line names the error, not the test.

## 1.2.0

Hooks that work without being asked: `after-bash` (PostToolUse and PostToolUseFailure on Bash)
groups a failed test run by cause and says which failures the current diff caused, in one line,
screens content fetched with curl, wget or gh, and records what ran so the guard never asks twice
about a command you already let through; `route` (UserPromptSubmit) rates each prompt and hints
when a cheaper subagent or lower effort is enough; `stop` (opt-in) holds a reply that claims tests
passed when no test command ran. The guard takes project rules from `.jev/guard.json`, and
`jev hooks tune` proposes this machine's ask bar from what you allowed and declined.

Also: `/jev:pr` with the `jev:reviewer` agent (Sonnet), a shipped question pack (`jev q packs`,
`jev q install core`), `jev batch --resume`, and Windows fallbacks (`py -3` in the hook commands,
no `/dev/tty` or `select()` on the way).

## 1.1.0

A Claude Code plugin, and the repo is its own marketplace (`claude plugin marketplace add
OWNER/jev-cli`, `claude plugin install jev@jev-cli`): the skill, six slash skills (`/jev:sift`,
`/jev:tests`, `/jev:review`, `/jev:triage`, `/jev:stats`, `/jev:setup`), the `jev:band-reader`
agent (Haiku) for the uncertain band, the hooks wired by the plugin with a SessionStart hook that
tags the agent's own `jev` calls per session, an MCP server (`jev mcp`: decide, ask, rank, sift,
tests, diff, cluster, session), user configuration for the key and the backend, and an eval suite.

New commands: `jev session` (the three measured rows for this session), `jev q` (saved questions:
`jev tune --save NAME`, then `--q NAME` on yes / rank / batch / label / stream), `jev mcp`,
`jev config set backend typesafe|openrouter`, `jev usage --by session`. `jev auth set` recognises
an OpenRouter key and configures the backend. The analysis behind sift, tests, diff, failures and
cluster moved into `jev.analysis`, usable from Python; session metrics into `jev.metrics`.

## 1.0.0

First release. A package (`jev/`) with a launcher, a skill file for Claude Code, Codex and
OpenCode, and these commands: ask, yes, pick, rate; rank, batch, sift, tune, label, scaffold;
tests, diff, failures, cluster, stream; hooks, hook, statusline, watch; usage, cost, cache,
config, auth, doctor, models, schema, guide, examples, docs.

Design points, each measured (see docs/MEASUREMENTS.md): items embedded in their own question,
never indexed; a threshold sweep before a question decides at volume; an abstention band handed
to a reader; a local answer cache justified by run-to-run jitter; hooks that ask and never allow;
a per-session metric of text kept out of the agent's context, next to what it cost.

Engineering: standard library only; one keep-alive HTTPS connection per worker thread; compact
UTF-8 request bodies used as both the wire format and the cache key; command modules imported
only when their command runs; the session transcript read incrementally by `jev watch`;
checksummed install.

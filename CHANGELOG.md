# Changelog

## 1.5.0 — first public release

The `jev` command (ask, yes, pick, rate; rank, batch, sift, tune, label, scaffold; tests, diff,
failures, cluster, stream; q and the core question pack; usage, cost, cache, session, watch,
statusline; mcp), the Claude Code plugin (skill, slash commands, two agents, hooks, a mod, MCP
server) and the docs.

Hooks: a guard on Bash, trimming of long output, triage of red test runs, a screen on fetched
content, an inspection of installed instruction files at session start, plus opt-in routing and an
opt-in honesty check. Secrets are masked in everything they send.

The mod (Claude Code 2.1.287+): the session's numbers above the prompt and in a pane (`/jevmate`), the
guard's question where no permission prompt can appear, a line under a reply that claims a check
passed when none ran, routing that lowers the effort or changes the model of a routine turn, and a
note when another mod reaches for credentials or permissions.

Backends: TypeSafe, OpenRouter, ollaya, von or any URL.

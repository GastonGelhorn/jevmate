# Changelog

## 1.3.0

First release.

- The `jev` command: one-item decisions (ask, yes, pick, rate); list grading with the item embedded in
  its own question (rank, batch, sift, tune, label, scaffold); a change and a red suite (tests, diff,
  failures, cluster, stream); saved questions (`jev q`, `--q NAME`, `jev tune --save`, the `core` pack);
  the ledger and the session view (usage, cost, cache, session, watch, statusline); an MCP server
  (`jev mcp`).
- The Claude Code plugin **jevmate**, served from its own marketplace: the skill Claude invokes, the
  slash skills `/jevmate:sift`, `tests`, `review`, `pr`, `triage`, `stats`, `setup`, the agents
  `jevmate:band-reader` (Haiku) and `jevmate:reviewer` (Sonnet), MCP tools `mcp__plugin_jevmate_jev__*`,
  and hooks that work unasked: a guard on Bash that asks and never allows, learns what you let through
  and takes project rules; a red-suite triage the moment a test command fails; an injection screen on
  fetched and curled content; an opt-in routing hint per prompt; an opt-in honesty check before a
  reply claims checks passed; a SessionStart hook that tags every `jev` call with the session.
- Measured defaults (docs/MEASUREMENTS.md), 84 tests, a CI matrix on five Pythons and two OSes,
  SECURITY.md, docs/PLUGIN.md, an eval suite for `claude plugin eval`.

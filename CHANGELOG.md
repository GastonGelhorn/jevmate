# Changelog

## 1.6.0

Compaction that keeps what the work needs (`compact_mode`, off by default; `jev compact`). Before
Claude Code or Codex compacts, every large tool result is judged. Rules settle, with no model call, a
file read again or edited later, a command run again and an error a retry fixed; Jev judges the rest,
each result inside its own question, against what the person asked. The uncertain band is cut, never
moved out whole. Every large result is saved on disk, and after the compaction a block of at most
2,500 tokens repeats what the work still needs and says where the rest is. With the mod, once the
summarizer is measured to pay for its whole input, it reads the conversation with stale results
moved out. `jev compact --report` counts how often a saved result is read again.

jevmate for Codex: a Codex plugin (`.codex-plugin/`, `hooks/codex.json`, a repo marketplace) with
the guard, trim, triage, the screen, routing, the honesty check and compaction, each answering in
Codex's terms. The slash skills run there only when named.

Fixed: the honesty check's block reached neither Claude Code nor Codex, because it was nested where a
Stop hook's answer is not read.

## 1.5.4 — first public release

The `jev` command (ask, yes, pick, rate; rank, batch, sift, tune, label, scaffold; tests, diff,
failures, cluster, stream; q and the core question pack; usage, cost, cache, session, watch,
statusline; mcp), the Claude Code plugin (skill, slash commands, two agents, hooks, a mod, MCP
server) and the docs.

Hooks: a guard on Bash, trimming of long output, triage of red test runs, a screen on fetched
content, an inspection of installed instruction files at session start, plus opt-in routing and an
opt-in honesty check. Secrets are masked in everything they send.

The mod (Claude Code 2.1.287+): the session's numbers above the prompt, in the theme's colors, folding
to a chip and back, and in a pane with a meter (`/jevmate`, `/jevmate show|hide`), the
guard's question where no permission prompt can appear, a line under a reply that claims a check
passed when none ran, and a note when another mod reaches for credentials or permissions.

The session metric: only text that stood in for the agent's reading counts as kept out (the hooks are
listed apart as safety checks); each piece is priced at the model of the turn that would have read
it, then re-read as cache until the next compaction or until it would not have fit; on a
subscription the saving shows as a share of the 5-hour window and of the week. The line above the
prompt leads with value in its own units: a saving worth showing, or what the hooks caught.

Levers on the big slices, opt-in and measured as they run: reading subagents on a cheaper model, and
low effort on routine turns once a check showed it keeps the prompt cache. Trim starts at 4,000
tokens (measured), and file reads behind `cd dir &&` are recognized and left alone.

The pane breaks the saving down by lever (reading, trim, subagents, low effort), says which are on,
and prices what the session's read-only subagents would have cost on Sonnet. Every plan window is
kept with the time of its reading, and the context's per-turn cost is shown.

Backends: TypeSafe, OpenRouter, ollaya, von or any URL.

# Changelog

## 1.8.0

Two backends, by size. `jev config set long_backend` sends the requests over `short_chars` (7,000
characters) or 64 questions to a second server, and the first one's refusal of a request for its
length is answered there without a warning. It is for a small local decoder beside a fast encoder:
Ollama's `tev1` (`jev config set backend ollama`, Ollama 0.35+) for the guard and the other short
questions, ollaya's `laya` for sift, diff, tests, inspect, trim and compaction. On the guard's own
history tev1 flagged 18 of the 24 commands Jev flagged and 5 of 180 it let through; it refuses
requests over 2,050 tokens and reads long ones slowly, which laya does in seconds. `jev doctor`
checks both.

A server on this machine is never sent the key configured for a hosted backend, and its answers are
marked `local` in the ledger and cost nothing; what was billed before keeps its price.

## 1.7.0

`compact_mode: auto`: jev compacts on its own, in the background, once the context passes 200k
tokens, and never before a turn, so nobody waits for it. After 55 minutes without a request it
compacts while the prompt cache still holds the conversation: the summary reads it at the cache's
price, and the first request after the break writes the small context instead of the whole one.
Right after a turn whose prompt Jev judges to start other work (`jev hook shift`: that prompt against
the three before it and the last reply), it compacts with that prompt as the summary's instructions,
while the answer is read. Each one is logged with the context before and after and the summary's own
cost, and `jev session`, the line above the prompt and the pane count what it saved: every later
request re-read the smaller context, until the next compaction, less the summary. In Codex, whose
hooks cannot start a compaction, `auto` acts as `on`.

## 1.6.4

The compactions stay on the line above the prompt when it is narrow, as when the pane is docked
beside the transcript: their count now comes ahead of the safety figures. The failure reads in two
words (`no credit`, `key rejected`, `unreachable`), so it no longer pushes the saving off the line;
the pane keeps the API's own words. On the desktop the line fits a fifth more text, as its
proportional font does.

## 1.6.3

The line above the prompt says when Jev cannot judge, with the reason, until a hook gets an answer
again; the warning alone could pass unseen at the start of a session, so the next hook repeats it.
Compactions show on the line and keep the pane open in a session where Jev never answered, which
used to read as nothing decided yet. `jev session` carries the same failure.

## 1.6.2

When Jev cannot judge, the person hears of it. The hooks failed open in silence: with no key, no
credit or no network every command went through unchecked and only hooks.log knew. Now the next hook
whose answer the person sees carries a warning with the reason (`HTTP 402: Insufficient credits`),
once a session and again if the reason changes, and one more line when Jev answers again. In Codex
too.

## 1.6.1

The block after a compaction repeats only what Jev judged the work still needs. The ten latest
results used to come back first on recency alone, and after a finished task that was its output;
they are still never touched, but they are judged with the rest. Without Jev the block repeats
nothing and lists where every result was saved.

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

# The plugin, piece by piece

```
.claude-plugin/plugin.json      manifest, userConfig (key, backend, guard, band, evidence, screen, triage, trim, compaction, inspect, routing, subagents, honesty)
.claude-plugin/marketplace.json this repo is its own marketplace: add it, install `jevmate@gastongelhorn`
skills/jev/SKILL.md             the skill Claude invokes on its own
skills/{sift,tests,review,pr,triage,stats,setup}/SKILL.md   /jevmate:… for the person; never auto-invoked
agents/band-reader.md           jevmate:band-reader, a Haiku agent that labels the uncertain band in its own context
agents/reviewer.md              jevmate:reviewer, a Sonnet agent that reads the hunks jev diff rated risky
hooks/hooks.json                SessionStart, UserPromptSubmit (route), PreToolUse Bash (guard), PostToolUse Bash (after-bash) and WebFetch|WebSearch (screen), PostToolUseFailure Bash, PreCompact (opt-in), Stop (opt-in); and the mod
hooks/jevmate.tsx               the mod (Claude Code 2.1.287+): the line above the prompt, /jevmate and its pane, the guard's question in bypass mode, the evidence line, routing, the pruned conversation for the summarizer
hooks/codex.json                the same hooks for Codex, answering in Codex's terms
.codex-plugin/plugin.json       the Codex plugin; .agents/plugins/marketplace.json makes the repo a Codex marketplace
jev/compact.py                  compaction: the rules, Jev's judgment, the saved results and the block after the compaction
jev/packs/core.json             ten questions with their thresholds: jev q install core
.mcp.json                       `jev mcp`: decide, ask, rank, sift, tests, diff, cluster, session as tools
bin/jev                         on the Bash tool's PATH while the plugin is enabled
evals/                          six cases for `claude plugin eval`
tests/mod/                      the mod's tests: `claude plugin test .`
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

Seven events. All fail open, and not in silence: when Jev cannot judge (no key, no credit, no
network), the next hook whose answer the person sees adds a warning with the reason, once a session
and again if the reason changes, then one line when Jev answers again. The bars come from the
plugin's settings (`/config`) or the environment.

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
  command is not part of the request (p <= 0.25); never `allow`; `deny` at p >= 0.90 with
  `guard_mode: deny`, or in bypassPermissions mode when the mod is not there to ask (below). Keys,
  tokens and passwords in a command are masked before it is sent or logged.
- **PostToolUse on WebFetch / WebSearch** (`screen`): one yes/no over the returned text; at
  p >= 0.55 one line of context says the text reads like instructions aimed at an agent.
- **PostToolUse and PostToolUseFailure on Bash** (`after-bash`): records that the command ran (the
  guard's memory: an identical command is not asked about twice in a session; `jev hooks tune`
  reads the pairs). **Trim**: output above ~4,000 tokens (`JEV_TRIM_MIN`) from a command that is not
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
  (lookup, routine, judgment, hard). With `route_mode: hint`, a routine prompt at confidence >= 0.80
  (`route_conf`) gets one line suggesting a cheaper subagent or lower effort. With `effort` the hook
  stays quiet and the mod acts instead (below). Prompts under 40 characters, slash commands and
  attachments are skipped. It ships off: measured on 101 prompts written in Spanish
  over five days, a 0.55 bar flagged half of them and several were design decisions or multi-step
  tasks; at 0.80 it would have flagged a quarter, and the sampled ones were routine.
- **Stop** (`stop`, opt-in via `honesty_mode`): when the reply reads like "the tests pass" and the
  transcript shows a test, build or lint command ran this turn, nothing happens and nothing is sent;
  when none ran, the model is asked whether the reply really claims a passed check (p >= 0.70) and,
  if so, Claude is asked to run it before stopping. Never twice in a row (`stop_hook_active`).
- **PreCompact** (`pre-compact`, opt-in via `compact_mode`): before Claude Code compacts, the live
  conversation is read from the transcript, from its latest compaction on, and every tool result over
  1,500 characters is judged. Rules come first and cost nothing: a file read again in full or edited
  later (by the Read tool, a shell read such as `cat` or `sed -n`, an edit or a patch), a command run
  again, an error a retry fixed. Jev judges the rest, each result inside its own question, against
  the person's last three prompts and the agent's last message: kept whole at p >= 0.65, cut to its
  head and tail from 0.35, moved out below (tev1's own bars: 0.48 and 0.30, since its answers sit
  nearer the middle). On a local model only the newest 20 large results are judged, since each
  question takes seconds there; the older ones stay whole and saved. The ten latest large results
  are never touched, only judged for the block. Every large result is saved under `compacted/` in the jev home, and when
  SessionStart fires with `source: compact` it adds a block of at most 2,500 tokens: the results Jev
  judged still needed, the surest first, verbatim or cut, then where the rest are, each named by the
  description the agent gave its command; `index.md` in that folder says why each one was kept, cut or
  moved (the rule, or Jev's answer). Without a key or a
  backend the rules still decide, everything else stays whole and the block repeats nothing: recency
  alone cannot tell the file about to change from the output of a task already finished. A read of one of those files later is logged as a result moved out
  too eagerly; `jev compact --report` gives the rate, which is what the two bars are tuned against.

## The mod

`hooks/hooks.json` names `hooks/jevmate.tsx` under `modules`. Claude Code 2.1.287 and later load
it into the session; older versions ignore the field and run the hooks alone, and the numbers are
in `/jevmate:stats`, `jev watch` (the desktop app's Terminal panel) and `jev statusline install` (the
terminal CLI). It calls the local
`jev` for every decision, so thresholds, the cache and the ledger stay the CLI's.

- **The line above the prompt** (`band_mode`): it leads with value in its own units. A saving of 50
  cents or more shows as money (or, on a subscription, as a share of the 5-hour window and of the
  week); below that, what the hooks caught (questions asked, pages and instruction files flagged, red
  runs sorted, unbacked claims) and the tokens kept out. Its other figures: decisions, tokens kept out, the share
  of the session's cost (Claude Code's own figure when it reports one), what was trimmed, how often
  the guard asked and the context in use, colored with the theme's own keys (`claude`, `success`,
  `warning`, `error`, `suggestion`) so it follows light and dark themes. While Jev cannot judge, it
  leads with that in two words (`Jev can't judge · no credit`), folded too, until a hook gets an
  answer again; the pane gives the API's own words. Compactions count ahead of the safety figures,
  and a session in which Jev never answered still shows them. On a narrow screen the least useful
  numbers go first (how many results the compactions saved before their count); the failure and the
  saving always stay. On the desktop, whose font is proportional, the line fits a fifth more. Refreshed after each Bash command and each turn,
  one refresh at a time. `details` and `/jevmate` open the session table in a pane, with a meter for
  the share and buttons to fold the line, refresh and close. `hide` folds the line to a chip with the
  saving and a `show` button; `/jevmate hide` and `/jevmate show` do the same from the prompt. The
  choice is kept across sessions (`band_collapsed` in Claude Code's store). Other mods' bands stay,
  drawn under this one.
- **The guard's question** (`guard_mode`): in bypassPermissions mode the PreToolUse hook holds its
  verdict for the mod instead of denying, and the mod's permission check reads it back (no second
  model call) and asks the person: "Refuse" or "Run it". `ask` asks from p >= 0.90, `strict` from
  p >= 0.60 and for unrequested commands; with `deny` the hook denies from p >= 0.90 and nothing is
  asked. Outside bypass mode nothing changes: the permission prompt is the question. A dismissed
  dialog refuses; so does a failure of the mod while it was about to ask.
- **The evidence line** (`evidence_line`): under a reply that says tests, a build or a check passed
  when no test, build or lint command ran in that turn. A regular expression, no model call.
- **Low effort on routine turns** (`route_mode: effort`): a prompt rated routine runs its turn at
  effort low, decided at the turn's first request and kept for the rest of it. Lowering effort is free
  only if Claude Code keeps the prompt cache across the change, so the mod checks: until it knows, it
  lowers effort only when the context is under 100k tokens, and it compares the first request after
  each change with the one before (a quick follow-up only, since a cache can also expire by age). If
  the request read the conversation from the cache, the change is free from then on; if it wrote it
  again, effort routing stops for good on that Claude Code build. The verdict is kept per version. The
  main turn's model is never switched: a model's cache does not carry to another.
- **Reading subagents on a cheaper model** (`subagent_model: sonnet | haiku`, off by default): when
  Claude starts a subagent without choosing its model, the parent is a costlier tier, and jev reads
  the task as reading, searching, listing or summarizing (p >= 0.85), the subagent runs on that model.
  When it finishes, its own usage is priced at both models and the difference counts as saved. The
  plugin's own agents and forks are left alone.
- **The summarizer's input** (`compact_mode`): the first compaction on a Claude Code version is left
  alone and measured. If its summarizer request read the conversation from the prompt cache, a pruned
  conversation would cost more than it saves, so this stays off on that version; if the summarizer paid
  for its whole input, from then on it gets the conversation with the moved results replaced by one
  line each and the cut ones by their head and tail, and the calls that wrote a file without the file's
  text. The saving is logged at the session model's input price. A summary computed ahead of time is
  skipped while this is on, since it would be computed over the unpruned conversation and thrown away.
- **Compactions it starts** (`compact_mode: auto`), in the background, never before a turn, once the
  context passes 200k tokens (`JEV_AUTO_COMPACT_MIN`). Idle: a timer from the main thread's last
  request fires after 55 minutes (`JEV_AUTO_COMPACT_IDLE`), while the prompt cache (an hour at most)
  still holds the conversation, so the summary reads it at the cache's price and the first request
  after the break writes the small context instead of the whole one; a prompt stops the timer, each
  turn re-arms it, the same session keeps it across a restart (`last_request` in the store), and a
  timer that wakes after the hour (a machine asleep) lets it go. Other work: a second after a turn,
  Jev reads that turn's prompt against the three before it and the last reply (`jev hook shift`);
  at p >= 0.85 the conversation is compacted with that prompt as the summary's instructions. Each one
  logs `compact-auto` with the context before and after, the summary's own request priced, and the
  time of the last request before it. `jev session` prices what it saved: every later request until
  the next compaction re-read the smaller context, the first one priced as it would have gone without
  it (a full write if the cache would have expired by then, a cache read if not), less the summary.
- **Other mods**: as each loads, one line when it reads a credential and reaches the network or
  processes, answers permission checks, or writes environment variables.

`claude plugin test .` runs the mod's tests on the terminal and desktop surfaces.

## Project rules

Project rules: `.jev/guard.json` with `{"safe": [regex…], "ask": [regex…]}`. `safe` skips the
guard's call; `ask` asks without one. `jev hooks tune` reads the guard's asks and what followed
(allowed, declined) and proposes this machine's ask bar once it has twenty pairs.

The bars: `guard_mode`, `band_mode`, `evidence_line`, `screen_mode`, `triage_mode`, `trim_mode`, `inspect_mode`, `route_mode`,
`route_conf`, `subagent_model`, `honesty_mode`, `compact_mode` in the plugin's settings (`/config`); `JEV_GUARD_ASK`, `JEV_GUARD_DENY`, `JEV_SCREEN_WARN`, `JEV_COMPACT_KEEP`, `JEV_COMPACT_CUT`, `JEV_COMPACT_RECENT`, `JEV_COMPACT_MIN`, `JEV_COMPACT_BUDGET` in the environment. Every
decision is one JSON line in `hooks.log` (`jev hooks status`). On Windows the hook commands fall
back to `py -3` when `python3` is not on the PATH.

## Backends

Any server that answers `POST /v1/systemone` works: `jev config set backend typesafe|openrouter|ollama|ollaya|von`,
or `jev config set backend http://host:port` for another one. A local server (plain http, or a loopback
host) needs no key; `jev doctor` says which backend is in use. ollaya serves open decision models on
`localhost:11435`; von serves its open System One model on `localhost:8000` (docker); Ollama 0.35+
serves `tev1` and `nimble` on `localhost:11434`. Thresholds tuned on one model do not carry to
another: run `jev tune` again after switching.

On Ollama, which reads the whole request again for each question, refuses bodies over 64 KiB and
takes 64 questions at most, jev sends one question per request (`max_questions`), four at a time
(`parallel`), keeps each body under 60,000 bytes (`max_body`) and cuts a state still too big in the
middle. On any local backend a hook gets most of its entry's time (the guard 8 s, after-bash 22,
screen 13, PreCompact 110), a command waits up to 300 s, a call whose questions would take longer
than that at 2 s each (`local_seconds_per_question`) is not sent and the caller goes on without it,
and a timeout is not reported as Jev failing. A local server is sent no key, and its answers are
marked `local` in the ledger and cost nothing. tev1 needs a variant with a larger window
(`PARAMETER num_ctx 32768`); the README shows it.

## MCP tools

Server `plugin:jevmate:jev`; tool names `mcp__plugin_jevmate_jev__<tool>`: `decide`, `ask`, `rank`,
`sift`, `tests`, `diff`, `cluster`, `session`. All read-only on the machine. The server process
lives for the session, so the HTTPS connection is reused and nothing is recompiled per call.

## The metric

`jev session` (and the line above the prompt, `/jevmate`, `/jevmate:stats`, and `jev watch`) shows:

- **went through jev**: every request of the session, hooks included: decisions, tokens, what jev
  charged. Attribution is exact for hook calls and, with the plugin, for the agent's own `jev`
  commands (the SessionStart hook tags them).
- **kept out**: only text that stood in for the agent's own reading (sift, rank, batch, tests, diff,
  failures, cluster, stream, the single-decision commands, the MCP tools, scripts built on the
  library) and what the trim hook dropped. The guard, the screen, inspect, routing, triage and the
  honesty check also go through jev, but the agent would not have read that text anyway, so they are
  listed apart as safety checks.
- **would have cost**: each piece once as input on the first turn after it, at that turn's model,
  then again as a cache read on every later turn, until the conversation was compacted (the
  transcript marks each compaction) or until the text would no longer have fit beside the real
  context (90% of the model's window). List prices per model, checked 2026-09-25;
  `jev config set model_prices` overrides them, `jev config set agent_price N` prices the first read
  at a flat $N per million instead.
- **saved**: that, minus what jev charged for those reads. A ceiling, since the uncertain band was
  read anyway. The share of the session uses Claude Code's own `/cost` figure when the mod passes it,
  else the transcript estimate.
- **on a subscription** no token is billed, so the dollars are an API equivalent. With each refresh
  the mod passes Claude Code's readings of the 5-hour and weekly windows, and jev learns how many
  points of each window one API-equivalent dollar takes: the points a window moved over the dollars
  the session spent meanwhile, decayed so it follows the plan. An interval in which the window
  reset, the session's figure went back, or another session was active teaches nothing. After $2 of
  observed use the saving shows as a share of the 5-hour window and of the week; until then it says
  "measuring". Every window Claude Code reports is kept, a week per model included on plans that
  have one, each with the time of its reading. A request that leaves a window out does not blank it:
  its last reading stands until the window resets, and a window with no reading yet says so. The rates
  and the latest readings live in `plan.json` in the jev home.
- **by lever**: the pane breaks the saving down into reading (what jev judged instead of the agent),
  trim, subagents and low effort, each saying whether it is on. With subagent routing off, it prices
  the session's read-only subagents (Explore, claude-code-guide) at Sonnet from their own transcripts,
  so the pane shows what turning it on would have saved; nothing, when they already ran on Haiku.
- **the context's own cost**: each turn sends the whole conversation again, so the pane shows what
  the next turn costs to re-read it at the current model's cache-read price. It is the largest cost
  in a long session, and the reason a /compact pays off.

## Evals

```bash
claude plugin eval . --allow-tools "Bash(jev *)"          # with and without the plugin, three runs each
claude plugin eval . --runs 1 --ablation none --case single-item-no-jev
```

Cases: triage-many-rows, which-tests-first, sift-before-reading, cluster-red-suite,
review-by-risk (each expects a `jev …` command and a right answer), and single-item-no-jev
(expects no jev call at all). `Bash(jev *)` has to be granted on the command line; eval runs
never prompt. Requires a Claude Code that has `claude plugin eval`.

## Codex

`.codex-plugin/plugin.json` names `hooks/codex.json` and the skills folder; Codex finds them once
`codex plugin marketplace add GastonGelhorn/jevmate` has run, the plugin is installed and its hooks
are trusted in `/hooks`. The hook commands are the same `jev hook …`, and the hooks know they run
under Codex (Codex sets `PLUGIN_ROOT` and puts a turn id in its turn hooks; `JEV_HOST=codex` forces
it). Where Codex reads an answer differently, they answer in its terms:

- the guard cannot ask: where Claude Code would show a permission prompt, the person gets the
  reason as a warning and Codex's own approval rules decide; where no prompt can appear it refuses;
- trimmed output replaces the tool's result as hook feedback, since Codex does not take a rewritten
  result from PostToolUse;
- compaction cannot be changed from a hook, so the large results are saved at PreCompact and the
  block comes back at SessionStart (`source: compact`); turn it on with `jev config set compact on`
  (`auto` acts as `on` there: a Codex hook cannot start a compaction);
- the slash skills carry `agents/openai.yaml` with `allow_implicit_invocation: false`: Codex runs
  them when named (`$sift`), never on its own.

The mod, the agents and `jev session` stay Claude Code's.

## Files the plugin writes

Under the jev home (`~/.config/jev`, or `JEV_HOME`): the key, `config.json`, the ledger,
`hooks.log`, `plan.json`, `cache/`, `sessions/`, `questions/`, `outputs/`, `compacted/`. The full
output of a trimmed command or a red test run goes to the session's scratch folder when Claude Code
has one, else to `outputs/`. `compacted/` holds one folder per compaction, kept 14 days. Nothing
under `${CLAUDE_PLUGIN_ROOT}`, which changes on every update.

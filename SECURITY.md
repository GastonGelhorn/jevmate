# Security and privacy

**What leaves the machine.** Requests go to the one backend you configured: TypeSafe's API
(`api.typesafe.ai`), OpenRouter (`openrouter.ai`), or a server on your own machine. Nothing else:
no telemetry, no crash reports, no other host. A command you run sends the text and the questions
you pass it. The hooks send, on their own:

- the Bash guard: the command, the working directory and your most recent prompt, before any
  command that is not read-only;
- trim: the command and the chunks of its output that are not kept by rule, when the output passes
  about 4,000 tokens;
- triage: the failures of a red test run and, when there is one, the current git diff;
- screen: text fetched by WebFetch, WebSearch, curl, wget or gh, and its address;
- inspect: at session start, the text of installed skills, agents, plugin and hook files,
  CLAUDE.md and AGENTS.md that are new or changed since the last look;
- routing and the honesty check, both off by default: the prompt you typed, or the reply and the
  commands of that turn;
- subagent routing, off by default: the task of a subagent Claude starts without choosing its model.

Tokens, keys and passwords (`key=…`, bearer headers and the common key shapes) are replaced with
`<secret>` before any of this is sent or logged. `--dry-run` prints the request any command would
send, without a key and without sending it. Each hook has an off switch in the plugin's settings.
What the backend keeps is up to its own policy: TypeSafe's or OpenRouter's terms apply.

**What stays.** The ledger (`usage.jsonl`) stores metadata per request: when, which command or
label, which session, how many questions and tokens, how long, the request id. Never the state,
never the answers. The cache stores the answers, keyed by a hash of the request, under the jev
home with your user's permissions; `jev cache clear` removes them. `hooks.log` keeps one line per
hook decision, with the command masked and cut to 200 characters. Trimmed output and red test runs
are saved in full to the session's scratch folder or the jev home, so nothing the trim drops is lost.
`plan.json` keeps, on a subscription, the 5-hour and weekly windows' percentages and each session's
cost for 14 days, to learn how much of a window a dollar of use takes.

**The key.** `jev auth set` writes it with mode 0600 and never prints it whole. The plugin's
configuration field is marked sensitive, so Claude Code keeps it in the platform's credential
store and hands it to the hook process, which copies it to the key file once. The agent never
needs to see it: `/jevmate:setup` tells the person to run `jev auth set` in their own terminal.
`TYPESAFE_API_KEY` in the environment is read too, for scripts and CI.

**Hooks.** The Bash guard answers `ask` or nothing; it never answers `allow`, so it cannot widen
what the person permitted. Where no prompt can appear (bypassPermissions) it denies a command at
p >= 0.90, unless the plugin's mod is loaded in an interactive session: then the mod asks the
person in Claude Code's own question dialog, with "Refuse" as the first answer, and the command runs
only on "Run it". A dismissed question, or a failure of the mod while it was about to ask, refuses
the command. Every hook fails open on any other error. Fetched pages are screened for text
addressed to an agent, but that is advice to the model, not a filter: the content still arrives
unchanged.

**The mod.** `hooks/jevmate.tsx` runs inside Claude Code and reaches nothing of its own: it runs
the local `jev` command (to read the session's numbers and the guard's verdict, and to record what
it did), reads `HOME` and `PATH`, sets `JEV_GUARD_MOD` for the hooks, keeps two values in Claude
Code's store (`band_collapsed`, whether the line is folded, and `effort_cache`, whether lowering
effort kept the prompt cache on this Claude Code version), and draws a line, a pane and a question.
With `route_mode: effort` it lowers the effort of a turn Jev rated routine; with `subagent_model`
set it picks the model of a reading subagent Claude starts without choosing one. `claude plugin
validate .` lists every call it makes.

**Code.** Standard library only, no dependencies, no `eval`, no `pickle`, no network beyond the
API call and the two documentation fetches (`jev docs`, `jev guide --live`) that print what they
get. `install.sh` refuses to install files that do not match `SHA256SUMS`.

Report a problem by opening an issue, or privately to gastongelhorn@gmail.com.

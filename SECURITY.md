# Security and privacy

**What leaves the machine.** Exactly what you pass as the state and the questions, to the backend
you configured (TypeSafe's host, or OpenRouter's). Nothing else: no telemetry, no crash reports,
no file names beyond what a command sends as a candidate. `--dry-run` prints the request any
command would send, without a key and without sending it.

**What stays.** The ledger (`usage.jsonl`) stores metadata per request: when, which command or
label, which session, how many questions and tokens, how long, the request id. Never the state,
never the answers. The cache stores the answers, keyed by a hash of the request, under the jev
home with your user's permissions; `jev cache clear` removes them.

**The key.** `jev auth set` writes it with mode 0600 and never prints it whole. The plugin's
configuration field is marked sensitive, so Claude Code keeps it in the platform's credential
store and hands it to the hook process, which copies it to the key file once. The agent never
needs to see it: `/jev:setup` tells the person to run `jev auth set` in their own terminal.

**Hooks.** The Bash guard answers `ask` or nothing; it never answers `allow`, so it cannot widen
what the person permitted. It answers `deny` only above p = 0.90 and only where no prompt can
appear. Every hook fails open on any error. Fetched pages are screened for text addressed to an
agent, but that is advice to the model, not a filter: the content still arrives unchanged.

**Code.** Standard library only, no dependencies, no `eval`, no `pickle`, no network beyond the
API call and the two documentation fetches (`jev docs`, `jev guide --live`) that print what they
get. `install.sh` refuses to install files that do not match `SHA256SUMS`.

Report a problem by opening an issue, or privately to the maintainer listed in `pyproject.toml`.

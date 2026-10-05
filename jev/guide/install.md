# Where jev lives, and how it is maintained

    ~/.local/bin/jev              the launcher (bin/jev in the repo)
    ~/.local/share/jev/jev/       the package; `import jev` after sys.path.insert(0, "~/.local/share/jev")
    ~/.local/share/jev-skill/     SKILL.md, symlinked into ~/.claude/skills/jev (and Codex, OpenCode dirs that exist)
    ~/.config/jev/                api_key (0600), config.json, usage.jsonl, hooks.log, plan.json, cache/, sessions/, questions/
                                  (JEV_HOME overrides; an existing ~/.config/typesafe is used when ~/.config/jev is absent)

`install.sh` verifies `SHA256SUMS` and refuses on a mismatch, so the files you audited are the
files that run; `tools/checksums.sh` regenerates the sums after a change. `pip install .` (or
pipx) installs the same package with a `jev` entry point, minus the skill symlinks.

The key: `jev auth set <key>`, or `TYPESAFE_API_KEY`, or `--api-key`. The backend: `--model`, the
`TYPESAFE_BASE_URL` / `TYPESAFE_DEFAULT_MODEL` variables, then `jev config set backend
typesafe|openrouter|ollama|ollaya|von|http://host:port`, then the vendor's host. A local server needs no key. The config file matters because an agent's tool shell, cron and launchd
start without a profile.

Claude Code: `jev hooks install` and `jev statusline install` write absolute commands into
`~/.claude/settings.json` (`<launcher> hook guard`, `<launcher> statusline render`) and back the
file up first. `jev hooks uninstall` / `jev statusline uninstall` remove exactly those entries.

Updating: pull, run `install.sh`, `jev doctor`. Nothing here fetches code from the network.

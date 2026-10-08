---
name: setup
description: Set up jev on this machine: where its judge runs (Ollama here, Ollama on another machine such as a Mac on Tailscale, or an API key for TypeSafe or OpenRouter), checked end to end.
disable-model-invocation: true
allowed-tools: ["Bash(jev *)"]
---

Set jev up. Never ask the person to paste an API key into the chat, and never run a command that
contains one.

1. Run `jev doctor` and say in one line where the judge runs now and whether it answered.
2. Unless the person already said, ask where it should run (one question):
   - **local**: Ollama 0.35+ on this machine. Free and private, about a second a check.
   - **remote**: Ollama on another machine they reach, such as a Mac on their Tailscale network or
     a box on the LAN. Ask for its address: host, host:port or URL.
   - **typesafe** or **openrouter**: a hosted API with a key. The fastest; the text jev judges
     leaves this machine.
   - **keep**: as it is.
3. Apply it with `jev setup`, which prepares the judge and makes one real decision through it:
   - local: `jev setup --judge local --yes`; remote: `jev setup --judge remote --url <address> --yes`.
     `--yes` makes `tev1-32k` from `tev1` when only `tev1` is there (no download). When `tev1` is
     missing the command says so: ask before adding `--pull`, a 4.5 GB download on that machine.
     If the remote one cannot be reached, pass on the hint the command prints: Ollama there listens
     only on 127.0.0.1 unless it sets `OLLAMA_HOST=0.0.0.0`, and it has no password, so only on a
     private network.
   - typesafe or openrouter: when `jev doctor` showed no key, tell the person to run
     `jev auth set <key>` **in their own terminal** (TypeSafe keys at console.typesafe.ai,
     OpenRouter keys start with sk-or-), then run `jev setup --judge typesafe` or
     `jev setup --judge openrouter`. The plugin's settings (`/config`, jev section) take the key too;
     it is written to the key file at the next session start.
   - keep: `jev setup --judge keep`.
4. Report the last lines: backend, model and round-trip time. Every tool on this machine that runs
   jev shares that backend (Shelflife's memory, if installed, among them).
5. Offer the three commands that pay first: `jev sift` before reading, `jev tests` on a change,
   `jev session` for what it saved.

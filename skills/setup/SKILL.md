---
name: setup
description: Set up jev on this machine: the API key (TypeSafe or OpenRouter), the backend, and a health check.
disable-model-invocation: true
allowed-tools: ["Bash(jev *)"]
---

Set jev up. Never ask the person to paste an API key into the chat, and never run a command that
contains one.

1. Run `jev doctor`. If the key step fails, tell the person to run **in their own terminal** one of:
   - `jev auth set <key>` with a TypeSafe key from console.typesafe.ai, or
   - `jev auth set <key>` with an OpenRouter key (`sk-or-…`): the OpenRouter backend is configured
     automatically; `jev config set backend typesafe|openrouter` switches later.
   The plugin's own settings (`/config`, jev section) accept the key too; it is written to the key
   file at the next session start.
2. Run `jev doctor` again and report the backend, the model and the round-trip time.
3. Offer the three commands that pay first: `jev sift` before reading, `jev tests` on a change,
   `jev session` for what it saved.

---
name: stats
description: Show what jev decided in this session, what that text would have cost the agent to read, and the session's estimated spend.
disable-model-invocation: true
allowed-tools: ["Bash(jev *)"]
---

## This session, as measured

```!
jev session --plain
```

Explain the three rows briefly to the person: "went through jev" is measured (decisions, tokens
of text jev read instead of you); "would have cost" is that text priced at your input rate, once,
plus its re-read on later turns; "saved" is the difference, a ceiling because the uncertain band
was read anyway. The model side is an estimate from the transcript at list price.

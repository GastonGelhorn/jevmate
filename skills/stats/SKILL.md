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

Explain the rows briefly to the person: "went through jev" is everything jev handled, hooks
included; "kept out" is only the text jev judged instead of the agent reading it, plus trimmed
output (the safety checks are listed apart because the agent would not have read that text);
"would have cost" prices each piece at the model of the turn that would have read it, once, then as
cache reads until the next compaction; "saved" is the difference, a ceiling because the uncertain
band was read anyway. On a subscription the dollars are an API equivalent and the "plan" row gives
the share of the 5-hour window and of the week. The model side is an estimate from the transcript.
If the person wants these numbers without asking, they are on the line above the prompt
(Claude Code 2.1.287 or later), and `/jevmate` opens the same table in a pane.

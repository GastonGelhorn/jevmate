---
name: review
description: Review the current diff where being wrong costs most: every hunk rated by risk, and hunks outside the stated task flagged.
argument-hint: "[what the change was supposed to do]"
disable-model-invocation: true
allowed-tools: ["Bash(jev *)", "Bash(git diff *)", "Read"]
---

Review the diff by risk. Task description: $ARGUMENTS

1. Run `jev diff --min-level shared` (with `--task "<description>"` when one was given, `--ref` or
   `--staged` as the person's setup needs). Hunks come back rated cosmetic / local / shared / critical.
2. Read the critical and shared hunks first, in that order, and any hunk flagged `outside the task?`.
3. Report findings ordered by risk, with `file:line`. On the person's own diff, an out-of-scope flag is a
   question to answer before committing, not after; raise it explicitly.

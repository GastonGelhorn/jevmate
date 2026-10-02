---
name: triage
description: Triage a large file of items (tickets, notes, commits, log lines) with one calibrated yes/no per item, without reading them all.
argument-hint: "<file> <question about `candidate`>"
disable-model-invocation: true
allowed-tools: ["Bash(jev *)", "Read", "Write", "Agent"]
---

Triage many items with jev instead of reading them. Input: $ARGUMENTS (a file, then the question;
the question refers to each item as `candidate`).

1. If a saved question fits (`jev q list`), use it: `jev rank --q <name> --query "<topic>" --candidates-file <file>`.
   Otherwise run `jev rank --query "<topic>" --instructions '<question>' --candidates-file <file> --abstain 0.35 0.65 --uncertain-out review.txt`.
   JSONL rows: use `jev batch --input <file> --state-key text --noul q '<question>' --abstain 0.35 0.65 --uncertain-out review.jsonl`.
2. The confident tail is decided. The band in `review.txt` is not: hand it to the `jevmate:band-reader` agent
   (cheaper model, its own context) with the question, or read it yourself when it is short.
3. Report counts, the decided lists, and how many rows went to review. When this question will run
   again, label 30 rows and run `jev tune … --save <name>` so the threshold is measured, not guessed.

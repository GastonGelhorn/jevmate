---
name: sift
description: Rank files, functions or grep hits by how worth reading they are for a question, before reading any of them.
argument-hint: "<what you are looking for> [paths…]"
disable-model-invocation: true
allowed-tools: ["Bash(jev *)", "Read"]
---

Decide what to read before reading it. The question is: $ARGUMENTS

1. Run `jev sift --query "<the question>" <paths, or . when none were given> --top 8`. For a huge tree,
   narrow first with `rg -l <keyword> | jev sift --files-from - --query "<question>"`, or grade single
   functions of one big file with `--functions`.
2. Each line is `p  tokens-to-read  path`. Read the top entries in order and stop when a file answers
   the question; never read past a `--- budget` marker without saying why.
3. Answer with what you found and the files you read, citing `path:line`.

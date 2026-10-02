---
name: tests
description: Run first the test files that exercise the current change, ranked by jev against the diff.
argument-hint: "[ref, e.g. main] "
disable-model-invocation: true
allowed-tools: ["Bash(jev *)", "Bash(git diff *)"]
---

Find the tests this change can break and run those first. Reference: $ARGUMENTS (empty means the
working tree against HEAD; `--staged` is also accepted).

1. Run `jev tests --top 5` (add `--ref <ref>` or `--staged` when given). Name matches and test files
   the diff touched are flagged: they are nearly certain.
2. Run the top files with the project's runner (`jev tests --top 5 --paths-only | xargs <runner>`).
3. Report what ran and what failed. The full suite still runs before the change is called done;
   say so if it has not.

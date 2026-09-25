---
name: pr
description: Review a pull request by risk: rate every hunk, flag what does not belong to the PR's stated purpose, name the tests to run, and hand the critical hunks to a reviewer agent.
argument-hint: "<pr number or url> [repo]"
disable-model-invocation: true
allowed-tools: ["Bash(gh *)", "Bash(jev *)", "Agent", "Read"]
---

Review pull request $ARGUMENTS where being wrong costs most.

1. `gh pr view <pr> --json title,body,baseRefName,url` for the purpose; `gh pr diff <pr> > /tmp/jev-pr-<pr>.diff`.
2. `jev diff --diff /tmp/jev-pr-<pr>.diff --task "<title>: <first line of the body>" --min-level shared`: hunks rated
   cosmetic / local / shared / critical, and hunks that do not belong to the task flagged.
3. `jev tests --diff /tmp/jev-pr-<pr>.diff --top 5`: the test files that exercise the change.
4. Delegate the reading to the `jev:reviewer` agent: give it the diff path, the rated hunk list and the
   task description. It reads the critical and shared hunks and reports findings with `file:line`.
5. Reply with: findings by risk, the out-of-scope hunks as questions for the author, the tests to run,
   and nothing about hunks jev rated cosmetic unless the agent found something there.

# Patterns that compose jev into systems
live: /patterns

**Filter, then read.** Grade everything with one cheap question, read only the top of the list
(`jev sift`, `jev rank --top`). The model decides what you read first; you still read it.

**Speculative fan-out.** Put every question the workflow might need into one request, including
ones that only matter on some branch, and let the code ignore the rest. A second request only when
the first answer is needed to build the second state.

**Three buckets.** yes / no / review. The review bucket goes to a person or to a slower model in a
small batch. `--abstain LO HI` on the list commands, `Decision.unsure` in Python.

**Greedy clustering against representatives.** The first unassigned item represents a cluster,
every remaining item is graded against it in one request, the joiners leave the pool, the next
unassigned item starts the next cluster. k clusters cost k requests (`jev cluster`).

**Diff as the state, the rest as candidates.** Which test files exercise this change, which
failures did it cause, which hunks are risky (`jev tests`, `jev failures`, `jev diff`). One
request per question whatever the size of the change.

**Semantic grep.** Lines batched by count or by time into one request each, printed with their
p as decided (`jev stream`).

**Guardrails.** A calibrated yes/no on a tool call before it runs, or on fetched text before the
model reads on. Advisory: ask, never allow; deny only where no prompt can appear (`jev guide hooks`).

**Verify your own output.** Claim against source, draft against a stated rule, tool call against
the stated intent. A second model that is not you, at $0.00004 per check.

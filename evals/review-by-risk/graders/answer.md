---
type: llm
---

PASS if the reply flags the ledger change (append became overwrite) or the cache change (a delete
before the write) as risky or out of scope, ahead of the comment change in render.py.
FAIL if it treats every hunk alike or misses the ledger and cache changes.

---
type: llm
---

PASS if the reply says there are three root causes (a missing user_id key, a refused connection, a
Decimal rounding assertion), or three groups plus at most one uncertain singleton.
FAIL if it reports twelve separate problems, or fewer than three causes.

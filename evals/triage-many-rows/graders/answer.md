---
type: llm
weight: 2
---

PASS if the reply lists ticket ids and says that the confident set was decided by a tool while any
uncertain ones were set aside or read separately, or lists ids that are clearly refund requests.
FAIL if the reply reads every ticket into the conversation one by one, or gives no ids.

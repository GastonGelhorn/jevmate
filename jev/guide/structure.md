# Structured instructions and criteria
live: /primitives/advanced

`instructions` and every `criteria` entry may be a JSON object or array instead of a string. The
model reads either; the structure is for you, to keep the question and its parts apart:

    {
      "type": "noul",
      "instructions": {"question": "Is `candidate` caused by the same problem as `query`?",
                       "criteria": {"true": "the same defect, even when names and values differ",
                                    "false": "a different cause that merely looks alike"}},
    }

This is how the list commands work: `jev rank` sends `{"question": …, "candidate": <item>}` as the
instructions of each question, with the shared `query` (and `context`) in the state. A score level
may be an object too (`{"level": "blocking", "what": "…"}`); `jev rate` shows the level's name from
`what`, `level`, `name` or `description`, in that order.

Keep the structure shallow. It buys separation, not accuracy.

# Writing instructions and criteria
live: /primitives

The model answers the words, not the intent behind them.

- One judgment per question. "Is it urgent and about billing?" is two questions; ask two.
- State the exact condition. Whatever you would add when explaining "what I really meant" belongs
  in the instructions or the criteria, not in your head.
- Name the state's fields in backticks: `ticket.messages[0].text`, `candidate`, `query`. Question
  ids are for you; the model never sees them.
- Never index into a long array. Measured on 320 items, one yes/no each: `candidates[i]` was wrong
  86 times at 150 items per request and 29 times at 25; a keyed object (`candidates.k137`) or the
  item embedded in its own question was wrong 0 times at 320 per request. `jev rank`, `jev sift`
  and every list command embed for you.
- Send only the state the questions need. Irrelevant context lowers accuracy and costs tokens.
- Phrase a noul so that a high number means yes. Add `true=` / `false=` criteria when the boundary
  is subtle: what counts, what does not, and the near miss that should fall on each side.
- Give a choice option a description that separates it from its neighbour; add `other` when the
  list may not cover the input. Up to 255 options; send the whole list every time.
- Give a score 2 to 10 levels, low to high, each a concrete situation ("broken, a workaround
  exists"), never a bare number or "moderate". One dimension per score.
- Non-English works but is weaker; watch the confidence, and keep the criteria in the language of
  the state where you can.

Instructions and criteria may be JSON objects instead of strings (`jev guide structure`), which
is how the list commands carry the question and the item together.

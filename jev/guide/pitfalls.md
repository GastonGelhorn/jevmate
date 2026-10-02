# Jagged edges of the current model
live: /model-jaggedness/jev-1.13

Things the model is bad at, measured or vendor-acknowledged. Keep them in code, not in a question.

- Arithmetic, counting, comparing numbers, date arithmetic.
- Double negatives and questions with an implicit "unless".
- Positions in long arrays (`items[137]`): 27% wrong at 150 items per request. Key the object or
  embed the item.
- Multi-hop reasoning: "if A then is B implied by C". Split it, or let the agent do it.
- Two questions expected to satisfy an identity (P(x) + P(not x) = 1). They will not.
- Non-English input works but is weaker; check the confidence and prefer criteria in the same
  language as the state.
- Very long single states near the ceiling: accuracy drops before the 400 does. Send less.
- Run-to-run jitter of about ±0.02 on identical requests; a bar placed exactly on a cluster of
  rows will flip some of them. Use a band.

# Probabilities, confidence, and acting on them
live: /confidence

A noul returns a probability; choice and score return a value plus a `confidence` (how peaked
the distribution behind it is). Both are calibrated in the statistical sense: over many
answers, the number tracks how often the model is right. Neither is a promise about one answer.

## Three bands

    high    act                     (read-only around 0.6, destructive around 0.9)
    middle  confirm, or gather more (a person, a second source, a richer state)
    low     do not act; escalate or ask

The bands are per question and per stake, never shared across questions. `jev tune` measures
where they sit for your question on your rows and prints the abstention bands with the accuracy
each one buys.

## Jitter

Identical requests do not return identical numbers: six in a row to one concrete model gave
0.88 0.89 0.90 0.89 0.89 0.88. Budget about ±0.02. A row that close to your threshold can land on
either side between runs; `jev tune` counts those rows as flip risk, and `--abstain` around the
threshold is the fix. The answer cache keeps the first sample, so re-runs are stable and free.

## Is p a probability or a ranking?

`jev tune` prints a reliability table: per bin, the mean p against the share of rows that were
actually positive, and ECE, their size-weighted gap. 0 is perfect; from 0.10 up the number still
ranks items correctly but does not mean what it says, so use it to sort, not to act at a fixed bar.

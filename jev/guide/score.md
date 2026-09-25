# Score: a position on an ordered rubric
live: /primitives/score

    jev rate 'How severe is the bug in `text`?' "cosmetic" "degraded, a workaround exists" "blocking" -s @report.txt
    jev rank --query "…" --candidates-file items.txt --levels "irrelevant" "related" "answers it exactly"

The answer is `{"score", "legend", "probabilities", "confidence"}`. `score` runs from 0 to N-1 and
may land between levels (1.7 is "between degraded and blocking, closer to blocking"). Round it for
one outcome, sort by it for a ranking, divide by N-1 to combine it with other scores.

Rules that hold up: 2 to 10 levels, low to high, each a concrete situation rather than a number or
an adjective; one dimension per rubric (severity OR effort, not both); the top level is where
being wrong costs the most, since that is the one you will act on.

`jev diff` rates hunks on a four-level risk rubric and `jev failures` rates flakiness on three.
Both show the rubric they use; copy the shape.

# The playbook: when to reach for jev, how to ask, how to read the answer
live: /concepts/how-to-build-with-system-one

jev is a decision model with a command line around it. It does not write text. It takes a STATE
(text or JSON) and typed QUESTIONS, and returns typed answers with calibrated probabilities in
about 250 ms, for $0.042 per million input tokens. You are the reasoning model; jev is the gut
check you can run ten thousand times.

## Three question shapes

    noul    "is this true?"          -> P(true), 0..1
    choice  "which one of these?"    -> the option, P per option, confidence
    score   "where on this rubric?"  -> a position between your levels, confidence

Several questions about one state travel in one request and are answered independently, so an
extra question is nearly free. Ask everything the workflow might need; ignore what you do not use.

## Reach for it when

- a judgment REPEATS over many items you should not read into your context: classify, filter,
  rank, dedupe, triage hundreds of notes, tickets, log lines, findings (`jev rank`, `jev batch`)
- a search returns more files or hits than you should read (`jev sift` decides what to read first)
- a task says "go through these thousands of rows and decide X" (`jev scaffold`: a script with
  a three-way semantic `if`, run unattended)
- you are about to act on your own confidence, which is not calibrated; jev's is trained to be
- a tool, hook or cron job needs a judgment with no agent in the loop
- you want an independent check on your own output: does the source support this claim, does the
  draft break a stated rule, does this fetched text carry instructions aimed at an agent, is this
  command destructive

Not for: generating or rewriting text, arithmetic, counting, dates, multi-step reasoning, or one
small item you can judge faster than a shell call. Keep arithmetic in code.

## Three habits

1. Before you read, `jev sift`. Your scarce resource is the context window.
2. Before a question decides anything at volume, `jev tune` it on thirty labelled rows. The
   threshold alone moved one question from 63.8% to 76.2% accuracy; four phrasings of the same
   judgment spanned ten points.
3. Hand off what jev cannot decide: `--abstain LO HI --uncertain-out review.txt`. The confident
   tail of a 76% question was 100% right; the band was where it was wrong.

## Writing the question

jev reads literally. One judgment per question; state the exact condition; name the fields of the
state in backticks (`ticket.message`); put the item INSIDE the question rather than pointing at an
index in a long array (`items[137]` mis-locates past about twenty items; `jev rank` embeds for you).
`jev guide questions` has the rest.

## Reading the answer

A noul is P(yes): 0.5 is undecided, not medium. A choice returns the argmax and a confidence that
says how peaked the distribution is; a runner-up with real mass is information. A score can land
between levels: round for one outcome, sort for a ranking. Thresholds scale with stakes: around
0.6 for a read-only action, around 0.9 for a destructive one, and never carried over from one
question to another. `jev guide confidence` goes deeper.

## In a shell

Single-quote any question that contains backticks; in zsh, double quotes run them as a command.
`--json` gives the raw response; `--dry-run` shows the exact request and sends nothing.

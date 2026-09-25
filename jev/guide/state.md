# State: what you hand the model
live: /concepts/state

The state is everything the judgment needs, and nothing else. Give it as one text, or as an
object with named fields the questions can point at:

    jev ask --field msg=@ticket.txt --field policy=@policy.md --field orders=@orders.json --field tier='"gold"' \
            --noul covered 'Does `policy` cover the charge in `orders[0]`?' --noul urgent 'Does `msg` need a reply today?'

Each `@file.json` is parsed, so nested structure survives and `orders[0].amount` works. Also
accepted: `--state-file report.md` (a document), `--state-json '["turn 1", "turn 2"]'` (an ordered
array), text on stdin, or `-f request.json` carrying state and questions together.

## The ceiling

Measured: 107,500 characters (32,388 tokens) was the last state the API accepted; above that it
answers `400 max_tokens_exceeded`. English prose runs about 3.32 characters per token, so
`jev cost --state-file f` prices a state before you send it. For anything larger, cut it or use
a list command: `jev rank`, `jev batch`, `jev sift` pack items into requests under the ceiling.

## Ordering and positions

Short arrays with keyed objects are fine. Long arrays addressed by position are not (`jev guide
questions`). When the judgment is about one item among many, put the item in the question and
the shared context in the state; every list command does exactly that.

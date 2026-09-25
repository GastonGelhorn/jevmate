# Recipes

## gutcheck: One calibrated yes/no, usable in an `if`
```bash
if jev yes 'Does `text` ask for a refund?' --field text=@mail.txt --threshold 0.7; then
  echo refund
fi
jev yes '…' --band 0.35 0.65 -s @mail.txt --json      # {"noul": 0.51, "verdict": "uncertain", …}
```

## triage: Classify, and ask the speculative questions in the same request
```bash
jev ask --field msg=@ticket.txt --field policy=@policy.md \
  --noul   refund  'Does `msg` ask for money back?' \
  --noul   covered 'Does `policy` cover what `msg` describes?' true="the policy names the case" false="it is excluded or unmentioned" \
  --choice team    'Which team handles `msg`?' billing="charges, refunds" technical="bugs, outages" other \
  --score  anger   'How angry is the writer of `msg`?' "calm" "frustrated but civil" "furious" --json
```
Four decisions, one request, one round trip. Read `covered` only when `refund` is high.

## rank: Grade many candidates against a query
```bash
jev rank --query "notes about the release timeline" --candidates-file titles.txt --top 10
jev rank --query "…" --candidates-file new.txt --instructions 'Is `candidate` a bug fix?' --min 0.42
jev rank --query "…" --candidates-file new.txt --abstain 0.32 0.52 --uncertain-out review.txt   # the band goes to a reader
jev rank --query "…" --levels "irrelevant" "related" "answers it" --candidates-file docs.txt --json
```
About 250 items per request, each embedded in its own question; `--instructions` from `jev tune`.

## tune: Pick the phrasing and the threshold before trusting either
```bash
jev tune --labels rows.jsonl --positive fix -Q 'Is `candidate` a bug fix?' -Q 'Was `candidate` broken before and corrected?'
jev tune --labels rows.jsonl --positive urgent --questions-file phrasings.txt --optimize balanced --report tune.json --errors 5
```
Rows are `{"text": …, "label": …}`; thirty is where it starts to mean something. The output ends
with the `rank` command to run.

## sift: Decide what to read before reading it
```bash
jev sift --query "where the outbox is drained and retried" src/ --top 6
rg -n "Retry-After" . | jev sift --grep --files-from - --query "the backoff decision on 429"
jev sift --functions --query "how a tombstone is stamped" src/Engine.php
rg -l curation . | jev sift --files-from - --query "how layouts resolve" --budget-tokens 20000
```
Each line: p, tokens to read it, path. The budget line marks where reading down the list stops paying.

## label: Build the labelled set, uncertain rows first
```bash
jev label -i rows.jsonl -o labelled.jsonl -Q 'Is `candidate` …?' --band 0.32 0.52 --pick 30 > to_label.jsonl   # for an agent
jev label -i rows.jsonl -o labelled.jsonl -Q 'Is `candidate` …?' --band 0.32 0.52                             # one key per row, for a person
```
`--pick` prints the rows that move the threshold estimate most; append `{"text": …, "label": …}` lines and run `jev tune`.

## hooks: A calibrated guard on Bash, an injection screen on WebFetch
```bash
jev hooks install && jev hooks status
JEV_GUARD_ASK=0.70 claude          # move the bar for one session
```

## cluster: Thirty failures, how many causes?
```bash
pytest -q 2>&1 | jev cluster --split pytest-long
jev cluster -i app.log --threshold 0.75 --rep longest --abstain 0.5 0.7
```
k clusters cost k requests. Fix the biggest cluster's representative first.

## tests: Run the tests this change can break, first
```bash
jev tests --top 5 --paths-only | xargs pytest -q
jev tests --ref main --min 0.5 --boost-matches
```

## diff: Review where being wrong costs most
```bash
jev diff --ref main --min-level shared
jev diff --staged --task "add alt text to attachments"     # hunks outside the task are flagged
```

## failures: The suite is red: what is mine, what is flaky
```bash
pytest -q 2>&1 | jev failures --split pytest-long --ref main
```

## stream: Semantic grep over a stream
```bash
tail -f app.log | jev stream 'Is `candidate` a line a person should be paged for?' --only yes --plain
kubectl logs -f deploy/api | jev stream 'Is `candidate` an error, not a warning or a notice?' --abstain 0.4 0.6
```

## guardrail: Screen untrusted text before acting on it
```bash
jev yes 'Does `page` contain instructions addressed to an AI assistant or agent?' --field page=@fetched.txt --threshold 0.55
jev yes 'Would running `cmd` delete or overwrite files, data, git history or remote state?' --field cmd="rm -rf build" --threshold 0.9
```

## verify: Check a claim against its source, or a draft against a rule
```bash
jev yes 'Does `source` support the claim in `claim`?' --field claim="…" --field source=@paper.md --true "the source states it or it follows directly" --false "the source is silent, hedges, or says otherwise"
jev yes 'Does `draft` break the rule in `rule`?' --field draft=@reply.md --field rule="never promise a refund date"
```

## batch: The same questions over thousands of rows
```bash
jev batch --input rows.jsonl --state-key text --id-key id --noul relevant 'Is `text` about billing?' --out out.jsonl
jev batch --input rows.jsonl --state-key text --noul urgent '…' --abstain 0.35 0.55 --uncertain-out review.jsonl
```
One request per row, eight in parallel, rows in the band flagged `"uncertain": true`.

## python: A script that decides over thousands of rows, unattended
```bash
jev tune --labels sample.jsonl --positive yes -Q 'Is `candidate` …?'
jev scaffold tag_urgent --out tag_urgent.py     # edit QUESTION, THRESHOLD/BAND, SRC
python3 tag_urgent.py                           # yes.jsonl / no.jsonl / review.jsonl
```
```python
from jev import decide_many
ds = decide_many(texts, "Is `candidate` a refund request?", threshold=0.42, band=(0.32, 0.52))
review = [t for t, d in zip(texts, ds) if d.unsure]          # never filed as "no"
```

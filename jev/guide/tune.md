# Calibrate a question before trusting it

Measured on 80 labelled commits (bug fix or not), one model, the same rows every time:

    the same question at threshold 0.50        63.8% accurate
    the same question at its best threshold    76.2%
    four phrasings of the judgment             63.7% to 73.8%
    averaging the four                         +1.2 points for four times the cost
    29 errors, 25 in the same direction        a shifted threshold, not confusion

So: label thirty rows by hand (`jev label` orders them so the useful ones come first), then

    jev tune --labels rows.jsonl --positive fix -Q 'Is `candidate` a bug fix?' -Q 'Was `candidate` broken before and corrected?'
    jev tune --labels rows.jsonl --positive urgent --questions-file phrasings.txt --optimize balanced --report tune.json --errors 5

Per question you get the best threshold, accuracy, balanced accuracy, F1, precision, recall, the
score at 0.50 for contrast, and ECE. For the winner: the confusion matrix (errors leaning one
way means the threshold, not the model), flip risk (rows within ±0.02 of the threshold, where
run-to-run jitter can flip them), the reliability table, abstention bands, and the exact `rank`
command to run. `--errors N` lists the most confident errors: the model is sure and the label
disagrees, and on the 80 commits those were the author's own ambiguous prefixes. Check the
labels before blaming the model.

A held-out check: the threshold found on 80 rows (0.42), applied unchanged to 80 fresh rows (28
from sibling repositories, 52 older commits), scored 78.8%. Pick the phrasing and threshold once, pin the
model (`--model jev-1.13.0`), and stop re-asking: the cache makes iteration free, and a fresh
call buys jitter, not information.

Do not ensemble phrasings. Pick the best one.

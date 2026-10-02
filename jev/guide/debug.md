# Debugging: cluster the failures, run the right tests first, review by risk

**Thirty red tests are rarely thirty problems.**

    pytest -q 2>&1 | jev cluster --split pytest-long        # or: pytest, phpunit, jest, tap, go, blank, a regex
    jev cluster -i app.log --threshold 0.75 --rep longest    # log lines; the fullest trace represents its cluster

Twelve planted failures with three causes came back as four clusters in three requests and one
second: two causes grouped perfectly, the third missed one member at p=0.67 against a 0.70 bar,
and `--abstain 0.5 0.7` named it as a near miss. Read the representative of the biggest cluster,
fix that, re-run.

**Run the tests this change can break, first.**

    jev tests --top 5 --paths-only | xargs pytest -q        # git diff HEAD; --staged; --ref main; --diff -
    jev tests --ref main --min 0.5 --boost-matches

The diff is the state; every test file is a candidate described by its head and its test names.
Name matches (Outbox.php ~ OutboxTest.php) and test files the diff itself touched are flagged for
free. On a twelve-file PHPUnit suite the right file ranked 0.98 and 0.95 on two different changes,
with everything else at or under 0.17 and 0.59. The full suite still runs before you call it done.

**The suite is red: what is mine, what is flaky.**

    pytest -q 2>&1 | jev failures --split pytest-long       # p(caused by this diff) and a deterministic / environmental / flaky rating

Two requests in total. Fix what is yours; rerun what looks flaky before investigating it.

**Review where being wrong costs most.**

    jev diff --ref main --min-level shared
    jev diff --staged --task "what the change was supposed to do"   # hunks that do not belong get flagged

Every hunk rated cosmetic / local / shared / critical; with `--task`, scope creep is named. On
your own diff, an out-of-scope flag is a question to answer before the commit.

**Semantic grep over a stream.**

    tail -f app.log | jev stream 'Is `candidate` a line a person should be paged for?' --only yes --plain

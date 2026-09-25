# Designing a new integration: a tool, a cron job, a pipeline
live: /concepts/how-to-build-with-system-one

Before writing the first line:

1. Name the decision in one sentence with a yes/no, a choice, or a rubric in it. If you cannot, it
   is not one decision yet.
2. Decide what stays in code: arithmetic, counting, dates, exact matches, joins. jev is for the
   judgment that a regex cannot express.
3. Write the state as an object with named fields. Send only what the judgment needs.
4. Label thirty rows and run `jev tune`. Take its threshold and its band.
5. Choose the three outcomes: what happens on yes, on no, and on unsure. Unsure must go somewhere
   a person will look.
6. Pin the model. Aliases move; a tuned threshold is tied to the model it was tuned on.
7. Give the client a `label`, so `jev usage --by cmd` can tell this job from the others.

Ship checklist: retries are built in (the client backs off on 429 and 5xx and spaces itself under
the published rate limit); a network failure exits 5 and an API failure 4, so a cron line can tell
them apart; the cache makes a re-run of the same rows free; `--dry-run` shows the exact request
without a key. `jev scaffold` writes the script skeleton with all of this in place.

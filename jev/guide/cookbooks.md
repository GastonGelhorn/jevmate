# Vendor cookbooks
live: /cookbooks/parallel_questions

The vendor publishes worked examples (parallel questions, routing, extraction shapes, guardrails)
as Markdown at docs.typesafe.ai; `jev docs` prints the live index and `jev docs --grep cookbook`
narrows it. `jev guide cookbooks --live` fetches the parallel-questions page, which is the one to
read first: it shows why several questions in one request beat several requests.

The recipes shipped here (`jev examples`) cover the same ground from the command line, with the
measurements behind each default.

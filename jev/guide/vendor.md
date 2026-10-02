# The vendor's own agent skill
live: https://raw.githubusercontent.com/typesafe-ai/skills/main/skills/typesafe-ai/SKILL.md

TypeSafe publishes a skill of their own at github.com/typesafe-ai/skills. It orients an agent
toward the documentation for BUILDING an integration (which page to read for which task, request
shapes, design guidance) and ships no executable. This tool's skill is about USING jev from a
shell and a script, day to day. They overlap on the triggers, so only one should be installed as
a skill; `jev guide vendor --live` prints theirs whenever you want the vendor's current view.

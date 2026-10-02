# Exit codes

    0   answered: yes (jev yes), confident (pick / rate), or the job ran clean
    1   answered: no or uncertain (jev yes / pick / rate)
    2   usage error: bad flags, missing state, invalid JSON
    3   no API key, or the key was rejected (401 / 403)
    4   the API failed after retries, or a batch had per-row errors
    5   network failure after retries
    130 interrupted

A shell `if jev yes …; then` reads 0 as yes and everything else as no, which is what you want for
a read-only action and not what you want for a destructive one: check `$?` for 1 explicitly, or
use `--band` and read the word.

"""jev: calibrated yes/no, pick-one and rubric decisions for coding agents.

    from jev import Client, noul, choice, score, decide, decide_many

    r = Client(label="triage").ask({"ticket": text}, {"urgent": noul("Does `ticket` need a reply today?")})
    p = r["answers"]["urgent"]["noul"]

The heavy modules load on first use, so `import jev` stays cheap for hooks and scripts.
"""

from ._version import VERSION
from .errors import AuthError, DryRun, JevError, NetworkError, UsageError
from .questions import choice, noul, score

__all__ = ["VERSION", "AuthError", "DryRun", "JevError", "NetworkError", "UsageError", "choice", "noul", "score",
           "Client", "JevClient", "ask", "Decision", "decide", "decide_many", "grade"]

_LAZY = {"Client": ("client", "Client"), "JevClient": ("client", "Client"), "ask": ("client", "ask"),
         "Decision": ("decide", "Decision"), "decide": ("decide", "decide"), "decide_many": ("decide", "decide_many"),
         "grade": ("grading", "grade")}


def __getattr__(name):
    try:
        module, attr = _LAZY[name]
    except KeyError:
        raise AttributeError(f"module 'jev' has no attribute {name!r}") from None
    from importlib import import_module
    value = getattr(import_module(f".{module}", __name__), attr)
    globals()[name] = value
    return value

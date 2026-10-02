"""Exit codes are part of the contract: scripts branch on them.

    0  answered (yes / ok)        2  usage error
    1  answered (no / uncertain)  3  no key, or the key was rejected
    4  the API failed after retries, or a job had errors
    5  network failure after retries
"""


class JevError(Exception):
    exit_code = 4


class UsageError(JevError):
    exit_code = 2


class AuthError(JevError):
    exit_code = 3


class NetworkError(JevError):
    exit_code = 5


class DryRun(Exception):
    """Raised instead of sending when --dry-run asked to see the request. Carries it."""

    def __init__(self, url: str, body: dict):
        super().__init__(url)
        self.url = url
        self.body = body

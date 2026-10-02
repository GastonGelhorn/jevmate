"""guide, examples, docs: the playbook shipped with the tool, and the vendor's live pages."""

from __future__ import annotations

import re
from pathlib import Path

from ..errors import NetworkError, UsageError
from ..settings import DOCS_URL

GUIDE_DIR = Path(__file__).resolve().parent.parent / "guide"
ORDER = ["when", "questions", "noul", "choice", "score", "state", "structure", "confidence", "tune", "patterns", "design",
         "library", "debug", "hooks", "usage", "api", "pitfalls", "cookbooks", "vendor", "exit-codes", "install"]
_EXAMPLES: dict[str, tuple[str, str]] | None = None


def register(sub) -> None:
    g = sub.add_parser("guide", help="the playbook: `guide` for the overview, `guide <topic>`, `guide --list`")
    g.add_argument("topic", nargs="?")
    g.add_argument("--list", action="store_true")
    g.add_argument("--live", action="store_true", help="fetch the vendor's current page for this topic instead")
    g.set_defaults(fn=cmd_guide)

    e = sub.add_parser("examples", help="copy-paste recipes: `examples <name>`, `examples all`")
    e.add_argument("name", nargs="?")
    e.set_defaults(fn=cmd_examples)

    d = sub.add_parser("docs", help="the vendor's live documentation index (llms.txt); --grep filters it")
    d.add_argument("--grep")
    d.set_defaults(fn=cmd_docs)


def _read_topic(name: str) -> tuple[str, str, str | None] | None:
    """(title, body, live url) from guide/<name>.md, or None."""
    try:
        text = (GUIDE_DIR / f"{name}.md").read_text()
    except OSError:
        return None
    lines = text.splitlines()
    title = lines[0].lstrip("# ").strip() if lines and lines[0].startswith("#") else name
    live = None
    body_start = 1
    for k in range(1, min(3, len(lines))):
        if lines[k].startswith("live:"):
            live = lines[k][5:].strip()
            body_start = k + 1
    body = "\n".join(lines[body_start:]).strip()
    return title, body, live


def topics() -> list[str]:
    found = sorted(p.stem for p in GUIDE_DIR.glob("*.md") if p.stem != "examples")
    return [t for t in ORDER if t in found] + [t for t in found if t not in ORDER]


def _live_url(live: str) -> str:
    return live if live.startswith("http") else f"{DOCS_URL}{live}.md"


def fetch(url: str, timeout: float = 20.0) -> str:
    import urllib.request

    from .._version import VERSION
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": f"jev/{VERSION}"}), timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        raise NetworkError(f"could not fetch {url}: {e}")


def cmd_guide(args) -> int:
    if args.list:
        for t in topics():
            title, _, live = _read_topic(t) or (t, "", None)
            print(f"{t:<12} {title}" + (f"   (live: {_live_url(live)})" if live else ""))
        return 0
    topic = args.topic or "when"
    got = _read_topic(topic)
    if got is None:
        raise UsageError(f"unknown topic {topic!r}. Topics: {', '.join(topics())}")
    title, body, live = got
    if args.live:
        if not live:
            raise UsageError(f"topic {topic!r} has no live page")
        print(fetch(_live_url(live)))
        return 0
    print(f"# {title}\n\n{body}\n")
    if live:
        print(f"Live source: {_live_url(live)}   (jev guide {topic} --live)")
    return 0


def examples() -> dict[str, tuple[str, str]]:
    global _EXAMPLES
    if _EXAMPLES is None:
        _EXAMPLES = {}
        try:
            text = (GUIDE_DIR / "examples.md").read_text()
        except OSError:
            text = ""
        for m in re.finditer(r"^## (\S+): (.+?)\n(.*?)(?=^## |\Z)", text, re.M | re.S):
            _EXAMPLES[m.group(1)] = (m.group(2).strip(), m.group(3).strip())
    return _EXAMPLES


def example(name: str) -> str:
    """The body of one example, for a command's --help epilog."""
    ex = examples().get(name)
    return f"example:\n{ex[1]}" if ex else ""


def cmd_examples(args) -> int:
    ex = examples()
    if not args.name:
        print("Copy-paste recipes. `jev examples <name>` prints one; `jev examples all` prints them all.\n")
        for k, (title, _) in ex.items():
            print(f"  {k:<10} {title}")
        return 0
    names = list(ex) if args.name == "all" else [args.name]
    for k in names:
        if k not in ex:
            raise UsageError(f"unknown example {k!r}. Names: {', '.join(ex)}")
        title, body = ex[k]
        print(f"## {title}\n{body}\n")
    return 0


def cmd_docs(args) -> int:
    txt = fetch(f"{DOCS_URL}/llms.txt")
    if args.grep:
        needle = args.grep.lower()
        txt = "\n".join(ln for ln in txt.splitlines() if needle in ln.lower())
    print(txt)
    return 0

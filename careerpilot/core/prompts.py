"""Load the AI instructions (playbooks). The person may override any file by putting their own copy
in <data folder>/playbooks/."""
import re
from pathlib import Path

from careerpilot.core.data import home

SHIPPED = Path(__file__).parent.parent / "playbooks"


def load(name: str) -> str:
    own = home() / "playbooks" / f"{name}.md"
    return (own if own.exists() else SHIPPED / f"{name}.md").read_text(encoding="utf-8")


def build(name: str, **parts: str) -> str:
    """Shared rules + the named playbook, with {part} placeholders filled in one pass: a value is never searched for
    placeholders itself, so a CV line or a posting can't pull another part (or the posting, outside its fence) in."""
    return re.sub(r"\{(\w+)\}", lambda m: parts.get(m[1], m[0]), load("rules") + "\n" + load(name))

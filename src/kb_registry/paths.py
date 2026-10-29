"""Which files a pack base may contain. Standard library only: the client imports this too."""

import re

_ALLOWED = re.compile(r"^(manifest\.json|base/links\.json|base/modules\.json|base/system\.md|base/graph/[A-Za-z0-9._-]+\.json)$")


def is_allowed_path(path: str) -> bool:
    if not path or ".." in path.split("/") or not _ALLOWED.match(path):
        return False
    return not path.startswith("base/graph/.")

_SAFE_ID = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._:-]{0,199}$")


def is_safe_id(value: str) -> bool:
    """Ids that become file names on every puller's machine: no separators, no leading dot, ≤200 chars."""
    return isinstance(value, str) and bool(_SAFE_ID.match(value)) and ".." not in value

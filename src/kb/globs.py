from fnmatch import fnmatch


def matches_any(rel_path: str, globs: list[str]) -> bool:
    """fnmatch where '*' also crosses '/', plus a leading '**/' matching zero folders."""
    for g in globs:
        if fnmatch(rel_path, g) or (g.startswith("**/") and fnmatch(rel_path, g[3:])):
            return True
    return False

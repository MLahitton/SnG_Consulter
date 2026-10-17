"""Structural path permissions; no diffing, mutation or semantic interpretation."""

import re
from collections.abc import Iterable


def _path_components(path: str) -> tuple[str | int, ...]:
    if not isinstance(path, str) or not path:
        raise ValueError("Resolution path must not be empty")
    parts = []
    for component in path.split('.'):
        match = re.fullmatch(r"([a-z][a-z0-9_]*)(?:\[([0-9]+)\])?", component)
        if match is None:
            raise ValueError("Invalid resolution path syntax")
        name, index = match.groups()
        parts.append(name)
        if index is not None:
            parts.append(int(index))
    return tuple(parts)


def path_is_within_allowed_root(path: str, allowed_root: str) -> bool:
    components, root = _path_components(path), _path_components(allowed_root)
    return components[:len(root)] == root


def normalize_allowed_change_paths(paths: Iterable[str]) -> list[str]:
    unique = {}
    for path in paths:
        components = _path_components(path)
        canonical = ''
        for part in components:
            canonical += f'[{part}]' if isinstance(part, int) else ('.' if canonical else '') + part
        unique.setdefault(components, canonical)
    # Retained roots keep their own first appearance, even if a parent occurs
    # later than a redundant descendant. Sibling indices remain independent.
    return [path for parts, path in unique.items()
            if not any(len(parent) < len(parts) and parts[:len(parent)] == parent for parent in unique)]

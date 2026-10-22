"""Canonical contract-order diff; immutable entity identities and safe path access."""

from copy import deepcopy

from pydantic import BaseModel

from .validation import _path_components


class UnsupportedTechnicalResolutionCompositionChangeError(ValueError):
    pass


_ENTITY_LISTS = {"modules": "module_id", "glass_panes": "glass_pane_id",
                 "decorative_elements": "decorative_element_id"}
_IMMUTABLE_IDS = {"identity_id", "technical_structure_id", *_ENTITY_LISTS.values()}


def structural_diff(before: BaseModel, after: BaseModel) -> list[str]:
    """Primitive lists are one change; entity lists retain exact IDs and order."""
    changes = []

    def visit(left, right, path="", name=""):
        if name in _IMMUTABLE_IDS and left != right:
            raise UnsupportedTechnicalResolutionCompositionChangeError("Technical identifiers must remain unchanged")
        if isinstance(left, BaseModel):
            if type(left) is not type(right):
                raise ValueError("Diff requires matching technical contracts")
            for key in type(left).model_fields:
                visit(getattr(left, key), getattr(right, key), f"{path}.{key}" if path else key, key)
        elif isinstance(left, dict):
            if not isinstance(right, dict) or set(left) != set(right):
                raise ValueError("Diff requires matching contract fields")
            for key in left:
                visit(left[key], right[key], f"{path}.{key}" if path else key, key)
        elif name in _ENTITY_LISTS:
            identifier = _ENTITY_LISTS[name]
            def identifiers(items):
                return [getattr(item, identifier) if isinstance(item, BaseModel) else item[identifier] for item in items]
            if identifiers(left) != identifiers(right):
                raise UnsupportedTechnicalResolutionCompositionChangeError(
                    "Entity addition, removal, reordering or identifier changes are unsupported")
            for index, (old, new) in enumerate(zip(left, right)):
                visit(old, new, f"{path}[{index}]")
        elif left != right:
            changes.append(path)

    visit(before, after)
    return changes


def apply_diff_paths(baseline: dict, proposed: dict, paths: list[str]) -> dict:
    """Copy only validated existing paths into an independent baseline representation."""
    result = deepcopy(baseline)
    for path in paths:
        components = _path_components(path)
        destination, source = result, proposed
        for part in components[:-1]:
            destination, source = _existing(destination, part), _existing(source, part)
        leaf = components[-1]
        _existing(destination, leaf)
        destination[leaf] = deepcopy(_existing(source, leaf))
    return result


def _existing(value, component):
    if ((isinstance(component, str) and isinstance(value, dict) and component in value)
            or (isinstance(component, int) and isinstance(value, list) and 0 <= component < len(value))):
        return value[component]
    raise ValueError("Apply path must refer to an existing contract component")

"""Deterministic checks of persisted technical results, without re-extraction."""

import re

from pydantic import BaseModel

from .models import FieldResolution, TechnicalExtractionInput, TechnicalExtractionResult, TechnicalResolutionStatus
from .service import _resolved_fields


def resolve_field_path(structure: BaseModel, path: str):
    if not isinstance(path, str) or not re.fullmatch(r"[a-z][a-z0-9_]*(?:\[[0-9]+\])?(?:\.[a-z][a-z0-9_]*(?:\[[0-9]+\])?)*", path):
        raise ValueError("Invalid technical field path syntax")
    value = structure
    for component in path.split('.'):
        match = re.fullmatch(r"([a-z][a-z0-9_]*)(?:\[([0-9]+)\])?", component)
        name, index = match.groups()
        if not isinstance(value, BaseModel) or name not in type(value).model_fields:
            raise ValueError("Technical field path does not exist")
        value = getattr(value, name)
        if index is not None:
            if not isinstance(value, list) or int(index) >= len(value):
                raise ValueError("Technical field path index does not exist")
            value = value[int(index)]
    return value


def validate_technical_result(result: TechnicalExtractionResult, evidence: TechnicalExtractionInput) -> None:
    if (result.batch_id != evidence.batch_id or result.source_identity_sha256 != evidence.source_identity_sha256
            or result.source_identity_gate_sha256 != evidence.source_identity_gate_sha256):
        raise ValueError("Technical Extraction is outdated relative to current Identity/Gate or batch")
    if ([s.identity_id for s in result.structures] != [i.identity_id for i in evidence.identities]
            or result.resolved_identity_count != len(evidence.identities)
            or result.unresolved_group_count != len(evidence.unresolved_groups)
            or result.unresolved_groups != evidence.unresolved_groups):
        raise ValueError("Technical targets, order, counters or unresolved groups are inconsistent")
    ids = {kind: set() for kind in ('structure', 'module', 'pane', 'decorative')}
    def unique(kind, identifier):
        if identifier in ids[kind]:
            raise ValueError("Duplicate technical IDs")
        ids[kind].add(identifier)
    for structure, identity in zip(result.structures, evidence.identities):
        unique('structure', structure.technical_structure_id)
        fields = list(_resolved_fields(structure))
        if any(not set(f.evidence_candidate_ids) <= set(identity.candidate_ids) for _, f in fields):
            raise ValueError("Technical evidence must belong to its Identity")
        missing = [p for p, f in fields if f.resolution == FieldResolution.UNKNOWN]
        status = (TechnicalResolutionStatus.AMBIGUOUS if any(f.resolution == FieldResolution.CONFLICT for _, f in fields)
                  else TechnicalResolutionStatus.PARTIAL if missing else TechnicalResolutionStatus.RESOLVED)
        if structure.missing_fields != missing or structure.resolution_status != status:
            raise ValueError("Technical missing_fields or resolution_status is inconsistent")
        for module in structure.modules:
            unique('module', module.module_id)
            for pane in module.glass_panes:
                unique('pane', pane.glass_pane_id)
            for decoration in module.decorative_elements:
                unique('decorative', decoration.decorative_element_id)

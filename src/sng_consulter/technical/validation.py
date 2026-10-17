"""Deterministic checks of persisted technical results, without re-extraction."""

import re
from typing import TYPE_CHECKING

from pydantic import BaseModel

from .models import FieldResolution, TechnicalExtractionInput, TechnicalExtractionResult, TechnicalResolutionStatus
from .service import _resolved_fields

if TYPE_CHECKING:
    from .audit.models import TechnicalAuditResult


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


def validate_technical_audit(audit: "TechnicalAuditResult", extraction: TechnicalExtractionResult,
                             evidence: TechnicalExtractionInput, extraction_hash: str) -> None:
    """Revalidate the persisted audit against its exact extraction and canonical targets."""
    if audit.batch_id != evidence.batch_id:
        raise ValueError("Technical Audit batch mismatch")
    if audit.audited_technical_extraction_sha256 != extraction_hash:
        raise ValueError("Technical Audit is stale relative to Technical Extraction; rerun audit before gate")
    if audit.structures_audited != len(extraction.structures) or audit.issue_count != len(audit.issues):
        raise ValueError("Inconsistent Technical Audit counters")
    identities = {i.identity_id: i for i in evidence.identities}
    structures = {s.identity_id: s for s in extraction.structures}
    structure_ids = {s.technical_structure_id: s for s in extraction.structures}
    positions = {s.technical_structure_id: n for n, s in enumerate(extraction.structures)}
    last_position = -1
    for n, issue in enumerate(audit.issues, 1):
        if issue.issue_id != f"technical-audit-{n:04d}":
            raise ValueError("Issue IDs must follow their exact deterministic sequence")
        if issue.identity_id not in identities or issue.technical_structure_id not in structure_ids:
            raise ValueError("Unknown issue Identity or structure")
        structure = structures[issue.identity_id]
        if structure.technical_structure_id != issue.technical_structure_id:
            raise ValueError("Issue structure does not belong to its Identity")
        position = positions[issue.technical_structure_id]
        if position < last_position:
            raise ValueError("Issue order conflicts with structure order")
        last_position = position
        if not issue.candidate_ids or not set(issue.candidate_ids) <= set(identities[issue.identity_id].candidate_ids):
            raise ValueError("Issue candidates must belong exclusively to its Identity")
        for path in issue.field_paths:
            resolve_field_path(structure, path)

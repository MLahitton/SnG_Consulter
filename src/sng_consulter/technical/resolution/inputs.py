"""Read-only reconstruction of work authorized by exact Extraction/Audit/Gate snapshots."""

from hashlib import sha256
from pathlib import Path

from pydantic import TypeAdapter

from sng_consulter.identity.models import BatchId
from sng_consulter.storage import prepared_file
from ..audit.models import TechnicalAuditResult
from ..gate import TechnicalGateResult, TechnicalGateStatus
from ..inputs import build_technical_extraction_input
from ..models import TechnicalExtractionResult
from ..validation import validate_technical_audit, validate_technical_result
from .models import TechnicalResolutionInput, TechnicalResolutionIssue, TechnicalResolutionTarget
from .validation import normalize_allowed_change_paths


class InvalidTechnicalResolutionInputError(ValueError):
    pass


def build_technical_resolution_input(
    batch_id: str, technical_root: Path = Path("runtime/technical"),
    identity_root: Path = Path("runtime/identity"), discovery_root: Path = Path("runtime/discovery"),
    batch_root: Path = Path("runtime/review/batches"), prepared_root: Path = Path("runtime/review/prepared"),
) -> TechnicalResolutionInput:
    try:
        batch_id = TypeAdapter(BatchId).validate_python(batch_id)
        root = Path(technical_root)
        directory = root / batch_id
        extraction_bytes, audit_bytes, gate_bytes = (
            prepared_file(root, directory, Path(name)).read_bytes() for name in
            ("technical_extraction.json", "technical_audit.json", "technical_gate.json"))
        extraction_hash, audit_hash, gate_hash = (
            sha256(raw).hexdigest() for raw in (extraction_bytes, audit_bytes, gate_bytes))
        extraction = TechnicalExtractionResult.model_validate_json(extraction_bytes)
        audit = TechnicalAuditResult.model_validate_json(audit_bytes)
        gate = TechnicalGateResult.model_validate_json(gate_bytes)
        evidence = build_technical_extraction_input(batch_id, identity_root, discovery_root, batch_root, prepared_root)
        validate_technical_result(extraction, evidence)
        validate_technical_audit(audit, extraction, evidence, extraction_hash)
        expected_status = (TechnicalGateStatus.BLOCKED_BY_TECHNICAL_ISSUES if audit.issues
                           else TechnicalGateStatus.READY_FOR_VERIFICATION)
        if (gate.batch_id != batch_id or gate.source_technical_extraction_sha256 != extraction_hash
                or gate.source_technical_audit_sha256 != audit_hash or gate.issue_count != audit.issue_count
                or gate.blocking_issue_ids != [i.issue_id for i in audit.issues] or gate.status != expected_status):
            raise ValueError("Technical Gate is stale or inconsistent with current Extraction/Audit")
        grouped = {}
        for issue in audit.issues:
            grouped.setdefault(issue.technical_structure_id, []).append(TechnicalResolutionIssue.model_validate(
                issue.model_dump(exclude={"technical_structure_id", "identity_id"})))
        targets = []
        for structure, identity in zip(extraction.structures, evidence.identities):
            issues = grouped.get(structure.technical_structure_id)
            if issues:
                targets.append(TechnicalResolutionTarget(identity_id=identity.identity_id,
                    technical_structure_id=structure.technical_structure_id, current_structure=structure,
                    issues=issues, allowed_change_paths=normalize_allowed_change_paths(
                        p for issue in issues for p in issue.field_paths), identity=identity))
        return TechnicalResolutionInput(batch_id=batch_id, source_technical_extraction_sha256=extraction_hash,
            source_technical_audit_sha256=audit_hash, source_technical_gate_sha256=gate_hash,
            resolution_required=bool(targets), targets=targets,
            resolved_identity_count=extraction.resolved_identity_count, unresolved_groups=extraction.unresolved_groups)
    except (ValueError, OSError):
        raise InvalidTechnicalResolutionInputError(
            "Missing, invalid, stale or inconsistent Resolution source artifacts / canonical evidence") from None

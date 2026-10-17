"""Offline gate over exact, validated Technical Extraction and Audit snapshots."""

from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from sng_consulter.identity.models import BatchId, Sha256
from sng_consulter.storage import prepared_file
from .audit.models import TechnicalAuditResult
from .inputs import build_technical_extraction_input
from .models import TechnicalExtractionResult
from .validation import resolve_field_path, validate_technical_result


class TechnicalGateStatus(StrEnum):
    READY_FOR_VERIFICATION = "READY_FOR_VERIFICATION"
    BLOCKED_BY_TECHNICAL_ISSUES = "BLOCKED_BY_TECHNICAL_ISSUES"


class TechnicalGateResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, revalidate_instances="always")
    batch_id: BatchId
    source_technical_extraction_sha256: Sha256
    source_technical_audit_sha256: Sha256
    status: TechnicalGateStatus
    issue_count: int = Field(ge=0)
    blocking_issue_ids: list[Annotated[str, Field(pattern=r"^technical-audit-[0-9]{4,}$")]]


class InvalidTechnicalGateInputError(ValueError):
    pass


class TechnicalGateService:
    def __init__(self, technical_root: Path = Path("runtime/technical"),
                 identity_root: Path = Path("runtime/identity"), discovery_root: Path = Path("runtime/discovery"),
                 batch_root: Path = Path("runtime/review/batches"), prepared_root: Path = Path("runtime/review/prepared")):
        self.technical_root = Path(technical_root)
        self.evidence_roots = (Path(identity_root), Path(discovery_root), Path(batch_root), Path(prepared_root))

    def gate(self, batch_id: str) -> TechnicalGateResult:
        try:
            batch_id = TypeAdapter(BatchId).validate_python(batch_id)
            directory = (self.technical_root.resolve() / batch_id).resolve()
            extraction_bytes = prepared_file(self.technical_root, directory, Path("technical_extraction.json")).read_bytes()
            audit_bytes = prepared_file(self.technical_root, directory, Path("technical_audit.json")).read_bytes()
            extraction_hash, audit_hash = (sha256(raw).hexdigest() for raw in (extraction_bytes, audit_bytes))
            extraction = TechnicalExtractionResult.model_validate_json(extraction_bytes)
            audit = TechnicalAuditResult.model_validate_json(audit_bytes)
            evidence = build_technical_extraction_input(batch_id, *self.evidence_roots)
            validate_technical_result(extraction, evidence)
        except (ValueError, OSError):
            raise InvalidTechnicalGateInputError("Missing, invalid or stale Technical Extraction / Audit / canonical evidence") from None
        if audit.batch_id != batch_id:
            raise InvalidTechnicalGateInputError("Technical Audit batch mismatch")
        if audit.audited_technical_extraction_sha256 != extraction_hash:
            raise InvalidTechnicalGateInputError("Technical Audit is stale relative to Technical Extraction; rerun audit before gate")
        if audit.structures_audited != len(extraction.structures) or audit.issue_count != len(audit.issues):
            raise InvalidTechnicalGateInputError("Inconsistent Technical Audit counters")
        identities = {i.identity_id: i for i in evidence.identities}
        structures = {s.identity_id: s for s in extraction.structures}
        structure_ids = {s.technical_structure_id: s for s in extraction.structures}
        positions = {s.technical_structure_id: n for n, s in enumerate(extraction.structures)}
        last_position = -1
        try:
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
        except ValueError as exc:
            raise InvalidTechnicalGateInputError(str(exc)) from None
        issue_ids = [issue.issue_id for issue in audit.issues]
        result = TechnicalGateResult(batch_id=batch_id, source_technical_extraction_sha256=extraction_hash,
            source_technical_audit_sha256=audit_hash,
            status=TechnicalGateStatus.BLOCKED_BY_TECHNICAL_ISSUES if issue_ids else TechnicalGateStatus.READY_FOR_VERIFICATION,
            issue_count=len(issue_ids), blocking_issue_ids=issue_ids)
        target = directory / "technical_gate.json"
        if not target.resolve().is_relative_to(directory):
            raise InvalidTechnicalGateInputError("Technical Gate output must stay within its batch")
        with TemporaryDirectory(prefix=".staging-", dir=directory) as staging:
            temporary = Path(staging) / "technical_gate.json"
            temporary.write_text(result.model_dump_json(indent=2), encoding="utf-8")
            temporary.replace(target)
        return result

"""Validate exact persisted snapshots, audit in order and publish atomically."""

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

from pydantic import TypeAdapter, ValidationError

from sng_consulter.identity.models import BatchId
from sng_consulter.storage import prepared_file
from ..inputs import build_technical_extraction_input
from ..models import TechnicalExtractionResult, TechnicalIdentityInput, TechnicalStructure
from ..validation import resolve_field_path, validate_technical_result
from .models import TechnicalAuditIssue, TechnicalAuditResult, TechnicalStructureAuditProposal


class InvalidTechnicalAuditInputError(ValueError):
    pass


class InvalidTechnicalAuditResponseError(RuntimeError):
    pass


class TechnicalAuditor(Protocol):
    def audit(self, identity: TechnicalIdentityInput, structure: TechnicalStructure) -> TechnicalStructureAuditProposal: ...


class TechnicalAuditService:
    def __init__(self, auditor: TechnicalAuditor, technical_root: Path = Path("runtime/technical"),
                 identity_root: Path = Path("runtime/identity"), discovery_root: Path = Path("runtime/discovery"),
                 batch_root: Path = Path("runtime/review/batches"), prepared_root: Path = Path("runtime/review/prepared")):
        self.auditor = auditor
        self.technical_root = Path(technical_root)
        self.evidence_roots = (Path(identity_root), Path(discovery_root), Path(batch_root), Path(prepared_root))

    def audit(self, batch_id: str) -> TechnicalAuditResult:
        try:
            batch_id = TypeAdapter(BatchId).validate_python(batch_id)
            directory = self.technical_root.resolve() / batch_id
            source = prepared_file(self.technical_root, directory, Path("technical_extraction.json"))
            raw = source.read_bytes()
            technical_hash = sha256(raw).hexdigest()
            extraction = TechnicalExtractionResult.model_validate_json(raw)
            evidence = build_technical_extraction_input(batch_id, *self.evidence_roots)
            validate_technical_result(extraction, evidence)
        except (ValueError, FileNotFoundError):
            raise InvalidTechnicalAuditInputError("Invalid, missing or stale Technical Extraction / canonical evidence") from None
        issues = []
        for structure, identity in zip(extraction.structures, evidence.identities):
            raw_proposal = self.auditor.audit(identity.model_copy(deep=True), structure.model_copy(deep=True))
            try:
                proposal = TechnicalStructureAuditProposal.model_validate(raw_proposal)
                if proposal.identity_id != identity.identity_id:
                    raise ValueError("Unexpected audit Identity")
                for issue in proposal.issues:
                    if not set(issue.candidate_ids) <= set(identity.candidate_ids):
                        raise ValueError("Audit candidates must belong to target Identity")
                    # Input corruption has already been rejected deterministically. Remaining
                    # issues need documentary evidence, rather than unsupported contract claims.
                    if not issue.candidate_ids:
                        raise ValueError("Semantic audit issues require candidate evidence")
                    for path in issue.field_paths:
                        resolve_field_path(structure, path)
                    issues.append(TechnicalAuditIssue(**issue.model_dump(), issue_id=f"technical-audit-{len(issues)+1:04d}",
                        technical_structure_id=structure.technical_structure_id, identity_id=identity.identity_id))
            except (ValidationError, ValueError):
                raise InvalidTechnicalAuditResponseError("Invalid technical audit targets, paths, evidence or proposal") from None
        result = TechnicalAuditResult(batch_id=batch_id, audited_technical_extraction_sha256=technical_hash,
            structures_audited=len(extraction.structures), issue_count=len(issues), issues=issues)
        target = directory / "technical_audit.json"
        if not target.resolve().is_relative_to(directory.resolve()):
            raise InvalidTechnicalAuditInputError("Audit output must stay within its batch")
        with TemporaryDirectory(prefix=".staging-", dir=directory) as staging:
            temporary = Path(staging) / "technical_audit.json"
            temporary.write_text(result.model_dump_json(indent=2), encoding="utf-8")
            temporary.replace(target)
        return result

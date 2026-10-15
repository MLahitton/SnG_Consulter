"""Offline, deterministic gate over persisted Identity and Audit snapshots."""

from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from sng_consulter.storage import prepared_file

from .audit.models import IdentityAuditResult
from .audit.service import validate_issue_references
from .models import BatchId, IdentityResolutionResult, Sha256
from .validation import validate_identity_partition


class IdentityGateStatus(StrEnum):
    READY_FOR_EXTRACTION = "READY_FOR_EXTRACTION"
    BLOCKED_BY_IDENTITY_ISSUES = "BLOCKED_BY_IDENTITY_ISSUES"


class IdentityGateResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, revalidate_instances="always")
    batch_id: BatchId
    source_identity_sha256: Sha256
    source_identity_audit_sha256: Sha256
    status: IdentityGateStatus
    issue_count: int = Field(ge=0)
    blocking_issue_ids: list[Annotated[str, Field(pattern=r"^identity-audit-[0-9]{4,}$")]]


class InvalidIdentityGateInputError(ValueError):
    pass


class IdentityGateService:
    def __init__(self, identity_root: Path = Path("runtime/identity")) -> None:
        self.identity_root = Path(identity_root)

    def gate(self, batch_id: str) -> IdentityGateResult:
        try:
            batch_id = TypeAdapter(BatchId).validate_python(batch_id)
        except ValidationError:
            raise InvalidIdentityGateInputError("Invalid batch ID") from None
        root = self.identity_root.resolve()
        directory = (root / batch_id).resolve()
        identity_path = prepared_file(root, directory, Path("identity.json"))
        audit_path = prepared_file(root, directory, Path("identity_audit.json"))
        identity_bytes = identity_path.read_bytes()
        identity_hash = sha256(identity_bytes).hexdigest()
        audit_bytes = audit_path.read_bytes()
        audit_hash = sha256(audit_bytes).hexdigest()
        try:
            partition = IdentityResolutionResult.model_validate_json(identity_bytes)
            audit = IdentityAuditResult.model_validate_json(audit_bytes)
        except ValidationError:
            raise InvalidIdentityGateInputError("Invalid persisted Identity or Identity Audit contract") from None
        if audit.batch_id != batch_id:
            raise InvalidIdentityGateInputError("Identity Audit batch identity mismatch")
        if audit.audited_identity_sha256 != identity_hash:
            raise InvalidIdentityGateInputError("Identity Audit is outdated; rerun audit before the gate")
        try:
            validate_identity_partition(partition, batch_id)
            identities = {g.identity_id: set(g.candidate_ids) for g in partition.identities}
            unresolved = {g.unresolved_id: set(g.candidate_ids) for g in partition.unresolved}
            for issue in audit.issues:
                validate_issue_references(issue, identities, unresolved)
        except ValueError:
            raise InvalidIdentityGateInputError("Invalid Identity partition or Audit issue references") from None
        issue_ids = [issue.issue_id for issue in audit.issues]
        if (audit.identities_audited != len(identities) or audit.unresolved_groups_audited != len(unresolved)
                or len(issue_ids) != len(set(issue_ids))):
            raise InvalidIdentityGateInputError("Inconsistent Audit counters or duplicate issue IDs")
        result = IdentityGateResult(batch_id=batch_id, source_identity_sha256=identity_hash,
            source_identity_audit_sha256=audit_hash,
            status=IdentityGateStatus.BLOCKED_BY_IDENTITY_ISSUES if issue_ids else IdentityGateStatus.READY_FOR_EXTRACTION,
            issue_count=len(issue_ids), blocking_issue_ids=issue_ids)
        target = directory / "identity_gate.json"
        if not target.resolve().is_relative_to(directory):
            raise InvalidIdentityGateInputError("Gate output must stay within its batch")
        with TemporaryDirectory(prefix=".staging-", dir=directory) as staging:
            temporary = Path(staging) / "identity_gate.json"
            temporary.write_text(result.model_dump_json(indent=2), encoding="utf-8")
            temporary.replace(target)
        return result

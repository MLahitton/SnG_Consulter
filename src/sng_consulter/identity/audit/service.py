"""Validate a persisted partition and independently audit it without rewriting it."""

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

from pydantic import TypeAdapter, ValidationError

from sng_consulter.storage import prepared_file

from ..inputs import build_identity_input
from ..models import BatchId, IdentityInput, IdentityResolutionResult
from ..validation import validate_identity_partition
from .models import IdentityAuditIssue, IdentityAuditIssueProposal, IdentityAuditProposal, IdentityAuditResult


class InvalidIdentityAuditInputError(ValueError):
    pass


class InvalidIdentityAuditProposalError(ValueError):
    pass


def validate_issue_references(issue: IdentityAuditIssueProposal, identities: dict[str, set[str]],
                              unresolved: dict[str, set[str]]) -> None:
    if not set(issue.identity_ids) <= identities.keys() or not set(issue.unresolved_ids) <= unresolved.keys():
        raise InvalidIdentityAuditProposalError("Audit issue references unknown group IDs")
    members = set().union(*(identities[i] for i in issue.identity_ids), *(unresolved[i] for i in issue.unresolved_ids))
    if not set(issue.candidate_ids) <= members:
        raise InvalidIdentityAuditProposalError("Issue candidates do not belong to its referenced groups")


class IdentityAuditor(Protocol):
    def audit(self, evidence: IdentityInput, partition: IdentityResolutionResult) -> IdentityAuditProposal: ...


class IdentityAuditService:
    def __init__(self, auditor: IdentityAuditor, identity_root: Path = Path("runtime/identity"),
                 discovery_root: Path = Path("runtime/discovery"),
                 batch_root: Path = Path("runtime/review/batches"),
                 prepared_root: Path = Path("runtime/review/prepared")) -> None:
        self.auditor = auditor
        self.identity_root = Path(identity_root)
        self.discovery_root = Path(discovery_root)
        self.batch_root = Path(batch_root)
        self.prepared_root = Path(prepared_root)

    def audit(self, batch_id: str) -> IdentityAuditResult:
        batch_id = TypeAdapter(BatchId).validate_python(batch_id)
        root = self.identity_root.resolve()
        directory = (root / batch_id).resolve()
        source = prepared_file(root, directory, Path("identity.json"))
        identity_bytes = source.read_bytes()
        identity_hash = sha256(identity_bytes).hexdigest()
        try:
            partition = IdentityResolutionResult.model_validate_json(identity_bytes)
        except ValidationError:
            raise InvalidIdentityAuditInputError("Invalid persisted Identity contract") from None
        evidence = build_identity_input(batch_id, self.discovery_root, self.batch_root, self.prepared_root)
        if partition.source_covered_discovery_sha256 != evidence.source_covered_discovery_sha256:
            raise InvalidIdentityAuditInputError("Identity is outdated relative to CoveredDiscovery; rerun Identity before audit")
        try:
            validate_identity_partition(partition, batch_id, {c.covered_candidate_id for c in evidence.candidates})
        except ValueError:
            raise InvalidIdentityAuditInputError("Identity has invalid assignments, counters, IDs or batch") from None
        identities = {g.identity_id: set(g.candidate_ids) for g in partition.identities}
        unresolved = {g.unresolved_id: set(g.candidate_ids) for g in partition.unresolved}
        proposal = IdentityAuditProposal.model_validate(self.auditor.audit(
            evidence.model_copy(deep=True), partition.model_copy(deep=True)))
        issues = []
        for issue in proposal.issues:
            validate_issue_references(issue, identities, unresolved)
            issues.append(IdentityAuditIssue(**issue.model_dump(), issue_id=f"identity-audit-{len(issues) + 1:04d}"))
        result = IdentityAuditResult(batch_id=batch_id, audited_identity_sha256=identity_hash,
            identities_audited=len(identities), unresolved_groups_audited=len(unresolved), issues=issues)
        target = directory / "identity_audit.json"
        if not target.resolve().is_relative_to(directory):
            raise InvalidIdentityAuditInputError("Audit output must stay within its batch")
        with TemporaryDirectory(prefix=".staging-", dir=directory) as staging:
            temporary = Path(staging) / "identity_audit.json"
            temporary.write_text(result.model_dump_json(indent=2), encoding="utf-8")
            temporary.replace(target)
        return result

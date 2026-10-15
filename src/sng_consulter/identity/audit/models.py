"""Evidence-supported concerns about a partition; never corrected identities."""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..models import BatchId, CandidateId, Sha256

IdentityId = Annotated[str, Field(pattern=r"^identity-[0-9]{4,}$")]
UnresolvedId = Annotated[str, Field(pattern=r"^unresolved-[0-9]{4,}$")]


class IdentityAuditIssueType(StrEnum):
    MERGE_ERROR = "MERGE_ERROR"
    SPLIT_ERROR = "SPLIT_ERROR"
    SHOULD_BE_UNRESOLVED = "SHOULD_BE_UNRESOLVED"
    UNRESOLVED_MAY_BE_RESOLVABLE = "UNRESOLVED_MAY_BE_RESOLVABLE"


class IdentityAuditIssueProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")
    issue_type: IdentityAuditIssueType
    identity_ids: list[IdentityId]
    unresolved_ids: list[UnresolvedId]
    candidate_ids: list[CandidateId]
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid_references(self):
        if not self.identity_ids and not self.unresolved_ids:
            raise ValueError("An issue must reference an identity or unresolved group")
        for ids in (self.identity_ids, self.unresolved_ids, self.candidate_ids):
            if len(ids) != len(set(ids)):
                raise ValueError("Issue references must not contain duplicate IDs")
        if self.issue_type == IdentityAuditIssueType.SPLIT_ERROR and len(self.identity_ids) < 2:
            raise ValueError("SPLIT_ERROR requires at least two identities")
        if self.issue_type in {IdentityAuditIssueType.MERGE_ERROR, IdentityAuditIssueType.SHOULD_BE_UNRESOLVED} and not self.identity_ids:
            raise ValueError("This issue type requires a resolved identity")
        if self.issue_type == IdentityAuditIssueType.UNRESOLVED_MAY_BE_RESOLVABLE and not self.unresolved_ids:
            raise ValueError("This issue type requires an unresolved group")
        if self.issue_type == IdentityAuditIssueType.UNRESOLVED_MAY_BE_RESOLVABLE:
            if self.identity_ids:
                raise ValueError("UNRESOLVED_MAY_BE_RESOLVABLE must not reference resolved identities")
        elif self.unresolved_ids:
            raise ValueError("Resolved-identity issues must not reference unresolved groups")
        return self


class IdentityAuditProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")
    issues: list[IdentityAuditIssueProposal]


class IdentityAuditIssue(IdentityAuditIssueProposal):
    issue_id: str = Field(pattern=r"^identity-audit-[0-9]{4,}$")


class IdentityAuditResult(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")
    batch_id: BatchId
    audited_identity_sha256: Sha256
    identities_audited: int = Field(ge=0)
    unresolved_groups_audited: int = Field(ge=0)
    issues: list[IdentityAuditIssue]

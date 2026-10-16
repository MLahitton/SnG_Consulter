"""Independent concerns about existing structures, never replacement values."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sng_consulter.identity.models import BatchId, CandidateId, Sha256
from ..models import IdentityId


class TechnicalAuditIssueType(StrEnum):
    UNSUPPORTED_VALUE = "UNSUPPORTED_VALUE"
    MISSING_SUPPORTED_VALUE = "MISSING_SUPPORTED_VALUE"
    RESOLUTION_ERROR = "RESOLUTION_ERROR"
    SCOPE_ERROR = "SCOPE_ERROR"
    QUANTITY_ERROR = "QUANTITY_ERROR"
    DIMENSION_ERROR = "DIMENSION_ERROR"
    MODULE_COMPOSITION_ERROR = "MODULE_COMPOSITION_ERROR"
    MODULE_FUNCTION_ERROR = "MODULE_FUNCTION_ERROR"
    PANE_COMPOSITION_ERROR = "PANE_COMPOSITION_ERROR"
    GLASS_SPECIFICATION_ERROR = "GLASS_SPECIFICATION_ERROR"
    ATTRIBUTE_OWNERSHIP_ERROR = "ATTRIBUTE_OWNERSHIP_ERROR"


class _AuditModel(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")


class TechnicalAuditIssueProposal(_AuditModel):
    issue_type: TechnicalAuditIssueType
    field_paths: list[str] = Field(min_length=1)
    candidate_ids: list[CandidateId]
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid_issue(self):
        if not self.reason.strip() or any(not p.strip() for p in self.field_paths):
            raise ValueError("Issue paths and reason must not be blank")
        if len(set(self.candidate_ids)) != len(self.candidate_ids):
            raise ValueError("Duplicate issue candidate IDs")
        return self


class TechnicalStructureAuditProposal(_AuditModel):
    identity_id: IdentityId
    issues: list[TechnicalAuditIssueProposal]


class TechnicalAuditIssue(TechnicalAuditIssueProposal):
    issue_id: str = Field(pattern=r"^technical-audit-[0-9]{4,}$")
    technical_structure_id: str = Field(pattern=r"^technical-structure-[0-9]{4,}$")
    identity_id: IdentityId


class TechnicalAuditResult(_AuditModel):
    batch_id: BatchId
    audited_technical_extraction_sha256: Sha256
    structures_audited: int = Field(ge=0)
    issue_count: int = Field(ge=0)
    issues: list[TechnicalAuditIssue]

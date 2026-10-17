"""Issue-targeted inputs and future full-structure proposals, never applied here."""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sng_consulter.identity.models import BatchId, CandidateId, Sha256, UnresolvedIdentity
from ..audit.models import TechnicalAuditIssueProposal
from ..models import IdentityId, TechnicalIdentityInput, TechnicalStructure
from ..validation import resolve_field_path
from .validation import normalize_allowed_change_paths

IssueId = Annotated[str, Field(pattern=r"^technical-audit-[0-9]{4,}$")]
StructureId = Annotated[str, Field(pattern=r"^technical-structure-[0-9]{4,}$")]


class _ResolutionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")


class TechnicalResolutionIssue(TechnicalAuditIssueProposal):
    issue_id: IssueId


class TechnicalResolutionTarget(_ResolutionModel):
    identity_id: IdentityId
    technical_structure_id: StructureId
    current_structure: TechnicalStructure
    issues: list[TechnicalResolutionIssue] = Field(min_length=1)
    allowed_change_paths: list[str] = Field(min_length=1)
    identity: TechnicalIdentityInput

    @model_validator(mode="after")
    def consistent_target(self):
        if (self.identity_id != self.identity.identity_id or self.current_structure.identity_id != self.identity_id
                or self.current_structure.technical_structure_id != self.technical_structure_id):
            raise ValueError("Resolution target must match its current structure and Identity")
        ids = [i.issue_id for i in self.issues]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate target issues")
        paths = []
        for issue in self.issues:
            if not issue.candidate_ids or not set(issue.candidate_ids) <= set(self.identity.candidate_ids):
                raise ValueError("Resolution issue evidence must belong to target Identity")
            for path in issue.field_paths:
                resolve_field_path(self.current_structure, path)
                paths.append(path)
        if self.allowed_change_paths != normalize_allowed_change_paths(paths):
            raise ValueError("Allowed paths must derive exclusively from the target issues")
        return self


class TechnicalResolutionInput(_ResolutionModel):
    batch_id: BatchId
    source_technical_extraction_sha256: Sha256
    source_technical_audit_sha256: Sha256
    source_technical_gate_sha256: Sha256
    resolution_required: bool = Field(strict=True)
    targets: list[TechnicalResolutionTarget]
    resolved_identity_count: int = Field(ge=0)
    unresolved_groups: list[UnresolvedIdentity]

    @model_validator(mode="after")
    def consistent_work(self):
        if self.resolution_required != bool(self.targets) or len(self.targets) > self.resolved_identity_count:
            raise ValueError("Resolution work must match its targets and identity count")
        for ids in ([t.identity_id for t in self.targets], [t.technical_structure_id for t in self.targets],
                    [i.issue_id for t in self.targets for i in t.issues]):
            if len(ids) != len(set(ids)):
                raise ValueError("Duplicate resolution targets or issues")
        return self


class TechnicalIssueResolutionOutcome(StrEnum):
    CORRECTED = "CORRECTED"
    DISPUTED = "DISPUTED"
    UNRESOLVED = "UNRESOLVED"


class TechnicalIssueResolutionProposal(_ResolutionModel):
    issue_id: IssueId
    outcome: TechnicalIssueResolutionOutcome
    evidence_candidate_ids: list[CandidateId]
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid_explanation(self):
        if not self.reason.strip() or len(set(self.evidence_candidate_ids)) != len(self.evidence_candidate_ids):
            raise ValueError("Resolution reason must not be blank and evidence must be unique")
        return self


class TechnicalStructureResolutionProposal(_ResolutionModel):
    identity_id: IdentityId
    technical_structure_id: StructureId
    issue_resolutions: list[TechnicalIssueResolutionProposal]
    proposed_structure: TechnicalStructure

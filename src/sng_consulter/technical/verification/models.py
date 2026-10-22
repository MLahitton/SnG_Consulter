"""Independent observations about current applied structures, never corrections."""

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sng_consulter.identity.models import BatchId, CandidateId, Sha256, UnresolvedIdentity
from ..audit.models import TechnicalAuditIssueProposal
from ..models import IdentityId, TechnicalIdentityInput, TechnicalStructure
from ..resolution.models import StructureId


class _VerificationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")


class TechnicalVerificationTarget(_VerificationModel):
    identity_id: IdentityId
    technical_structure_id: StructureId
    current_structure: TechnicalStructure
    identity: TechnicalIdentityInput

    @model_validator(mode="after")
    def consistent_target(self):
        if (self.identity_id != self.identity.identity_id or self.identity_id != self.current_structure.identity_id
                or self.technical_structure_id != self.current_structure.technical_structure_id):
            raise ValueError("Verification target must match its current structure and Identity")
        return self


class TechnicalVerificationInput(_VerificationModel):
    batch_id: BatchId
    source_technical_resolution_sha256: Sha256
    targets: list[TechnicalVerificationTarget]
    resolved_identity_count: int = Field(ge=0)
    unresolved_groups: list[UnresolvedIdentity]

    @model_validator(mode="after")
    def consistent_targets(self):
        if self.resolved_identity_count != len(self.targets):
            raise ValueError("Verification must cover every resolved Identity")
        for ids in ([t.identity_id for t in self.targets], [t.technical_structure_id for t in self.targets]):
            if len(ids) != len(set(ids)):
                raise ValueError("Duplicate Verification targets")
        return self


class TechnicalVerificationIssueProposal(TechnicalAuditIssueProposal):
    candidate_ids: list[CandidateId] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_paths(self):
        if len(self.field_paths) != len(set(self.field_paths)):
            raise ValueError("Duplicate Verification field paths")
        return self


class TechnicalStructureVerificationProposal(_VerificationModel):
    identity_id: IdentityId
    technical_structure_id: StructureId
    issues: list[TechnicalVerificationIssueProposal]


class TechnicalVerificationIssue(TechnicalVerificationIssueProposal):
    issue_id: str = Field(pattern=r"^technical-verification-[0-9]{4,}$")
    technical_structure_id: StructureId
    identity_id: IdentityId


class TechnicalVerificationResult(_VerificationModel):
    batch_id: BatchId
    verified_technical_resolution_sha256: Sha256
    structures_verified: int = Field(ge=0)
    issue_count: int = Field(ge=0)
    issues: list[TechnicalVerificationIssue]

    @model_validator(mode="after")
    def consistent_result(self):
        if self.issue_count != len(self.issues):
            raise ValueError("Verification issue count mismatch")
        if [i.issue_id for i in self.issues] != [f"technical-verification-{n:04d}" for n in range(1, len(self.issues) + 1)]:
            raise ValueError("Verification issue IDs must follow their exact sequence")
        if len({i.technical_structure_id for i in self.issues}) > self.structures_verified:
            raise ValueError("Verification issues exceed verified structures")
        return self

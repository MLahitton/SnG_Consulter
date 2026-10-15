"""Provisional observation grouping, without technical extraction or quantity."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sng_consulter.discovery.models import BatchId, DocumentId, GlassRelevance, NormalizedRegion

CandidateId = Annotated[str, Field(pattern=r"^covered-[0-9]{4,}$")]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
CandidateIds = Annotated[list[CandidateId], Field(min_length=1)]


class _IdentityModel(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")


class IdentityCandidateInput(_IdentityModel):
    covered_candidate_id: CandidateId
    origin: Literal["DISCOVERY", "COVERAGE_AUDIT"]
    source_candidate_id: str = Field(min_length=1)
    reference_hint: str | None
    description: str = Field(min_length=1)
    glass_relevance: GlassRelevance
    document_id: DocumentId
    occurrence: int = Field(ge=0)
    page_number: int = Field(ge=1)
    region: NormalizedRegion
    width_px: int = Field(gt=0)
    height_px: int = Field(gt=0)
    image_path: Path


class IdentityInput(_IdentityModel):
    batch_id: BatchId
    source_covered_discovery_sha256: Sha256
    candidates: list[IdentityCandidateInput]

    @model_validator(mode="after")
    def unique_candidates(self):
        ids = [candidate.covered_candidate_id for candidate in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate covered candidate IDs")
        return self


class IdentityGroupProposal(_IdentityModel):
    candidate_ids: CandidateIds
    reference_hint: str | None
    description: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class UnresolvedIdentityProposal(_IdentityModel):
    candidate_ids: CandidateIds
    reason: str = Field(min_length=1)


class IdentityResolutionProposal(_IdentityModel):
    groups: list[IdentityGroupProposal]
    unresolved: list[UnresolvedIdentityProposal]

    @model_validator(mode="after")
    def exclusive_assignments(self):
        ids = [candidate_id for group in [*self.groups, *self.unresolved] for candidate_id in group.candidate_ids]
        if len(ids) != len(set(ids)):
            raise ValueError("Each candidate must be assigned exactly once")
        return self


class IdentityGroup(IdentityGroupProposal):
    identity_id: str = Field(pattern=r"^identity-[0-9]{4,}$")


class UnresolvedIdentity(UnresolvedIdentityProposal):
    unresolved_id: str = Field(pattern=r"^unresolved-[0-9]{4,}$")


class IdentityResolutionResult(_IdentityModel):
    batch_id: BatchId
    source_covered_discovery_sha256: Sha256
    identities: list[IdentityGroup]
    unresolved: list[UnresolvedIdentity]
    covered_candidate_count: int = Field(ge=0)
    resolved_candidate_count: int = Field(ge=0)
    unresolved_candidate_count: int = Field(ge=0)

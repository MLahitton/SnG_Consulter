"""Possible omissions remain distinct from Discovery candidates and final identity."""

from pydantic import BaseModel, ConfigDict, Field

from ..models import BatchId, DocumentId, GlassRelevance, NormalizedRegion


class CoverageAuditProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")
    reference_hint: str | None = None
    description: str = Field(min_length=1)
    region: NormalizedRegion
    glass_relevance: GlassRelevance
    reason: str = Field(min_length=1)


class CoverageAuditPageResult(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")
    document_id: DocumentId
    occurrence: int = Field(ge=0)
    page_number: int = Field(ge=1)
    missing_candidates: list[CoverageAuditProposal]


class CoverageMissingCandidate(CoverageAuditProposal):
    missing_candidate_id: str = Field(pattern=r"^coverage-[0-9]{4,}$")
    document_id: DocumentId
    occurrence: int = Field(ge=0)
    page_number: int = Field(ge=1)


class CoverageAuditResult(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")
    batch_id: BatchId
    pages_audited: int = Field(ge=0)
    missing_candidates: list[CoverageMissingCandidate]

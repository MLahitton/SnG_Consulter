"""Discovery observations, not canonical structures or technical extraction."""

from enum import StrEnum
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sng_consulter.preparation.models import TextSpan

UnitCoordinate = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
BatchId = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
DocumentId = Annotated[str, Field(pattern=r"^[0-9a-f]{16}$")]


class _DiscoveryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")


class NormalizedRegion(_DiscoveryModel):
    """Nonempty rectangle in prepared visual space, with top-left origin."""

    x0: UnitCoordinate
    y0: UnitCoordinate
    x1: UnitCoordinate
    y1: UnitCoordinate

    @model_validator(mode="after")
    def validate_endpoints(self):
        if self.x0 >= self.x1 or self.y0 >= self.y1:
            raise ValueError("Region must satisfy x0 < x1 and y0 < y1")
        return self


class PageDiscoveryInput(_DiscoveryModel):
    document_id: DocumentId
    occurrence: int = Field(ge=0)
    page_number: int = Field(ge=1)
    width_px: int = Field(gt=0)
    height_px: int = Field(gt=0)
    image_path: Path
    # Deduplicated native text; bbox is in prepared visual render pixels.
    native_text_spans: list[TextSpan]


class DiscoveryInput(_DiscoveryModel):
    batch_id: BatchId
    pages: list[PageDiscoveryInput]


class GlassRelevance(StrEnum):
    LIKELY = "LIKELY"
    POSSIBLE = "POSSIBLE"


class EvidenceKind(StrEnum):
    VISUAL = "VISUAL"
    NATIVE_TEXT = "NATIVE_TEXT"
    COMBINED = "COMBINED"


class CandidateProposal(_DiscoveryModel):
    reference_hint: str | None = None
    description: str = Field(min_length=1)
    region: NormalizedRegion
    glass_relevance: GlassRelevance
    evidence_kind: EvidenceKind


class DiscoveryEvidence(_DiscoveryModel):
    document_id: DocumentId
    occurrence: int = Field(ge=0)
    page_number: int = Field(ge=1)
    region: NormalizedRegion
    evidence_kind: EvidenceKind


class DiscoveryCandidate(_DiscoveryModel):
    candidate_id: str = Field(pattern=r"^candidate-[0-9]{4,}$")
    reference_hint: str | None = None
    description: str = Field(min_length=1)
    glass_relevance: GlassRelevance
    evidence: DiscoveryEvidence


class DiscoveryResult(_DiscoveryModel):
    batch_id: BatchId
    candidates: list[DiscoveryCandidate]
    pages_analyzed: int = Field(ge=0)

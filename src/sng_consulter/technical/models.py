"""Internal technical proposals and results; no extraction heuristics or defaults."""

from enum import StrEnum
from typing import Annotated, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sng_consulter.discovery.models import PageDiscoveryInput
from sng_consulter.identity.models import BatchId, CandidateId, IdentityCandidateInput, Sha256, UnresolvedIdentity

IdentityId = Annotated[str, Field(pattern=r"^identity-[0-9]{4,}$")]
T = TypeVar("T")


class _TechnicalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")


class FieldResolution(StrEnum):
    EXPLICIT = "EXPLICIT"
    INFERRED = "INFERRED"
    DEFAULTED = "DEFAULTED"
    UNKNOWN = "UNKNOWN"
    CONFLICT = "CONFLICT"


class ResolvedField(_TechnicalModel, Generic[T]):
    value: T | None
    resolution: FieldResolution
    evidence_candidate_ids: list[CandidateId]
    note: str | None = None

    @model_validator(mode="after")
    def resolution_semantics(self):
        if len(self.evidence_candidate_ids) != len(set(self.evidence_candidate_ids)):
            raise ValueError("Duplicate evidence candidate IDs")
        if self.resolution in {FieldResolution.UNKNOWN, FieldResolution.CONFLICT}:
            if self.value is not None:
                raise ValueError("UNKNOWN and CONFLICT require null value")
        elif self.value is None:
            raise ValueError("Resolved values must not be null")
        if self.resolution in {FieldResolution.EXPLICIT, FieldResolution.INFERRED, FieldResolution.CONFLICT}:
            if not self.evidence_candidate_ids:
                raise ValueError("This resolution requires documentary evidence")
        elif self.evidence_candidate_ids:
            raise ValueError("UNKNOWN and DEFAULTED must not cite evidence")
        if self.resolution in {FieldResolution.INFERRED, FieldResolution.DEFAULTED, FieldResolution.CONFLICT}:
            if self.note is None or not self.note.strip():
                raise ValueError("This resolution requires an explanatory note")
        return self


class ResolvedText(ResolvedField[Annotated[str, Field(strict=True, min_length=1)]]):
    pass


class ResolvedPositiveInt(ResolvedField[Annotated[int, Field(strict=True, gt=0)]]):
    pass


class ResolvedNonNegativeInt(ResolvedField[Annotated[int, Field(strict=True, ge=0)]]):
    pass


class ResolvedPositiveNumber(ResolvedField[Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]]):
    pass


class ModuleFunction(StrEnum):
    FIXED = "FIXED"
    SLIDING = "SLIDING"
    PROJECTING = "PROJECTING"
    HINGED = "HINGED"
    FOLDING = "FOLDING"
    PIVOT = "PIVOT"
    LOUVER = "LOUVER"
    OTHER = "OTHER"


class ResolvedModuleFunction(ResolvedField[ModuleFunction]):
    pass


class TechnicalScope(StrEnum):
    GLASS_RELATED = "GLASS_RELATED"
    NOT_GLASS_RELATED = "NOT_GLASS_RELATED"


class ResolvedTechnicalScope(ResolvedField[TechnicalScope]):
    pass


class TechnicalDimensions(_TechnicalModel):
    width_mm: ResolvedPositiveNumber
    height_mm: ResolvedPositiveNumber


class GlassPaneProposal(_TechnicalModel):
    position_hint: ResolvedText
    dimensions: TechnicalDimensions
    glass_specification: ResolvedText


class DecorativeElementProposal(_TechnicalModel):
    kind: ResolvedText
    description: ResolvedText
    position_hint: ResolvedText


class ModuleProposal(_TechnicalModel):
    position_hint: ResolvedText
    dimensions: TechnicalDimensions
    function: ResolvedModuleFunction
    opening_direction: ResolvedText
    glass_pane_count: ResolvedNonNegativeInt
    glass_panes: list[GlassPaneProposal]
    decorative_elements: list[DecorativeElementProposal]

    @model_validator(mode="after")
    def consistent_pane_count(self):
        if self.glass_pane_count.value is not None and self.glass_pane_count.value != len(self.glass_panes):
            raise ValueError("Known glass_pane_count must equal the number of proposed physical panes")
        return self


class TechnicalStructureProposal(_TechnicalModel):
    identity_id: IdentityId
    scope: ResolvedTechnicalScope
    reference: ResolvedText
    name: ResolvedText
    category: ResolvedText
    location: ResolvedText
    quantity: ResolvedPositiveInt
    dimensions: TechnicalDimensions
    module_count: ResolvedNonNegativeInt
    modules: list[ModuleProposal]
    notes: list[str]

    @model_validator(mode="after")
    def consistent_module_count(self):
        if self.module_count.value is not None and self.module_count.value != len(self.modules):
            raise ValueError("Known module_count must equal the number of proposed functional modules")
        return self


class TechnicalExtractionProposal(_TechnicalModel):
    structures: list[TechnicalStructureProposal]


class GlassPane(GlassPaneProposal):
    glass_pane_id: str = Field(pattern=r"^glass-pane-[0-9]{4,}$")


class DecorativeElement(DecorativeElementProposal):
    decorative_element_id: str = Field(pattern=r"^decorative-[0-9]{4,}$")


class Module(ModuleProposal):
    module_id: str = Field(pattern=r"^module-[0-9]{4,}$")
    glass_panes: list[GlassPane]
    decorative_elements: list[DecorativeElement]


class TechnicalResolutionStatus(StrEnum):
    RESOLVED = "RESOLVED"
    PARTIAL = "PARTIAL"
    AMBIGUOUS = "AMBIGUOUS"


class TechnicalStructure(TechnicalStructureProposal):
    technical_structure_id: str = Field(pattern=r"^technical-structure-[0-9]{4,}$")
    modules: list[Module]
    resolution_status: TechnicalResolutionStatus
    missing_fields: list[str]


class TechnicalPageEvidence(PageDiscoveryInput):
    """Existing deduplicated native spans in visual render pixels; RAW remains intact."""


class TechnicalIdentityInput(_TechnicalModel):
    identity_id: IdentityId
    reference_hint: str | None
    description: str
    candidate_ids: list[CandidateId] = Field(min_length=1)
    candidates: list[IdentityCandidateInput]
    pages: list[TechnicalPageEvidence]

    @model_validator(mode="after")
    def complete_evidence(self):
        ids = [c.covered_candidate_id for c in self.candidates]
        if len(set(self.candidate_ids)) != len(self.candidate_ids) or ids != self.candidate_ids:
            raise ValueError("Identity candidates must match its ordered candidate IDs")
        page_keys = [(p.document_id, p.occurrence, p.page_number) for p in self.pages]
        if len(page_keys) != len(set(page_keys)):
            raise ValueError("Duplicate page evidence")
        required = {(c.document_id, c.occurrence, c.page_number) for c in self.candidates}
        if set(page_keys) != required:
            raise ValueError("Identity pages must cover its candidate pages exactly")
        pages = dict(zip(page_keys, self.pages))
        for c in self.candidates:
            p = pages[(c.document_id, c.occurrence, c.page_number)]
            if (c.width_px,c.height_px,c.image_path) != (p.width_px,p.height_px,p.image_path):
                raise ValueError("Candidate render does not match full-page evidence")
        return self


class TechnicalExtractionInput(_TechnicalModel):
    batch_id: BatchId
    source_identity_sha256: Sha256
    source_identity_gate_sha256: Sha256
    identities: list[TechnicalIdentityInput]
    unresolved_groups: list[UnresolvedIdentity]

    @model_validator(mode="after")
    def exclusive_targets(self):
        ids = [g.identity_id for g in self.identities]
        unresolved_ids = [g.unresolved_id for g in self.unresolved_groups]
        members = [cid for g in [*self.identities,*self.unresolved_groups] for cid in g.candidate_ids]
        if len(ids) != len(set(ids)) or len(unresolved_ids) != len(set(unresolved_ids)) or len(members) != len(set(members)):
            raise ValueError("Technical identities, unresolved groups and candidate memberships must be unique")
        return self


class TechnicalExtractionResult(_TechnicalModel):
    batch_id: BatchId
    source_identity_sha256: Sha256
    source_identity_gate_sha256: Sha256
    structures: list[TechnicalStructure]
    unresolved_groups: list[UnresolvedIdentity]
    resolved_identity_count: int = Field(ge=0)
    unresolved_group_count: int = Field(ge=0)

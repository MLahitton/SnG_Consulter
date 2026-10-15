"""Deterministic concatenation of persisted observations, without identity resolution."""

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from sng_consulter.storage import prepared_file

from .coverage.models import CoverageAuditResult
from .models import BatchId, DiscoveryEvidence, DiscoveryResult, DocumentId, GlassRelevance, NormalizedRegion


class CoverageEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")
    document_id: DocumentId
    occurrence: int = Field(ge=0)
    page_number: int = Field(ge=1)
    region: NormalizedRegion
    reason: str = Field(min_length=1)


class CoveredCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")
    covered_candidate_id: str = Field(pattern=r"^covered-[0-9]{4,}$")
    origin: Literal["DISCOVERY", "COVERAGE_AUDIT"]
    source_candidate_id: str
    reference_hint: str | None
    description: str = Field(min_length=1)
    glass_relevance: GlassRelevance
    evidence: DiscoveryEvidence | CoverageEvidence


class CoveredDiscoveryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")
    batch_id: BatchId
    candidates: list[CoveredCandidate]
    discovery_count: int = Field(ge=0)
    coverage_added_count: int = Field(ge=0)
    total_candidates: int = Field(ge=0)


class InvalidConsolidationInputError(ValueError):
    pass


class CoveredDiscoveryService:
    def __init__(self, output_root: Path = Path("runtime/discovery")) -> None:
        self.output_root = Path(output_root)

    def consolidate(self, batch_id: str) -> CoveredDiscoveryResult:
        try:
            batch_id = TypeAdapter(BatchId).validate_python(batch_id)
        except ValidationError:
            raise InvalidConsolidationInputError("Invalid batch ID") from None
        root = self.output_root.resolve()
        directory = (root / batch_id).resolve()
        discovery_path = prepared_file(root, directory, Path("discovery.json"))
        coverage_path = prepared_file(root, directory, Path("coverage_audit.json"))
        discovery_bytes = discovery_path.read_bytes()
        current_discovery_sha256 = sha256(discovery_bytes).hexdigest()
        try:
            discovery = DiscoveryResult.model_validate_json(discovery_bytes)
            coverage = CoverageAuditResult.model_validate_json(coverage_path.read_text(encoding="utf-8"))
        except (ValidationError, UnicodeDecodeError):
            raise InvalidConsolidationInputError("Invalid Discovery or Coverage Audit JSON contract") from None
        if coverage.audited_discovery_sha256 != current_discovery_sha256:
            raise InvalidConsolidationInputError("Coverage Audit is outdated; rerun Coverage Audit before consolidation")
        if discovery.batch_id != batch_id or coverage.batch_id != batch_id:
            raise InvalidConsolidationInputError("Input batch identity mismatch")
        if discovery.pages_analyzed != coverage.pages_audited:
            raise InvalidConsolidationInputError("Input page counts disagree")
        candidates = []
        for candidate in discovery.candidates:
            candidates.append(CoveredCandidate(
                covered_candidate_id=f"covered-{len(candidates) + 1:04d}", origin="DISCOVERY",
                source_candidate_id=candidate.candidate_id, reference_hint=candidate.reference_hint,
                description=candidate.description, glass_relevance=candidate.glass_relevance,
                evidence=candidate.evidence,
            ))
        for candidate in coverage.missing_candidates:
            candidates.append(CoveredCandidate(
                covered_candidate_id=f"covered-{len(candidates) + 1:04d}", origin="COVERAGE_AUDIT",
                source_candidate_id=candidate.missing_candidate_id, reference_hint=candidate.reference_hint,
                description=candidate.description, glass_relevance=candidate.glass_relevance,
                evidence=CoverageEvidence(document_id=candidate.document_id, occurrence=candidate.occurrence,
                    page_number=candidate.page_number, region=candidate.region, reason=candidate.reason),
            ))
        result = CoveredDiscoveryResult(batch_id=batch_id, candidates=candidates,
            discovery_count=len(discovery.candidates), coverage_added_count=len(coverage.missing_candidates),
            total_candidates=len(candidates))
        target = directory / "covered_discovery.json"
        if not target.resolve().is_relative_to(directory):
            raise InvalidConsolidationInputError("Consolidated output must stay within its batch")
        with TemporaryDirectory(prefix=".staging-", dir=directory) as staging:
            temporary = Path(staging) / "covered_discovery.json"
            temporary.write_text(result.model_dump_json(indent=2), encoding="utf-8")
            temporary.replace(target)
        return result

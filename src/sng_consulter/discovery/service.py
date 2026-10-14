"""Per-page Discovery orchestration and separate, atomic result publication."""

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

from .models import (
    CandidateProposal, DiscoveryCandidate, DiscoveryEvidence, DiscoveryInput,
    DiscoveryResult, PageDiscoveryInput,
)


class PageAnalyzer(Protocol):
    def discover(self, page: PageDiscoveryInput) -> list[CandidateProposal]: ...


class DiscoveryService:
    def __init__(self, analyzer: PageAnalyzer, output_root: Path = Path("runtime/discovery")) -> None:
        self.analyzer = analyzer
        self.output_root = Path(output_root)

    def discover(self, discovery_input: DiscoveryInput) -> DiscoveryResult:
        discovery_input = DiscoveryInput.model_validate(discovery_input)
        candidates = []
        for page in discovery_input.pages:
            for proposal in self.analyzer.discover(page):
                proposal = CandidateProposal.model_validate(proposal)
                candidates.append(DiscoveryCandidate(
                    candidate_id=f"candidate-{len(candidates) + 1:04d}",
                    reference_hint=proposal.reference_hint, description=proposal.description,
                    glass_relevance=proposal.glass_relevance,
                    evidence=DiscoveryEvidence(document_id=page.document_id, occurrence=page.occurrence,
                        page_number=page.page_number, region=proposal.region, evidence_kind=proposal.evidence_kind),
                ))
        result = DiscoveryResult(batch_id=discovery_input.batch_id, candidates=candidates,
                                 pages_analyzed=len(discovery_input.pages))
        root = self.output_root.resolve()
        directory = (root / result.batch_id).resolve()
        if not directory.is_relative_to(root):
            raise ValueError("Discovery output must stay within its root")
        directory.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".staging-", dir=directory) as staging:
            temporary = Path(staging) / "discovery.json"
            temporary.write_text(result.model_dump_json(indent=2), encoding="utf-8")
            temporary.replace(directory / "discovery.json")
        return result

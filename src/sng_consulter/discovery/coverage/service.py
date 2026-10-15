"""Audit each prepared page against its saved Discovery, then publish atomically."""

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

from pydantic import ValidationError

from sng_consulter.storage import prepared_file

from ..models import DiscoveryCandidate, DiscoveryInput, DiscoveryResult, PageDiscoveryInput
from .models import CoverageAuditPageResult, CoverageAuditProposal, CoverageAuditResult, CoverageMissingCandidate


class InvalidCoverageInputError(ValueError):
    pass


class PageCoverageAuditor(Protocol):
    def audit(self, page: PageDiscoveryInput,
              candidates: list[DiscoveryCandidate]) -> list[CoverageAuditProposal]: ...


class CoverageAuditService:
    def __init__(self, auditor: PageCoverageAuditor, output_root: Path = Path("runtime/discovery")) -> None:
        self.auditor = auditor
        self.output_root = Path(output_root)

    def audit(self, discovery_input: DiscoveryInput) -> CoverageAuditResult:
        evidence = DiscoveryInput.model_validate(discovery_input)
        root = self.output_root.resolve()
        directory = (root / evidence.batch_id).resolve()
        source = prepared_file(root, directory, Path("discovery.json"))
        discovery_bytes = source.read_bytes()
        audited_discovery_sha256 = sha256(discovery_bytes).hexdigest()
        try:
            discovery = DiscoveryResult.model_validate_json(discovery_bytes)
        except (ValidationError, UnicodeDecodeError):
            raise InvalidCoverageInputError("Invalid persisted Discovery contract") from None
        keys = [(p.document_id, p.occurrence, p.page_number) for p in evidence.pages]
        if (discovery.batch_id != evidence.batch_id or discovery.pages_analyzed != len(keys)
                or len(set(keys)) != len(keys)):
            raise InvalidCoverageInputError("Discovery batch/page coverage does not match prepared input")
        grouped = {key: [] for key in keys}
        ids = set()
        for candidate in discovery.candidates:
            key = (candidate.evidence.document_id, candidate.evidence.occurrence, candidate.evidence.page_number)
            if key not in grouped or candidate.candidate_id in ids:
                raise InvalidCoverageInputError("Invalid Discovery candidate identity or page reference")
            ids.add(candidate.candidate_id)
            grouped[key].append(candidate)
        missing = []
        for page in evidence.pages:
            key = (page.document_id, page.occurrence, page.page_number)
            page_result = CoverageAuditPageResult(
                document_id=page.document_id, occurrence=page.occurrence, page_number=page.page_number,
                missing_candidates=self.auditor.audit(page, grouped[key]),
            )
            for proposal in page_result.missing_candidates:
                missing.append(CoverageMissingCandidate(
                    **proposal.model_dump(), missing_candidate_id=f"coverage-{len(missing) + 1:04d}",
                    document_id=page.document_id, occurrence=page.occurrence, page_number=page.page_number,
                ))
        result = CoverageAuditResult(batch_id=evidence.batch_id,
            audited_discovery_sha256=audited_discovery_sha256, pages_audited=len(keys), missing_candidates=missing)
        # No output is published until every page and proposal has validated.
        target = directory / "coverage_audit.json"
        if not target.resolve().is_relative_to(directory):
            raise InvalidCoverageInputError("Coverage output must stay within its batch")
        with TemporaryDirectory(prefix=".staging-", dir=directory) as staging:
            temporary = Path(staging) / "coverage_audit.json"
            temporary.write_text(result.model_dump_json(indent=2), encoding="utf-8")
            temporary.replace(target)
        return result

"""Read the canonical CoveredDiscovery artifact and locate its prepared evidence."""

from hashlib import sha256
from pathlib import Path

from pydantic import ValidationError

from sng_consulter.discovery.consolidation import CoveredDiscoveryResult
from sng_consulter.storage import prepared_file, read_prepared_document, read_review_batch

from .models import IdentityCandidateInput, IdentityInput


class InvalidIdentityInputError(ValueError):
    pass


def build_identity_input(
    batch_id: str, discovery_root: Path = Path("runtime/discovery"),
    batch_root: Path = Path("runtime/review/batches"),
    prepared_root: Path = Path("runtime/review/prepared"),
) -> IdentityInput:
    batch = read_review_batch(batch_root, batch_id)
    source = prepared_file(discovery_root, discovery_root / batch_id, Path("covered_discovery.json"))
    source_bytes = source.read_bytes()
    source_hash = sha256(source_bytes).hexdigest()
    try:
        covered = CoveredDiscoveryResult.model_validate_json(source_bytes)
    except ValidationError:
        raise InvalidIdentityInputError("Invalid CoveredDiscovery contract") from None
    if covered.batch_id != batch_id:
        raise InvalidIdentityInputError("CoveredDiscovery batch identity mismatch")
    discovery_count = sum(c.origin == "DISCOVERY" for c in covered.candidates)
    if (covered.total_candidates != len(covered.candidates) or covered.discovery_count != discovery_count
            or covered.coverage_added_count != len(covered.candidates) - discovery_count):
        raise InvalidIdentityInputError("Inconsistent CoveredDiscovery counts")
    candidates = []
    documents = {}
    for candidate in covered.candidates:
        evidence = candidate.evidence
        if not 0 <= evidence.occurrence < len(batch.documents):
            raise InvalidIdentityInputError("Unknown candidate occurrence")
        entry = batch.documents[evidence.occurrence]
        if entry.status != "prepared" or entry.document_id != evidence.document_id:
            raise InvalidIdentityInputError("Candidate document does not match its occurrence")
        if evidence.document_id not in documents:
            documents[evidence.document_id] = read_prepared_document(prepared_root, evidence.document_id)
        directory, document = documents[evidence.document_id]
        page = next((page for page in document.pages if page.page_number == evidence.page_number), None)
        if page is None:
            raise InvalidIdentityInputError("Unknown candidate page")
        candidates.append(IdentityCandidateInput(
            covered_candidate_id=candidate.covered_candidate_id, origin=candidate.origin,
            source_candidate_id=candidate.source_candidate_id, reference_hint=candidate.reference_hint,
            description=candidate.description, glass_relevance=candidate.glass_relevance,
            document_id=evidence.document_id, occurrence=evidence.occurrence,
            page_number=evidence.page_number, region=evidence.region,
            width_px=page.width_px, height_px=page.height_px,
            image_path=prepared_file(prepared_root, directory, page.image_path),
        ))
    return IdentityInput(batch_id=batch_id, source_covered_discovery_sha256=source_hash, candidates=candidates)

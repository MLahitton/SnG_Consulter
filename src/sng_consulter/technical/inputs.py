"""Validate persisted lineage and assemble existing full-page and local evidence."""

from hashlib import sha256
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from sng_consulter.discovery.inputs import build_discovery_input
from sng_consulter.identity.audit.models import IdentityAuditResult
from sng_consulter.identity.gate import IdentityGateResult, IdentityGateStatus
from sng_consulter.identity.inputs import build_identity_input
from sng_consulter.identity.models import BatchId, IdentityResolutionResult
from sng_consulter.identity.validation import validate_identity_partition
from sng_consulter.storage import prepared_file

from .models import TechnicalExtractionInput, TechnicalIdentityInput, TechnicalPageEvidence


class InvalidTechnicalInputError(ValueError):
    pass


def build_technical_extraction_input(
    batch_id: str, identity_root: Path = Path("runtime/identity"),
    discovery_root: Path = Path("runtime/discovery"), batch_root: Path = Path("runtime/review/batches"),
    prepared_root: Path = Path("runtime/review/prepared"),
) -> TechnicalExtractionInput:
    batch_id = TypeAdapter(BatchId).validate_python(batch_id)
    directory = identity_root / batch_id
    identity_bytes = prepared_file(identity_root,directory,Path("identity.json")).read_bytes()
    audit_bytes = prepared_file(identity_root,directory,Path("identity_audit.json")).read_bytes()
    gate_bytes = prepared_file(identity_root,directory,Path("identity_gate.json")).read_bytes()
    identity_hash, audit_hash, gate_hash = (sha256(b).hexdigest() for b in (identity_bytes,audit_bytes,gate_bytes))
    try:
        partition = IdentityResolutionResult.model_validate_json(identity_bytes)
        audit = IdentityAuditResult.model_validate_json(audit_bytes)
        gate = IdentityGateResult.model_validate_json(gate_bytes)
    except ValidationError:
        raise InvalidTechnicalInputError("Invalid Identity, Audit or Gate contract") from None
    if gate.batch_id != batch_id or audit.batch_id != batch_id or partition.batch_id != batch_id:
        raise InvalidTechnicalInputError("Technical input batch identity mismatch")
    if gate.status != IdentityGateStatus.READY_FOR_EXTRACTION:
        raise InvalidTechnicalInputError("Technical Extraction requires READY_FOR_EXTRACTION")
    if gate.source_identity_sha256 != identity_hash or gate.source_identity_audit_sha256 != audit_hash:
        raise InvalidTechnicalInputError("Identity Gate is outdated relative to Identity or Audit")
    if audit.audited_identity_sha256 != identity_hash:
        raise InvalidTechnicalInputError("Identity Audit is outdated relative to Identity")
    if (audit.issues or gate.issue_count != 0 or gate.blocking_issue_ids
            or audit.identities_audited != len(partition.identities)
            or audit.unresolved_groups_audited != len(partition.unresolved)):
        raise InvalidTechnicalInputError("Gate READY is inconsistent with Identity Audit")
    evidence = build_identity_input(batch_id,discovery_root,batch_root,prepared_root)
    if partition.source_covered_discovery_sha256 != evidence.source_covered_discovery_sha256:
        raise InvalidTechnicalInputError("Identity is outdated relative to CoveredDiscovery")
    validate_identity_partition(partition,batch_id,{c.covered_candidate_id for c in evidence.candidates})
    candidates = {c.covered_candidate_id:c for c in evidence.candidates}
    full_pages = build_discovery_input(batch_id,batch_root,prepared_root).pages
    page_index = {(p.document_id,p.occurrence,p.page_number):p for p in full_pages}
    identities = []
    for group in partition.identities:
        local = [candidates[cid] for cid in group.candidate_ids]
        keys = list(dict.fromkeys((c.document_id,c.occurrence,c.page_number) for c in local))
        identities.append(TechnicalIdentityInput(identity_id=group.identity_id,reference_hint=group.reference_hint,
            description=group.description,candidate_ids=group.candidate_ids,candidates=local,
            pages=[TechnicalPageEvidence.model_validate(page_index[key].model_dump()) for key in keys]))
    return TechnicalExtractionInput(batch_id=batch_id,source_identity_sha256=identity_hash,
        source_identity_gate_sha256=gate_hash,identities=identities,unresolved_groups=partition.unresolved)

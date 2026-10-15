"""Structural validation shared by persisted Identity consumers; no I/O or provider."""

from .models import IdentityResolutionProposal, IdentityResolutionResult


def validate_identity_partition(
    partition: IdentityResolutionResult, batch_id: str, expected_candidate_ids: set[str] | None = None,
) -> None:
    if partition.batch_id != batch_id:
        raise ValueError("Identity batch identity mismatch")
    assignments = IdentityResolutionProposal(
        groups=[g.model_dump(exclude={"identity_id"}) for g in partition.identities],
        unresolved=[g.model_dump(exclude={"unresolved_id"}) for g in partition.unresolved],
    )
    assigned = {cid for g in [*assignments.groups, *assignments.unresolved] for cid in g.candidate_ids}
    resolved_count = sum(len(g.candidate_ids) for g in partition.identities)
    unresolved_count = sum(len(g.candidate_ids) for g in partition.unresolved)
    if (len({g.identity_id for g in partition.identities}) != len(partition.identities)
            or len({g.unresolved_id for g in partition.unresolved}) != len(partition.unresolved)
            or partition.covered_candidate_count != len(assigned)
            or partition.resolved_candidate_count != resolved_count
            or partition.unresolved_candidate_count != unresolved_count
            or (expected_candidate_ids is not None and assigned != expected_candidate_ids)):
        raise ValueError("Identity must cover all candidates exactly once with consistent counters and IDs")

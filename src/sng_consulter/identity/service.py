"""Validate a complete partition, assign deterministic IDs and publish atomically."""

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

from .models import (
    IdentityGroup, IdentityInput, IdentityResolutionProposal, IdentityResolutionResult, UnresolvedIdentity,
)


class IdentityResolver(Protocol):
    def resolve(self, identity_input: IdentityInput) -> IdentityResolutionProposal: ...


class InvalidIdentityResolutionError(ValueError):
    pass


class IdentityResolutionService:
    def __init__(self, resolver: IdentityResolver, output_root: Path = Path("runtime/identity")) -> None:
        self.resolver = resolver
        self.output_root = Path(output_root)

    def resolve(self, identity_input: IdentityInput) -> IdentityResolutionResult:
        evidence = IdentityInput.model_validate(identity_input)
        order = {candidate.covered_candidate_id: index for index, candidate in enumerate(evidence.candidates)}
        # Isolate canonical input from resolver mutation; lineage and ordering are service-owned.
        proposal = IdentityResolutionProposal.model_validate(self.resolver.resolve(evidence.model_copy(deep=True)))
        assigned = {candidate_id for group in [*proposal.groups, *proposal.unresolved] for candidate_id in group.candidate_ids}
        if assigned != set(order):
            raise InvalidIdentityResolutionError("Resolution must cover every input candidate exactly once, without unknown IDs")

        def ordered(groups):
            return sorted(groups, key=lambda group: min(order[candidate_id] for candidate_id in group.candidate_ids))

        identities = [IdentityGroup(
            **group.model_dump(exclude={"candidate_ids"}), candidate_ids=sorted(group.candidate_ids, key=order.__getitem__),
            identity_id=f"identity-{index:04d}",
        ) for index, group in enumerate(ordered(proposal.groups), 1)]
        unresolved = [UnresolvedIdentity(
            reason=group.reason, candidate_ids=sorted(group.candidate_ids, key=order.__getitem__),
            unresolved_id=f"unresolved-{index:04d}",
        ) for index, group in enumerate(ordered(proposal.unresolved), 1)]
        resolved_count = sum(len(group.candidate_ids) for group in identities)
        result = IdentityResolutionResult(
            batch_id=evidence.batch_id, source_covered_discovery_sha256=evidence.source_covered_discovery_sha256,
            identities=identities, unresolved=unresolved, covered_candidate_count=len(order),
            resolved_candidate_count=resolved_count, unresolved_candidate_count=len(order) - resolved_count,
        )
        root = self.output_root.resolve()
        directory = (root / result.batch_id).resolve()
        if not directory.is_relative_to(root):
            raise ValueError("Identity output must stay within its root")
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "identity.json"
        if not target.resolve().is_relative_to(directory):
            raise ValueError("Identity output must stay within its batch")
        with TemporaryDirectory(prefix=".staging-", dir=directory) as staging:
            temporary = Path(staging) / "identity.json"
            temporary.write_text(result.model_dump_json(indent=2), encoding="utf-8")
            temporary.replace(target)
        return result

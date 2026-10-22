"""Validate exact applied snapshots and lineage before creating history-free targets."""

from hashlib import sha256
from pathlib import Path

from sng_consulter.storage import prepared_file
from ..inputs import build_technical_extraction_input
from ..models import TechnicalExtractionResult
from ..resolution.diff import structural_diff
from ..resolution.inputs import build_technical_resolution_input
from ..resolution.models import TechnicalIssueResolutionOutcome
from ..resolution.result_models import TechnicalResolutionResult
from ..resolution.validation import path_is_within_allowed_root
from ..validation import validate_technical_result
from .models import TechnicalVerificationInput, TechnicalVerificationTarget


class InvalidTechnicalVerificationInputError(ValueError):
    pass


def build_technical_verification_input(
    batch_id: str, technical_root: Path = Path("runtime/technical"),
    identity_root: Path = Path("runtime/identity"), discovery_root: Path = Path("runtime/discovery"),
    batch_root: Path = Path("runtime/review/batches"), prepared_root: Path = Path("runtime/review/prepared"),
) -> TechnicalVerificationInput:
    try:
        work = build_technical_resolution_input(batch_id, technical_root, identity_root, discovery_root, batch_root, prepared_root)
        root = Path(technical_root)
        directory = root / work.batch_id
        raw = prepared_file(root, directory, Path("technical_resolution.json")).read_bytes()
        resolution_hash = sha256(raw).hexdigest()
        resolution = TechnicalResolutionResult.model_validate_json(raw)
        if (resolution.batch_id != work.batch_id or resolution.resolution_required != work.resolution_required
                or any(getattr(resolution, name) != getattr(work, name) for name in (
                    "source_technical_extraction_sha256", "source_technical_audit_sha256", "source_technical_gate_sha256"))):
            raise ValueError("Applied Resolution source lineage mismatch")
        if work.resolution_required:
            proposals = prepared_file(root, directory, Path("technical_resolution_proposals.json")).read_bytes()
            if sha256(proposals).hexdigest() != resolution.source_technical_resolution_proposals_sha256:
                raise ValueError("Applied Resolution proposals are stale")
        # READY deliberately neither reads nor requires historical proposals.
        issues = [issue for target in work.targets for issue in target.issues]
        if [i.issue_id for i in resolution.issue_resolutions] != [i.issue_id for i in issues]:
            raise ValueError("Applied issue lineage differs from current Audit")
        evidence = build_technical_extraction_input(work.batch_id, identity_root, discovery_root, batch_root, prepared_root)
        validate_technical_result(resolution, evidence)
        # Preserve the immutable identities certified by 5C, including clean structures.
        baseline_raw = prepared_file(root, directory, Path("technical_extraction.json")).read_bytes()
        if sha256(baseline_raw).hexdigest() != work.source_technical_extraction_sha256:
            raise ValueError("Extraction changed between snapshot reads")
        baseline = TechnicalExtractionResult.model_validate_json(baseline_raw)
        outcomes = {outcome.issue_id: outcome for outcome in resolution.issue_resolutions}
        targets = {target.technical_structure_id: target for target in work.targets}
        actual_changes = set()
        for old, current in zip(baseline.structures, resolution.structures):
            paths = [p for p in structural_diff(old, current) if p not in {"missing_fields", "resolution_status"}]
            target = targets.get(old.technical_structure_id)
            corrected = []
            for issue in target.issues if target else []:
                outcome = outcomes[issue.issue_id]
                if not set(outcome.evidence_candidate_ids) <= set(target.identity.candidate_ids):
                    raise ValueError("Applied issue evidence is foreign")
                expected = ([p for p in paths if any(path_is_within_allowed_root(p, root) for root in issue.field_paths)]
                            if outcome.outcome == TechnicalIssueResolutionOutcome.CORRECTED else [])
                if outcome.changed_paths != expected:
                    raise ValueError("Applied issue paths do not match the current snapshot")
                corrected.extend(expected)
            if set(paths) != set(corrected):
                raise ValueError("Applied snapshot contains uncertified changes")
            actual_changes.update((old.technical_structure_id, path) for path in paths)
        if resolution.changes_applied_count != len(actual_changes):
            raise ValueError("Applied change counter mismatch")
        return TechnicalVerificationInput(batch_id=work.batch_id, source_technical_resolution_sha256=resolution_hash,
            targets=[TechnicalVerificationTarget(identity_id=s.identity_id, technical_structure_id=s.technical_structure_id,
                      current_structure=s, identity=i) for s, i in zip(resolution.structures, evidence.identities)],
            resolved_identity_count=resolution.resolved_identity_count, unresolved_groups=resolution.unresolved_groups)
    except (ValueError, OSError):
        raise InvalidTechnicalVerificationInputError(
            "Missing, invalid, stale or inconsistent applied Resolution / source lineage / canonical evidence") from None

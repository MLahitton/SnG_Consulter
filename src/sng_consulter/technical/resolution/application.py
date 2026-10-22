"""Offline all-or-nothing authorization, baseline apply and canonical validation."""

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import ValidationError

from sng_consulter.storage import prepared_file
from ..audit.models import TechnicalAuditResult
from ..inputs import build_technical_extraction_input
from ..models import TechnicalExtractionResult, TechnicalStructure
from ..service import technical_bookkeeping
from ..validation import validate_technical_result
from .diff import apply_diff_paths, structural_diff
from .inputs import build_technical_resolution_input
from .models import TechnicalIssueResolutionOutcome
from .result_models import TechnicalAppliedIssueResolution, TechnicalResolutionResult
from .service import TechnicalResolutionProposalResult, validate_resolution_proposal
from .validation import path_is_within_allowed_root


class InvalidTechnicalResolutionApplicationInputError(ValueError):
    pass


class UnauthorizedTechnicalResolutionChangeError(ValueError):
    pass


def apply_structure(target, proposal):
    """Certify every diff, attribute it only to CORRECTED issues, then copy paths."""
    proposal = validate_resolution_proposal(target, proposal)
    paths = structural_diff(target.current_structure, proposal.proposed_structure)
    corrected = [(issue, outcome) for issue, outcome in zip(target.issues, proposal.issue_resolutions)
                 if outcome.outcome == TechnicalIssueResolutionOutcome.CORRECTED]
    for path in paths:
        if (not any(path_is_within_allowed_root(path, root) for root in target.allowed_change_paths)
                or not any(path_is_within_allowed_root(path, root)
                           for issue, _ in corrected for root in issue.field_paths)):
            raise UnauthorizedTechnicalResolutionChangeError("Semantic change is not authorized by a CORRECTED issue")
    outcomes = []
    for issue, outcome in zip(target.issues, proposal.issue_resolutions):
        changed = ([path for path in paths if any(path_is_within_allowed_root(path, root)
                                                 for root in issue.field_paths)]
                   if outcome.outcome == TechnicalIssueResolutionOutcome.CORRECTED else [])
        if outcome.outcome == TechnicalIssueResolutionOutcome.CORRECTED and not changed:
            raise UnauthorizedTechnicalResolutionChangeError("CORRECTED issue has no real authorized change")
        outcomes.append(TechnicalAppliedIssueResolution(**outcome.model_dump(), changed_paths=changed))
    data = apply_diff_paths(target.current_structure.model_dump(mode="json"),
                            proposal.proposed_structure.model_dump(mode="json"), paths)
    applied = TechnicalStructure.model_validate(data)
    applied.missing_fields, applied.resolution_status = technical_bookkeeping(applied)
    return TechnicalStructure.model_validate(applied), outcomes, paths


class TechnicalResolutionApplicationService:
    def __init__(self, technical_root: Path = Path("runtime/technical"),
                 identity_root: Path = Path("runtime/identity"), discovery_root: Path = Path("runtime/discovery"),
                 batch_root: Path = Path("runtime/review/batches"), prepared_root: Path = Path("runtime/review/prepared")):
        self.technical_root = Path(technical_root)
        self.evidence_roots = tuple(Path(p) for p in (identity_root, discovery_root, batch_root, prepared_root))

    def apply(self, batch_id: str) -> TechnicalResolutionResult:
        work = build_technical_resolution_input(batch_id, self.technical_root, *self.evidence_roots)
        directory = self.technical_root / work.batch_id

        def snapshot(name, expected_hash):
            raw = prepared_file(self.technical_root, directory, Path(name)).read_bytes()
            if sha256(raw).hexdigest() != expected_hash:
                raise InvalidTechnicalResolutionApplicationInputError("Resolution source changed between snapshot reads")
            return raw

        try:
            extraction = TechnicalExtractionResult.model_validate_json(snapshot(
                "technical_extraction.json", work.source_technical_extraction_sha256))
            audit = TechnicalAuditResult.model_validate_json(snapshot(
                "technical_audit.json", work.source_technical_audit_sha256))
            snapshot("technical_gate.json", work.source_technical_gate_sha256)
            evidence = build_technical_extraction_input(work.batch_id, *self.evidence_roots)
            validate_technical_result(extraction, evidence)
            proposals_hash = None
            proposals = []
            if work.resolution_required:
                try:
                    raw = prepared_file(self.technical_root, directory, Path("technical_resolution_proposals.json")).read_bytes()
                except FileNotFoundError:
                    raise InvalidTechnicalResolutionApplicationInputError("BLOCKED requires technical_resolution_proposals.json") from None
                proposals_hash = sha256(raw).hexdigest()
                source = TechnicalResolutionProposalResult.model_validate_json(raw)
                if (source.batch_id != work.batch_id or source.targets_processed != len(work.targets)
                        or len(source.proposals) != len(work.targets)
                        or any(getattr(source, name) != getattr(work, name) for name in (
                            "source_technical_extraction_sha256", "source_technical_audit_sha256", "source_technical_gate_sha256"))):
                    raise InvalidTechnicalResolutionApplicationInputError("Invalid or stale Resolution proposal lineage / target count")
                proposals = source.proposals
            structures = {s.technical_structure_id: s.model_copy(deep=True) for s in extraction.structures}
            outcomes = []
            changes = set()
            for target, proposal in zip(work.targets, proposals):
                applied, applied_outcomes, paths = apply_structure(target, proposal)
                structures[target.technical_structure_id] = applied
                outcomes.extend(applied_outcomes)
                changes.update((target.technical_structure_id, path) for path in paths)
            if [i.issue_id for i in outcomes] != [i.issue_id for i in audit.issues]:
                raise InvalidTechnicalResolutionApplicationInputError("Applied outcomes must match exact Audit issue order")
            ordered = [structures[s.technical_structure_id] for s in extraction.structures]
            for structure in ordered:
                structure.missing_fields, structure.resolution_status = technical_bookkeeping(structure)
            result = TechnicalResolutionResult(**extraction.model_dump(exclude={"structures"}), structures=ordered,
                source_technical_extraction_sha256=work.source_technical_extraction_sha256,
                source_technical_audit_sha256=work.source_technical_audit_sha256,
                source_technical_gate_sha256=work.source_technical_gate_sha256,
                source_technical_resolution_proposals_sha256=proposals_hash,
                resolution_required=work.resolution_required, issue_resolutions=outcomes,
                corrected_issue_count=sum(i.outcome == TechnicalIssueResolutionOutcome.CORRECTED for i in outcomes),
                disputed_issue_count=sum(i.outcome == TechnicalIssueResolutionOutcome.DISPUTED for i in outcomes),
                unresolved_issue_count=sum(i.outcome == TechnicalIssueResolutionOutcome.UNRESOLVED for i in outcomes),
                changes_applied_count=len(changes))
            validate_technical_result(result, evidence)
        except ValidationError:
            raise InvalidTechnicalResolutionApplicationInputError("Invalid Resolution source, proposal or applied result contract") from None
        root = self.technical_root.resolve()
        directory = directory.resolve()
        target = directory / "technical_resolution.json"
        if not directory.is_relative_to(root) or not target.resolve().is_relative_to(directory):
            raise InvalidTechnicalResolutionApplicationInputError("Resolution output must stay within its batch")
        with TemporaryDirectory(prefix=".staging-", dir=directory) as staging:
            temporary = Path(staging) / "technical_resolution.json"
            temporary.write_text(result.model_dump_json(indent=2), encoding="utf-8")
            temporary.replace(target)
        return result

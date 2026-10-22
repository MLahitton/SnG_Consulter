"""Applied snapshot contracts; not a final certified Backend result."""

from pydantic import Field, model_validator

from sng_consulter.identity.models import Sha256
from ..models import TechnicalExtractionResult
from .models import TechnicalIssueResolutionProposal, TechnicalIssueResolutionOutcome


class TechnicalAppliedIssueResolution(TechnicalIssueResolutionProposal):
    changed_paths: list[str]

    @model_validator(mode="after")
    def consistent_changes(self):
        if len(self.changed_paths) != len(set(self.changed_paths)):
            raise ValueError("Applied paths must be unique per issue")
        if (self.outcome == TechnicalIssueResolutionOutcome.CORRECTED) != bool(self.changed_paths):
            raise ValueError("Only CORRECTED outcomes must have applied changes")
        if self.outcome != TechnicalIssueResolutionOutcome.UNRESOLVED and not self.evidence_candidate_ids:
            raise ValueError("CORRECTED and DISPUTED require evidence")
        return self


class TechnicalResolutionResult(TechnicalExtractionResult):
    source_technical_extraction_sha256: Sha256
    source_technical_audit_sha256: Sha256
    source_technical_gate_sha256: Sha256
    source_technical_resolution_proposals_sha256: Sha256 | None
    resolution_required: bool = Field(strict=True)
    issue_resolutions: list[TechnicalAppliedIssueResolution]
    corrected_issue_count: int = Field(ge=0)
    disputed_issue_count: int = Field(ge=0)
    unresolved_issue_count: int = Field(ge=0)
    changes_applied_count: int = Field(ge=0)

    @model_validator(mode="after")
    def consistent_resolution(self):
        if (self.resolution_required != bool(self.issue_resolutions)
                or self.resolution_required != (self.source_technical_resolution_proposals_sha256 is not None)):
            raise ValueError("Resolution mode, issues and proposal lineage must agree")
        ids = [issue.issue_id for issue in self.issue_resolutions]
        if len(ids) != len(set(ids)):
            raise ValueError("Applied issue IDs must be unique")
        for outcome, count in ((TechnicalIssueResolutionOutcome.CORRECTED, self.corrected_issue_count),
                               (TechnicalIssueResolutionOutcome.DISPUTED, self.disputed_issue_count),
                               (TechnicalIssueResolutionOutcome.UNRESOLVED, self.unresolved_issue_count)):
            if count != sum(issue.outcome == outcome for issue in self.issue_resolutions):
                raise ValueError("Applied outcome counters are inconsistent")
        paths = [path for issue in self.issue_resolutions for path in issue.changed_paths]
        # Exact uniqueness includes target ownership and is certified by the application service.
        if not len(set(paths)) <= self.changes_applied_count <= len(paths):
            raise ValueError("Applied change counter is inconsistent")
        return self

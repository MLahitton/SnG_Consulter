"""Offline Resolution foundation; no resolver, apply step or output persistence."""

from .inputs import build_technical_resolution_input, InvalidTechnicalResolutionInputError
from .models import (
    TechnicalResolutionIssue, TechnicalResolutionTarget, TechnicalResolutionInput,
    TechnicalIssueResolutionOutcome, TechnicalIssueResolutionProposal, TechnicalStructureResolutionProposal,
)
from .validation import normalize_allowed_change_paths, path_is_within_allowed_root

__all__ = [
    "build_technical_resolution_input", "InvalidTechnicalResolutionInputError",
    "TechnicalResolutionIssue", "TechnicalResolutionTarget", "TechnicalResolutionInput",
    "TechnicalIssueResolutionOutcome", "TechnicalIssueResolutionProposal", "TechnicalStructureResolutionProposal",
    "normalize_allowed_change_paths", "path_is_within_allowed_root",
]

"""Resolution foundation and proposal service; provider adapter is loaded lazily."""

from .inputs import build_technical_resolution_input, InvalidTechnicalResolutionInputError
from .models import (
    TechnicalResolutionIssue, TechnicalResolutionTarget, TechnicalResolutionInput,
    TechnicalIssueResolutionOutcome, TechnicalIssueResolutionProposal, TechnicalStructureResolutionProposal,
)
from .validation import normalize_allowed_change_paths, path_is_within_allowed_root
from .service import (
    TechnicalResolutionProposalService, TechnicalResolutionProposalResult,
    TechnicalResolutionConfigurationError, TechnicalResolutionAPIError,
    InvalidTechnicalResolutionResponseError, TechnicalResolutionContextBudgetExceeded,
)

__all__ = [
    "build_technical_resolution_input", "InvalidTechnicalResolutionInputError",
    "TechnicalResolutionIssue", "TechnicalResolutionTarget", "TechnicalResolutionInput",
    "TechnicalIssueResolutionOutcome", "TechnicalIssueResolutionProposal", "TechnicalStructureResolutionProposal",
    "normalize_allowed_change_paths", "path_is_within_allowed_root",
    "TechnicalResolutionProposalService", "TechnicalResolutionProposalResult",
    "TechnicalResolutionConfigurationError", "TechnicalResolutionAPIError",
    "InvalidTechnicalResolutionResponseError", "TechnicalResolutionContextBudgetExceeded",
]

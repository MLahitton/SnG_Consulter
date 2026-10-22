"""Resolution foundation, proposals and deterministic apply; providers stay lazy."""

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
from .application import (
    TechnicalResolutionApplicationService, InvalidTechnicalResolutionApplicationInputError,
    UnauthorizedTechnicalResolutionChangeError,
)
from .diff import UnsupportedTechnicalResolutionCompositionChangeError
from .result_models import TechnicalAppliedIssueResolution, TechnicalResolutionResult

__all__ = [
    "build_technical_resolution_input", "InvalidTechnicalResolutionInputError",
    "TechnicalResolutionIssue", "TechnicalResolutionTarget", "TechnicalResolutionInput",
    "TechnicalIssueResolutionOutcome", "TechnicalIssueResolutionProposal", "TechnicalStructureResolutionProposal",
    "normalize_allowed_change_paths", "path_is_within_allowed_root",
    "TechnicalResolutionProposalService", "TechnicalResolutionProposalResult",
    "TechnicalResolutionConfigurationError", "TechnicalResolutionAPIError",
    "InvalidTechnicalResolutionResponseError", "TechnicalResolutionContextBudgetExceeded",
    "TechnicalResolutionApplicationService", "InvalidTechnicalResolutionApplicationInputError",
    "UnauthorizedTechnicalResolutionChangeError", "UnsupportedTechnicalResolutionCompositionChangeError",
    "TechnicalAppliedIssueResolution", "TechnicalResolutionResult",
]

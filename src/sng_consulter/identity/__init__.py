"""Identity contracts and orchestration; provider adapters are imported explicitly."""

from .inputs import InvalidIdentityInputError, build_identity_input
from .models import (
    IdentityCandidateInput, IdentityGroup, IdentityGroupProposal, IdentityInput,
    IdentityResolutionProposal, IdentityResolutionResult, UnresolvedIdentity, UnresolvedIdentityProposal,
)
from .service import IdentityResolutionService, IdentityResolver, InvalidIdentityResolutionError

__all__ = [
    "IdentityCandidateInput", "IdentityGroup", "IdentityGroupProposal", "IdentityInput",
    "IdentityResolutionProposal", "IdentityResolutionResult", "UnresolvedIdentity", "UnresolvedIdentityProposal",
    "IdentityResolutionService", "IdentityResolver", "InvalidIdentityResolutionError",
    "InvalidIdentityInputError", "build_identity_input",
]

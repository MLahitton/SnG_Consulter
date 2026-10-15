"""Explicit manual execution of the existing Identity pipeline with OpenAI."""

import argparse
import sys

from pydantic import ValidationError

from sng_consulter.storage import ContentIdentityConflictError, IncompatiblePreparedDocumentError

from .inputs import InvalidIdentityInputError, build_identity_input
from .openai_resolver import (
    IdentityContextBudgetExceeded, InvalidIdentityResponseError, OpenAIIdentityAPIError,
    OpenAIIdentityConfigurationError, OpenAIIdentityResolver,
)
from .service import IdentityResolutionService, InvalidIdentityResolutionError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve a persisted CoveredDiscovery batch with OpenAI")
    parser.add_argument("batch_id")
    args = parser.parse_args(argv)
    resolver = None
    try:
        evidence = build_identity_input(args.batch_id)
        resolver = OpenAIIdentityResolver()
        result = IdentityResolutionService(resolver).resolve(evidence)
    except (InvalidIdentityInputError, InvalidIdentityResolutionError, IdentityContextBudgetExceeded,
            InvalidIdentityResponseError, OpenAIIdentityAPIError, OpenAIIdentityConfigurationError,
            IncompatiblePreparedDocumentError, ContentIdentityConflictError, FileNotFoundError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    except ValidationError:
        # Do not print Pydantic input values or the original provider payload.
        print("ValidationError: Invalid Identity input or resolution contract", file=sys.stderr)
        return 1
    finally:
        if resolver is not None:
            resolver.close()
    print(f"Identity completed: {len(result.identities)} provisional identities, "
          f"{result.unresolved_candidate_count} unresolved candidates. "
          f"Result: runtime/identity/{result.batch_id}/identity.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

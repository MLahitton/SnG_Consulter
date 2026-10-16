"""Manual real extraction from an existing READY TechnicalExtractionInput."""

import argparse
import sys

from pydantic import ValidationError

from sng_consulter.storage import ContentIdentityConflictError, IncompatiblePreparedDocumentError
from sng_consulter.identity.inputs import InvalidIdentityInputError

from .inputs import build_technical_extraction_input, InvalidTechnicalInputError
from .openai_extractor import (
    OpenAITechnicalExtractor, TechnicalAPIError, TechnicalConfigurationError,
    InvalidTechnicalResponseError, TechnicalContextBudgetExceeded,
)
from .service import TechnicalExtractionService, InvalidTechnicalProposalError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract technical proposals from a persisted READY batch")
    parser.add_argument("batch_id")
    args = parser.parse_args(argv)
    extractor = None
    try:
        evidence = build_technical_extraction_input(args.batch_id)
        extractor = OpenAITechnicalExtractor()
        result = TechnicalExtractionService(extractor).extract(evidence)
    except (InvalidTechnicalInputError,InvalidIdentityInputError,InvalidTechnicalProposalError,
            TechnicalAPIError,TechnicalConfigurationError,InvalidTechnicalResponseError,TechnicalContextBudgetExceeded,
            ContentIdentityConflictError,IncompatiblePreparedDocumentError,FileNotFoundError) as exc:
        print(f"{type(exc).__name__}: {exc}",file=sys.stderr)
        return 1
    except ValidationError:
        print("ValidationError: Invalid technical input or proposal contract",file=sys.stderr)
        return 1
    finally:
        if extractor is not None:
            extractor.close()
    print(f"Technical Extraction completed: {len(result.structures)} structures, "
          f"{result.unresolved_group_count} unresolved Identity groups preserved. "
          f"Result: runtime/technical/{result.batch_id}/technical_extraction.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

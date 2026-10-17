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


def _audit_main(argv: list[str]) -> int:
    from .audit.service import TechnicalAuditService, InvalidTechnicalAuditInputError, InvalidTechnicalAuditResponseError
    from .audit.openai_auditor import (
        OpenAITechnicalAuditor, TechnicalAuditConfigurationError, TechnicalAuditAPIError,
        TechnicalAuditContextBudgetExceeded,
    )
    parser = argparse.ArgumentParser(description="Independently audit persisted Technical Extraction")
    parser.add_argument("batch_id")
    args = parser.parse_args(argv)
    auditor = None
    try:
        auditor = OpenAITechnicalAuditor()
        result = TechnicalAuditService(auditor).audit(args.batch_id)
    except (InvalidTechnicalAuditInputError, InvalidTechnicalAuditResponseError, TechnicalAuditConfigurationError,
            TechnicalAuditAPIError, TechnicalAuditContextBudgetExceeded, OSError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if auditor is not None:
            auditor.close()
    print(f"Technical Audit completed: {result.structures_audited} structures audited, {result.issue_count} issues. "
          f"Result: runtime/technical/{result.batch_id}/technical_audit.json")
    return 0


def _gate_main(argv: list[str]) -> int:
    from .gate import TechnicalGateService, InvalidTechnicalGateInputError

    parser = argparse.ArgumentParser(description="Offline deterministic gate over persisted technical artifacts")
    parser.add_argument("batch_id")
    args = parser.parse_args(argv)
    try:
        result = TechnicalGateService().gate(args.batch_id)
    except (InvalidTechnicalGateInputError, OSError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"Technical Gate: {result.status}\n{result.issue_count} blocking issues\n"
          f"Result: runtime/technical/{result.batch_id}/technical_gate.json")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "audit":
        return _audit_main(argv[1:])
    if argv and argv[0] == "gate":
        return _gate_main(argv[1:])
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

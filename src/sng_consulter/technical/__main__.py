"""Manual technical commands; deterministic commands load no provider adapters."""

import argparse
import sys

from pydantic import ValidationError

from sng_consulter.storage import ContentIdentityConflictError, IncompatiblePreparedDocumentError
from sng_consulter.identity.inputs import InvalidIdentityInputError

from .inputs import build_technical_extraction_input, InvalidTechnicalInputError
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


def _resolve_main(argv: list[str]) -> int:
    from .resolution.inputs import InvalidTechnicalResolutionInputError
    from .resolution.service import (
        TechnicalResolutionProposalService,
        TechnicalResolutionConfigurationError, TechnicalResolutionAPIError,
        InvalidTechnicalResolutionResponseError, TechnicalResolutionContextBudgetExceeded,
    )
    parser = argparse.ArgumentParser(description="Propose issue-targeted Resolution without applying it")
    parser.add_argument("batch_id")
    args = parser.parse_args(argv)
    try:
        result = TechnicalResolutionProposalService().resolve(args.batch_id)
    except (InvalidTechnicalResolutionInputError, TechnicalResolutionConfigurationError, TechnicalResolutionAPIError,
            InvalidTechnicalResolutionResponseError, TechnicalResolutionContextBudgetExceeded, OSError, ValueError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if result is None:
        print("Technical Resolution not required: READY_FOR_VERIFICATION\n0 targets")
    else:
        print(f"Technical Resolution proposals completed: {result.targets_processed} targets\n"
              f"Result: runtime/technical/{result.batch_id}/technical_resolution_proposals.json")
    return 0


def _apply_resolution_main(argv: list[str]) -> int:
    from .resolution.application import TechnicalResolutionApplicationService
    from .resolution.service import InvalidTechnicalResolutionResponseError

    parser = argparse.ArgumentParser(description="Offline deterministic Resolution apply / validation")
    parser.add_argument("batch_id")
    args = parser.parse_args(argv)
    try:
        result = TechnicalResolutionApplicationService().apply(args.batch_id)
    except (ValueError, InvalidTechnicalResolutionResponseError, OSError) as exc:
        # Error classes distinguish invalid inputs, authorization, composition and system failure.
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if not result.resolution_required:
        print("Technical Resolution applied: 0 issues, 0 changes")
    else:
        print(f"Technical Resolution applied:\nCORRECTED: {result.corrected_issue_count}\n"
              f"DISPUTED: {result.disputed_issue_count}\nUNRESOLVED: {result.unresolved_issue_count}\n"
              f"Changes: {result.changes_applied_count}")
    print(f"Result: runtime/technical/{result.batch_id}/technical_resolution.json")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "audit":
        return _audit_main(argv[1:])
    if argv and argv[0] == "gate":
        return _gate_main(argv[1:])
    if argv and argv[0] == "resolve":
        return _resolve_main(argv[1:])
    if argv and argv[0] == "apply-resolution":
        return _apply_resolution_main(argv[1:])
    from .openai_extractor import (
        OpenAITechnicalExtractor, TechnicalAPIError, TechnicalConfigurationError,
        InvalidTechnicalResponseError, TechnicalContextBudgetExceeded,
    )
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

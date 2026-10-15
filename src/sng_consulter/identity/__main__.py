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
from .gate import IdentityGateService, InvalidIdentityGateInputError
from .audit.service import IdentityAuditService, InvalidIdentityAuditInputError, InvalidIdentityAuditProposalError
from .audit.openai_auditor import (
    OpenAIIdentityAuditor, IdentityAuditConfigurationError, IdentityAuditAPIError,
    InvalidIdentityAuditResponseError, IdentityAuditContextBudgetExceeded,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Identity resolution, independent audit, or offline gate")
    parser.add_argument("command_or_batch", help="BATCH_ID, audit BATCH_ID, or gate BATCH_ID")
    parser.add_argument("batch_id", nargs="?")
    args = parser.parse_args(argv)
    audit = args.command_or_batch == "audit"
    gate = args.command_or_batch == "gate"
    subcommand = audit or gate
    if (subcommand and args.batch_id is None) or (not subcommand and args.batch_id is not None):
        parser.error("Use BATCH_ID, audit BATCH_ID, or gate BATCH_ID")
    batch_id = args.batch_id if subcommand else args.command_or_batch
    resolver = None
    try:
        if gate:
            result = IdentityGateService().gate(batch_id)
            print(f"Identity Gate: {result.status.value}\n{result.issue_count} blocking issues")
            return 0
        if audit:
            resolver = OpenAIIdentityAuditor()
            result = IdentityAuditService(resolver).audit(batch_id)
            print(f"Identity Audit completed: {result.identities_audited} identities, "
                  f"{result.unresolved_groups_audited} unresolved groups, {len(result.issues)} issues. "
                  f"Result: runtime/identity/{result.batch_id}/identity_audit.json")
            return 0
        evidence = build_identity_input(batch_id)
        resolver = OpenAIIdentityResolver()
        result = IdentityResolutionService(resolver).resolve(evidence)
    except (InvalidIdentityInputError, InvalidIdentityResolutionError, IdentityContextBudgetExceeded,
            InvalidIdentityResponseError, OpenAIIdentityAPIError, OpenAIIdentityConfigurationError,
            InvalidIdentityAuditInputError, InvalidIdentityAuditProposalError, IdentityAuditConfigurationError,
            IdentityAuditAPIError, InvalidIdentityAuditResponseError, IdentityAuditContextBudgetExceeded,
            InvalidIdentityGateInputError,
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

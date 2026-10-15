"""Manual analysis commands and offline consolidation of persisted results."""

import argparse
import sys

from sng_consulter.storage import ContentIdentityConflictError, IncompatiblePreparedDocumentError

from .inputs import build_discovery_input
from .openai_analyzer import (
    InvalidDiscoveryResponseError, OpenAIAnalyzerAPIError,
    OpenAIAnalyzerConfigurationError, OpenAIPageAnalyzer,
)
from .service import DiscoveryService
from .coverage.service import CoverageAuditService, InvalidCoverageInputError
from .coverage.openai_analyzer import OpenAICoverageAuditor, InvalidCoverageResponseError
from .consolidation import CoveredDiscoveryService, InvalidConsolidationInputError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Discovery, Coverage Audit, or offline consolidation")
    parser.add_argument("command_or_batch", help="Batch ID, or coverage/consolidate followed by Batch ID")
    parser.add_argument("batch_id", nargs="?")
    args = parser.parse_args(argv)
    coverage = args.command_or_batch == "coverage"
    consolidate = args.command_or_batch == "consolidate"
    subcommand = coverage or consolidate
    if (subcommand and args.batch_id is None) or (not subcommand and args.batch_id is not None):
        parser.error("Use BATCH_ID, coverage BATCH_ID, or consolidate BATCH_ID")
    batch_id = args.batch_id if subcommand else args.command_or_batch
    analyzer = None
    try:
        if consolidate:
            result = CoveredDiscoveryService().consolidate(batch_id)
            print(f"Consolidation completed: {result.discovery_count} Discovery + "
                  f"{result.coverage_added_count} Coverage = {result.total_candidates} candidates. "
                  f"Result: runtime/discovery/{result.batch_id}/covered_discovery.json")
            return 0
        evidence = build_discovery_input(batch_id)
        analyzer = OpenAICoverageAuditor() if coverage else OpenAIPageAnalyzer()
        result = (CoverageAuditService(analyzer).audit(evidence) if coverage
                  else DiscoveryService(analyzer).discover(evidence))
    except (OpenAIAnalyzerConfigurationError, OpenAIAnalyzerAPIError, InvalidDiscoveryResponseError,
            InvalidCoverageInputError, InvalidCoverageResponseError, InvalidConsolidationInputError,
            IncompatiblePreparedDocumentError, ContentIdentityConflictError, FileNotFoundError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if analyzer is not None:
            analyzer.close()
    if coverage:
        print(f"Coverage Audit completed: {result.pages_audited} pages, {len(result.missing_candidates)} missing candidates. "
              f"Result: runtime/discovery/{result.batch_id}/coverage_audit.json")
    else:
        print(f"Discovery completed: {result.pages_analyzed} pages, {len(result.candidates)} candidates. "
              f"Result: runtime/discovery/{result.batch_id}/discovery.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

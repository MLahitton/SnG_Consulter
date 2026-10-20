"""Resolve only persisted targets and publish proposals, never applied structures."""

from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_core import ErrorType

from sng_consulter.identity.models import BatchId, Sha256
from ..models import FieldResolution
from ..service import _resolved_fields
from .inputs import build_technical_resolution_input
from .models import TechnicalResolutionTarget, TechnicalStructureResolutionProposal, TechnicalIssueResolutionOutcome
from .validation import path_is_within_allowed_root


class TechnicalResolutionConfigurationError(ValueError):
    pass


class TechnicalResolutionAPIError(RuntimeError):
    pass


class InvalidTechnicalResolutionResponseError(RuntimeError):
    pass


class TechnicalResolutionContextBudgetExceeded(ValueError):
    pass


def safe_resolution_validation_summary(error: ValidationError) -> str:
    """Report schema locations and trusted messages, never provider-controlled input."""
    schema = TechnicalStructureResolutionProposal.model_json_schema()
    fields = set(schema.get("properties", {}))
    for definition in schema.get("$defs", {}).values():
        fields.update(definition.get("properties", {}))
    messages = {
        "Duplicate evidence candidate IDs",
        "UNKNOWN and CONFLICT require null value",
        "Resolved values must not be null",
        "This resolution requires documentary evidence",
        "UNKNOWN and DEFAULTED must not cite evidence",
        "This resolution requires an explanatory note",
        "Known glass_pane_count must equal the number of proposed physical panes",
        "Known module_count must equal the number of proposed functional modules",
        "Resolution reason must not be blank and evidence must be unique",
    }
    builtin_messages = {
        "missing": "Field required", "extra_forbidden": "Extra inputs are not permitted",
        "string_type": "Input should be a valid string", "int_type": "Input should be a valid integer",
        "float_type": "Input should be a valid number", "list_type": "Input should be a valid list",
        "enum": "Input should be a permitted enum value", "json_invalid": "Invalid JSON",
    }
    summaries = []
    for item in error.errors(include_input=False, include_context=False, include_url=False):
        path = ".".join(str(part) if isinstance(part, int) or part in fields else "<unknown-field>"
                        for part in item["loc"]) or "<root>"
        kind = item["type"]
        message = item["msg"].removeprefix("Value error, ")
        if kind == "value_error":
            message = message if message in messages else "Invalid value"
        else:
            message = builtin_messages.get(kind, "Invalid value for schema constraint")
            # Unknown custom error types may themselves contain provider-controlled text.
            if kind not in get_args(ErrorType):
                kind = "validation_error"
        summaries.append(f"{path} [{kind}]: {message}")
    return "; ".join(sorted(summaries))


class TechnicalResolutionProposalResult(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")
    batch_id: BatchId
    source_technical_extraction_sha256: Sha256
    source_technical_audit_sha256: Sha256
    source_technical_gate_sha256: Sha256
    targets_processed: int = Field(ge=0)
    proposals: list[TechnicalStructureResolutionProposal]

    @model_validator(mode="after")
    def consistent_count(self):
        if self.targets_processed != len(self.proposals):
            raise ValueError("Resolution proposal count mismatch")
        return self


class TechnicalResolver(Protocol):
    def resolve(self, target: TechnicalResolutionTarget) -> TechnicalStructureResolutionProposal: ...
    def close(self) -> None: ...


def validate_resolution_proposal(target: TechnicalResolutionTarget, raw) -> TechnicalStructureResolutionProposal:
    try:
        proposal = TechnicalStructureResolutionProposal.model_validate(raw)
        structure = proposal.proposed_structure
        if (proposal.identity_id != target.identity_id or structure.identity_id != target.identity_id
                or proposal.technical_structure_id != target.technical_structure_id
                or structure.technical_structure_id != target.technical_structure_id):
            raise ValueError("Unexpected resolution target")
        if [i.issue_id for i in proposal.issue_resolutions] != [i.issue_id for i in target.issues]:
            raise ValueError("Resolution outcomes must cover the exact ordered issues")
        candidates = set(target.identity.candidate_ids)
        for outcome in proposal.issue_resolutions:
            if not set(outcome.evidence_candidate_ids) <= candidates:
                raise ValueError("Foreign resolution evidence")
            if outcome.outcome != TechnicalIssueResolutionOutcome.UNRESOLVED and not outcome.evidence_candidate_ids:
                raise ValueError("CORRECTED and DISPUTED require evidence")
        if (structure.missing_fields != target.current_structure.missing_fields
                or structure.resolution_status != target.current_structure.resolution_status):
            raise ValueError("Resolution must preserve baseline bookkeeping for future apply")
        baseline = dict(_resolved_fields(target.current_structure))
        proposed = dict(_resolved_fields(structure))
        for path, field in proposed.items():
            if not set(field.evidence_candidate_ids) <= candidates:
                raise ValueError("Foreign proposed structure evidence")
            if field.resolution == FieldResolution.DEFAULTED:
                if path not in baseline or field.model_dump() != baseline[path].model_dump():
                    raise ValueError("Resolution must not introduce DEFAULTED")
        for path, field in baseline.items():
            if field.resolution == FieldResolution.DEFAULTED and not any(
                    path_is_within_allowed_root(path, root) for root in target.allowed_change_paths):
                if path not in proposed or proposed[path].model_dump() != field.model_dump():
                    raise ValueError("Existing defaults outside allowed paths must be preserved")
        return proposal
    except ValidationError as exc:
        raise InvalidTechnicalResolutionResponseError(
            "Invalid structured resolution response: " + safe_resolution_validation_summary(exc)) from None
    except ValueError:
        raise InvalidTechnicalResolutionResponseError("Invalid resolution target, outcomes, evidence, defaults or structure contract") from None


class TechnicalResolutionProposalService:
    def __init__(self, resolver_factory: Callable[[], TechnicalResolver] | None = None,
                 technical_root: Path = Path("runtime/technical"), identity_root: Path = Path("runtime/identity"),
                 discovery_root: Path = Path("runtime/discovery"), batch_root: Path = Path("runtime/review/batches"),
                 prepared_root: Path = Path("runtime/review/prepared")):
        self.resolver_factory = resolver_factory
        self.technical_root = Path(technical_root)
        self.evidence_roots = tuple(Path(p) for p in (identity_root, discovery_root, batch_root, prepared_root))

    def resolve(self, batch_id: str) -> TechnicalResolutionProposalResult | None:
        evidence = build_technical_resolution_input(batch_id, self.technical_root, *self.evidence_roots)
        if not evidence.resolution_required:
            return None
        if self.resolver_factory is None:
            from .openai_resolver import OpenAITechnicalResolver
            resolver = OpenAITechnicalResolver()
        else:
            resolver = self.resolver_factory()
        try:
            proposals = []
            for target in evidence.targets:
                raw = resolver.resolve(target.model_copy(deep=True))
                proposals.append(validate_resolution_proposal(target, raw))
            result = TechnicalResolutionProposalResult(batch_id=evidence.batch_id,
                source_technical_extraction_sha256=evidence.source_technical_extraction_sha256,
                source_technical_audit_sha256=evidence.source_technical_audit_sha256,
                source_technical_gate_sha256=evidence.source_technical_gate_sha256,
                targets_processed=len(proposals), proposals=proposals)
            directory = (self.technical_root.resolve() / result.batch_id).resolve()
            target = directory / "technical_resolution_proposals.json"
            if not directory.is_relative_to(self.technical_root.resolve()) or not target.resolve().is_relative_to(directory):
                raise ValueError("Resolution proposals must stay within their batch")
            with TemporaryDirectory(prefix=".staging-", dir=directory) as staging:
                temporary = Path(staging) / "technical_resolution_proposals.json"
                temporary.write_text(result.model_dump_json(indent=2), encoding="utf-8")
                temporary.replace(target)
            return result
        finally:
            resolver.close()

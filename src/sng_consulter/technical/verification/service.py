"""Verify every current structure independently and publish observations atomically."""

from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

from pydantic import ValidationError

from ..validation import resolve_field_path
from .inputs import build_technical_verification_input, InvalidTechnicalVerificationInputError
from .models import (TechnicalVerificationTarget, TechnicalStructureVerificationProposal,
                     TechnicalVerificationIssue, TechnicalVerificationResult)


class TechnicalVerificationConfigurationError(ValueError):
    pass


class TechnicalVerificationAPIError(RuntimeError):
    pass


class InvalidTechnicalVerificationResponseError(RuntimeError):
    pass


class TechnicalVerificationContextBudgetExceeded(ValueError):
    pass


class TechnicalVerifier(Protocol):
    def verify(self, target: TechnicalVerificationTarget) -> TechnicalStructureVerificationProposal: ...
    def close(self) -> None: ...


def validate_verification_proposal(target, raw) -> TechnicalStructureVerificationProposal:
    try:
        proposal = TechnicalStructureVerificationProposal.model_validate(raw)
        if proposal.identity_id != target.identity_id or proposal.technical_structure_id != target.technical_structure_id:
            raise ValueError("Unexpected Verification target")
        for issue in proposal.issues:
            if not set(issue.candidate_ids) <= set(target.identity.candidate_ids):
                raise ValueError("Verification evidence must belong to target Identity")
            for path in issue.field_paths:
                resolve_field_path(target.current_structure, path)
        return proposal
    except (ValidationError, ValueError):
        raise InvalidTechnicalVerificationResponseError("Invalid Verification target, paths, evidence or issue contract") from None


class TechnicalVerificationService:
    def __init__(self, verifier_factory: Callable[[], TechnicalVerifier] | None = None,
                 technical_root: Path = Path("runtime/technical"), identity_root: Path = Path("runtime/identity"),
                 discovery_root: Path = Path("runtime/discovery"), batch_root: Path = Path("runtime/review/batches"),
                 prepared_root: Path = Path("runtime/review/prepared")):
        self.verifier_factory = verifier_factory
        self.technical_root = Path(technical_root)
        self.evidence_roots = tuple(Path(p) for p in (identity_root, discovery_root, batch_root, prepared_root))

    def verify(self, batch_id: str) -> TechnicalVerificationResult:
        evidence = build_technical_verification_input(batch_id, self.technical_root, *self.evidence_roots)
        if self.verifier_factory is None:
            from .openai_verifier import OpenAITechnicalVerifier
            verifier = OpenAITechnicalVerifier()
        else:
            verifier = self.verifier_factory()
        try:
            proposals = []
            for target in evidence.targets:
                raw = verifier.verify(target.model_copy(deep=True))
                proposals.append(validate_verification_proposal(target, raw))
            issues = []
            for proposal in proposals:
                for issue in proposal.issues:
                    issues.append(TechnicalVerificationIssue(**issue.model_dump(),
                        issue_id=f"technical-verification-{len(issues) + 1:04d}", identity_id=proposal.identity_id,
                        technical_structure_id=proposal.technical_structure_id))
            result = TechnicalVerificationResult(batch_id=evidence.batch_id,
                verified_technical_resolution_sha256=evidence.source_technical_resolution_sha256,
                structures_verified=len(evidence.targets), issue_count=len(issues), issues=issues)
            root = self.technical_root.resolve()
            directory = (root / result.batch_id).resolve()
            target = directory / "technical_verification.json"
            if not directory.is_relative_to(root) or not target.resolve().is_relative_to(directory):
                raise InvalidTechnicalVerificationInputError("Verification output must stay within its batch")
            with TemporaryDirectory(prefix=".staging-", dir=directory) as staging:
                temporary = Path(staging) / "technical_verification.json"
                temporary.write_text(result.model_dump_json(indent=2), encoding="utf-8")
                temporary.replace(target)
            return result
        finally:
            verifier.close()

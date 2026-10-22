"""Validate proposals, assign IDs, derive completeness and publish only on success."""

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

from pydantic import BaseModel

from .models import (
    DecorativeElement, FieldResolution, GlassPane, Module, ResolvedField, TechnicalExtractionInput,
    TechnicalExtractionProposal, TechnicalExtractionResult, TechnicalResolutionStatus, TechnicalStructure,
)


class TechnicalExtractor(Protocol):
    def extract(self, technical_input: TechnicalExtractionInput) -> TechnicalExtractionProposal: ...


class InvalidTechnicalProposalError(ValueError):
    pass


def _resolved_fields(value, path=""):
    if isinstance(value, ResolvedField):
        yield path,value
    elif isinstance(value, BaseModel):
        for name in type(value).model_fields:
            yield from _resolved_fields(getattr(value,name),f"{path}.{name}" if path else name)
    elif isinstance(value,list):
        for index,item in enumerate(value):
            yield from _resolved_fields(item,f"{path}[{index}]")


def technical_bookkeeping(structure):
    """Canonical non-destructive completeness view, shared by extraction and apply."""
    fields = list(_resolved_fields(structure))
    missing = [path for path, field in fields if field.resolution == FieldResolution.UNKNOWN]
    status = (TechnicalResolutionStatus.AMBIGUOUS if any(f.resolution == FieldResolution.CONFLICT for _, f in fields)
              else TechnicalResolutionStatus.PARTIAL if missing else TechnicalResolutionStatus.RESOLVED)
    return missing, status


class TechnicalExtractionService:
    def __init__(self, extractor: TechnicalExtractor, output_root: Path = Path("runtime/technical")) -> None:
        self.extractor = extractor
        self.output_root = Path(output_root)

    def extract(self, technical_input: TechnicalExtractionInput) -> TechnicalExtractionResult:
        evidence = TechnicalExtractionInput.model_validate(technical_input)
        targets = {g.identity_id:g for g in evidence.identities}
        proposal = TechnicalExtractionProposal.model_validate(self.extractor.extract(evidence.model_copy(deep=True)))
        proposed = {s.identity_id:s for s in proposal.structures}
        if len(proposed) != len(proposal.structures) or set(proposed) != set(targets):
            raise InvalidTechnicalProposalError("Exactly one structure is required per resolved Identity, without unknown or duplicate targets")
        structures = []
        module_count = pane_count = decorative_count = 0
        for target in evidence.identities:
            structure = proposed[target.identity_id]
            fields = list(_resolved_fields(structure))
            for _,field in fields:
                if not set(field.evidence_candidate_ids) <= set(target.candidate_ids):
                    raise InvalidTechnicalProposalError("Structure evidence must belong to its own Identity")
            missing, status = technical_bookkeeping(structure)
            modules = []
            for module in structure.modules:
                panes = []
                for pane in module.glass_panes:
                    pane_count += 1
                    panes.append(GlassPane(**pane.model_dump(),glass_pane_id=f"glass-pane-{pane_count:04d}"))
                decorations = []
                for decoration in module.decorative_elements:
                    decorative_count += 1
                    decorations.append(DecorativeElement(**decoration.model_dump(),decorative_element_id=f"decorative-{decorative_count:04d}"))
                module_count += 1
                modules.append(Module(**module.model_dump(exclude={"glass_panes","decorative_elements"}),
                    module_id=f"module-{module_count:04d}",glass_panes=panes,decorative_elements=decorations))
            structures.append(TechnicalStructure(**structure.model_dump(exclude={"modules"}),
                technical_structure_id=f"technical-structure-{len(structures)+1:04d}",modules=modules,
                resolution_status=status,missing_fields=missing))
        result = TechnicalExtractionResult(batch_id=evidence.batch_id,source_identity_sha256=evidence.source_identity_sha256,
            source_identity_gate_sha256=evidence.source_identity_gate_sha256,structures=structures,
            unresolved_groups=evidence.unresolved_groups,resolved_identity_count=len(targets),
            unresolved_group_count=len(evidence.unresolved_groups))
        root = self.output_root.resolve()
        directory = (root / result.batch_id).resolve()
        if not directory.is_relative_to(root):
            raise ValueError("Technical output must stay within its root")
        directory.mkdir(parents=True,exist_ok=True)
        target = directory / "technical_extraction.json"
        if not target.resolve().is_relative_to(directory):
            raise ValueError("Technical output must stay within its batch")
        with TemporaryDirectory(prefix=".staging-",dir=directory) as staging:
            temporary = Path(staging) / "technical_extraction.json"
            temporary.write_text(result.model_dump_json(indent=2),encoding="utf-8")
            temporary.replace(target)
        return result

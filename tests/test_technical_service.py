from pathlib import Path

import pytest
from pydantic import ValidationError

from test_technical_inputs import technical_case, identity_review, review, snapshot
from test_technical_models import structure, field, known
from sng_consulter.technical import TechnicalExtractionService, InvalidTechnicalProposalError
from sng_consulter.technical.models import TechnicalExtractionResult


class FakeTechnicalExtractor:
    def __init__(self,proposals=None,fail=False,mutate=False):
        self.proposals = proposals
        self.fail = fail
        self.mutate = mutate

    def extract(self,evidence):
        if self.fail: raise RuntimeError("Intentional extractor failure")
        proposals = self.proposals if self.proposals is not None else [structure(g.identity_id,g.candidate_ids[0]) for g in evidence.identities]
        if self.mutate:
            evidence.batch_id = "f" * 32
            evidence.identities.clear()
            evidence.unresolved_groups.clear()
        return dict(structures=proposals)


def test_exact_targets_deterministic_ids_nested_domain_separation_and_immutable_sources(technical_case,tmp_path):
    evidence,_,_,_,_,root = technical_case
    before = snapshot(root)
    original_input = evidence.model_dump()
    proposals = [structure(g.identity_id,g.candidate_ids[0],panes=3,decorations=2) for g in evidence.identities]
    service = TechnicalExtractionService(FakeTechnicalExtractor(list(reversed(proposals)),mutate=True),tmp_path / "technical")
    result = service.extract(evidence)
    assert result.resolved_identity_count == 2
    assert result.unresolved_group_count == 1
    assert result.unresolved_groups == evidence.unresolved_groups
    assert [s.identity_id for s in result.structures] == [g.identity_id for g in evidence.identities]
    assert [s.technical_structure_id for s in result.structures] == ["technical-structure-0001","technical-structure-0002"]
    assert [s.modules[0].module_id for s in result.structures] == ["module-0001","module-0002"]
    assert [p.glass_pane_id for s in result.structures for m in s.modules for p in m.glass_panes] == [f"glass-pane-{i:04d}" for i in range(1,7)]
    assert [d.decorative_element_id for s in result.structures for m in s.modules for d in m.decorative_elements] == [f"decorative-{i:04d}" for i in range(1,5)]
    assert all(len(s.modules) == 1 and s.modules[0].glass_pane_count.value == 3 for s in result.structures)
    assert result.structures[0].quantity.value is None  # Two views never become quantity=2, nor default=1.
    assert result.structures[0].missing_fields == ["quantity"]
    assert all(s.resolution_status == "PARTIAL" for s in result.structures)
    assert evidence.model_dump() == original_input
    output = tmp_path / "technical" / evidence.batch_id / "technical_extraction.json"
    original = output.read_bytes()
    assert service.extract(evidence) == result
    assert output.read_bytes() == original
    assert TechnicalExtractionResult.model_validate_json(original) == result
    after = snapshot(root)
    assert all(after[path] == content for path,content in before.items())
    assert set(after)-set(before) == {output.relative_to(root)}


@pytest.mark.parametrize("mode",["RESOLVED","PARTIAL","AMBIGUOUS","DEFAULTED"])
def test_recursive_status_and_missing_fields(technical_case,tmp_path,mode):
    evidence = technical_case[0]
    proposals = [structure(g.identity_id,g.candidate_ids[0],panes=2,decorations=1) for g in evidence.identities]
    for s,g in zip(proposals,evidence.identities): s["quantity"] = known(2,g.candidate_ids[0])
    s = proposals[0]
    if mode == "DEFAULTED": s["quantity"] = field(1,"DEFAULTED",note="Approved default quantity=1")
    elif mode in {"PARTIAL","AMBIGUOUS"}:
        s["reference"] = field()
        s["dimensions"]["width_mm"] = field()
        s["modules"][0]["function"] = field()
        s["modules"][0]["glass_panes"][1]["dimensions"]["height_mm"] = field()
        s["modules"][0]["decorative_elements"][0]["description"] = field()
        if mode == "AMBIGUOUS":
            s["modules"][0]["glass_panes"][0]["glass_specification"] = field(None,"CONFLICT",["covered-0001"],"Contradictory specifications")
    result = TechnicalExtractionService(FakeTechnicalExtractor(proposals),tmp_path / "technical").extract(evidence)
    assert result.structures[0].resolution_status == ("RESOLVED" if mode == "DEFAULTED" else mode)
    assert result.structures[0].missing_fields == (["reference","dimensions.width_mm","modules[0].function",
        "modules[0].glass_panes[1].dimensions.height_mm","modules[0].decorative_elements[0].description"] if mode in {"PARTIAL","AMBIGUOUS"} else [])


@pytest.mark.parametrize("invalid",["omitted","duplicate","unknown","other-identity","unknown-evidence","nested-evidence","assigned-id","assigned-status"])
def test_invalid_proposals_never_publish_or_destroy_previous(technical_case,tmp_path,invalid):
    evidence = technical_case[0]
    root = tmp_path / "technical"
    proposals = [structure(g.identity_id,g.candidate_ids[0]) for g in evidence.identities]
    if invalid == "omitted": proposals.pop()
    elif invalid == "duplicate": proposals.append(proposals[0])
    elif invalid == "unknown": proposals[0]["identity_id"] = "identity-9999"
    elif invalid == "other-identity": proposals[0]["name"] = known("Wrong identity evidence","covered-0004")
    elif invalid == "unknown-evidence": proposals[0]["name"] = known("Unknown evidence","covered-9999")
    elif invalid == "nested-evidence": proposals[0]["modules"][0]["glass_panes"][0]["dimensions"]["width_mm"] = known(400,"covered-0004")
    elif invalid == "assigned-id": proposals[0]["modules"][0]["module_id"] = "module-0001"
    else: proposals[0]["resolution_status"] = "RESOLVED"
    target = root / evidence.batch_id / "technical_extraction.json"
    for previous in [False,True]:
        if previous: TechnicalExtractionService(FakeTechnicalExtractor(),root).extract(evidence)
        before = target.read_bytes() if target.exists() else None
        with pytest.raises((InvalidTechnicalProposalError,ValidationError)):
            TechnicalExtractionService(FakeTechnicalExtractor(proposals),root).extract(evidence)
        assert (target.read_bytes() if target.exists() else None) == before
        assert not list(root.rglob(".staging-*"))


@pytest.mark.parametrize("previous",[False,True])
@pytest.mark.parametrize("failure",["extractor","write","publish"])
def test_failures_keep_all_previous_bytes_and_cleanup(technical_case,tmp_path,monkeypatch,previous,failure):
    evidence,_,_,_,_,root = technical_case
    output_root = tmp_path / "technical"
    service = TechnicalExtractionService(FakeTechnicalExtractor(),output_root)
    if previous: service.extract(evidence)
    before = snapshot(root),snapshot(output_root)
    if failure == "extractor": service.extractor = FakeTechnicalExtractor(fail=True)
    elif failure == "write":
        original = Path.write_text
        def broken(path,*args,**kwargs):
            original(path,"partial")
            raise OSError("Simulated write failure")
        monkeypatch.setattr(Path,"write_text",broken)
    else:
        def broken(*args,**kwargs): raise OSError("Simulated publication failure")
        monkeypatch.setattr(Path,"replace",broken)
    with pytest.raises((RuntimeError,OSError)): service.extract(evidence)
    assert (snapshot(root),snapshot(output_root)) == before
    assert not list(output_root.rglob(".staging-*"))


def test_no_targets_preserves_only_unresolved_without_inventing_structures(technical_case,tmp_path):
    evidence = technical_case[0].model_copy(deep=True)
    from sng_consulter.identity.models import UnresolvedIdentity
    for i,g in enumerate(evidence.identities,2):
        evidence.unresolved_groups.append(UnresolvedIdentity(unresolved_id=f"unresolved-{i:04d}",candidate_ids=g.candidate_ids,reason="Uncertain"))
    evidence.identities = []
    result = TechnicalExtractionService(FakeTechnicalExtractor(),tmp_path / "technical").extract(evidence)
    assert result.structures == [] and result.resolved_identity_count == 0
    assert result.unresolved_group_count == 3


@pytest.mark.parametrize("name",["scope","module_count"])
@pytest.mark.parametrize("resolution",["UNKNOWN","CONFLICT"])
def test_new_fields_participate_in_status_and_missing_paths(technical_case,tmp_path,name,resolution):
    evidence = technical_case[0]
    proposals = [structure(g.identity_id,g.candidate_ids[0]) for g in evidence.identities]
    for proposal,target in zip(proposals,evidence.identities): proposal["quantity"] = known(1,target.candidate_ids[0])
    proposals[0][name] = field(None,resolution,["covered-0001"] if resolution == "CONFLICT" else [],
                              "Contradictory observations" if resolution == "CONFLICT" else None)
    result = TechnicalExtractionService(FakeTechnicalExtractor(proposals),tmp_path / "technical").extract(evidence)
    assert result.structures[0].resolution_status == ("AMBIGUOUS" if resolution == "CONFLICT" else "PARTIAL")
    assert result.structures[0].missing_fields == ([name] if resolution == "UNKNOWN" else [])


@pytest.mark.parametrize("name,value",[("scope","NOT_GLASS_RELATED"),("module_count",1)])
def test_new_field_evidence_cannot_cite_another_identity(technical_case,tmp_path,name,value):
    evidence = technical_case[0]
    proposals = [structure(g.identity_id,g.candidate_ids[0]) for g in evidence.identities]
    proposals[0][name] = known(value,"covered-0004")
    with pytest.raises(InvalidTechnicalProposalError):
        TechnicalExtractionService(FakeTechnicalExtractor(proposals),tmp_path / "technical").extract(evidence)
    assert not list((tmp_path / "technical").rglob("technical_extraction.json"))


def test_not_glass_related_is_retained_and_views_do_not_change_counts(technical_case,tmp_path):
    evidence = technical_case[0]
    assert len(evidence.identities[0].candidate_ids) == 2
    proposals = [structure(g.identity_id,g.candidate_ids[0],panes=1,decorations=4) for g in evidence.identities]
    proposals[0]["scope"] = field("NOT_GLASS_RELATED","INFERRED",["covered-0001"],"Observable non-glass item")
    result = TechnicalExtractionService(FakeTechnicalExtractor(proposals),tmp_path / "technical").extract(evidence)
    assert len(result.structures) == 2
    target = result.structures[0]
    assert target.scope.value == "NOT_GLASS_RELATED"
    assert target.quantity.value is None
    assert target.module_count.value == len(target.modules) == 1
    assert target.modules[0].glass_pane_count.value == len(target.modules[0].glass_panes) == 1
    assert len(target.modules[0].decorative_elements) == 4
    assert [s.technical_structure_id for s in result.structures] == ["technical-structure-0001","technical-structure-0002"]

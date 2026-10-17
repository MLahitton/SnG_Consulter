import pytest
from pydantic import ValidationError

from test_technical_service import FakeTechnicalExtractor
from test_technical_inputs import technical_case, identity_review, review
from sng_consulter.technical.service import TechnicalExtractionService
from sng_consulter.technical.resolution.models import (
    TechnicalIssueResolutionOutcome, TechnicalIssueResolutionProposal, TechnicalStructureResolutionProposal,
)
from sng_consulter.technical.resolution.validation import (
    path_is_within_allowed_root, normalize_allowed_change_paths,
)


def issue_proposal():
    return dict(issue_id='technical-audit-0001', outcome='CORRECTED',
        evidence_candidate_ids=['covered-0001'], reason='Supported documentary evidence')


def test_exact_outcomes():
    assert {o.value for o in TechnicalIssueResolutionOutcome} == {'CORRECTED','DISPUTED','UNRESOLVED'}


@pytest.mark.parametrize('outcome',list(TechnicalIssueResolutionOutcome))
def test_valid_future_structure_proposal_does_not_apply_or_persist(technical_case,tmp_path,outcome):
    evidence=technical_case[0]
    current=TechnicalExtractionService(FakeTechnicalExtractor(),tmp_path/'technical').extract(evidence).structures[0]
    data=issue_proposal()
    data['outcome']=outcome
    proposal=TechnicalStructureResolutionProposal(identity_id=current.identity_id,
        technical_structure_id=current.technical_structure_id, issue_resolutions=[data], proposed_structure=current)
    assert proposal.proposed_structure == current
    assert proposal.issue_resolutions[0].outcome == outcome
    assert not list(tmp_path.rglob('technical_resolution.json'))


@pytest.mark.parametrize('change',[
    dict(reason=''),dict(reason=' '),dict(issue_id=''),dict(issue_id='arbitrary-id'),
    dict(evidence_candidate_ids=['covered-0001','covered-0001']),dict(confidence=.5),
    dict(corrected_value=10),dict(price=5),dict(system='Product'),
])
def test_invalid_future_issue_contract(change):
    with pytest.raises(ValidationError): TechnicalIssueResolutionProposal(**(issue_proposal()|change))


def test_structure_extra_fields_forbidden(technical_case,tmp_path):
    current=TechnicalExtractionService(FakeTechnicalExtractor(),tmp_path/'technical').extract(technical_case[0]).structures[0]
    valid=TechnicalStructureResolutionProposal(identity_id=current.identity_id,
        technical_structure_id=current.technical_structure_id,issue_resolutions=[issue_proposal()],proposed_structure=current)
    with pytest.raises(ValidationError):
        TechnicalStructureResolutionProposal.model_validate(valid.model_dump()|dict(confidence=.8))


@pytest.mark.parametrize('path,root,expected',[
    ('modules','modules',True),('modules[0]','modules',True),
    ('modules[0].function','modules',True),('modules[0].glass_panes[0].dimensions.width_mm','modules[0].glass_panes',True),
    ('modules[1]','modules[10]',False),('modules[10]','modules[1]',False),
    ('modules[10].function','modules[1]',False),('modules[1].function','modules[1]',True),
    ('modules[0].function','modules[0].dimensions.width_mm',False),
    ('dimensions.width_mm','modules[0].dimensions.width_mm',False),('quantity','modules',False),
    ('modules[1].glass_panes','modules[0].glass_panes',False),
    ('quantity.value','quantity',True),('quantity_extra','quantity',False),
    ('modules[01].function','modules[1]',True),
])
def test_structural_path_containment(path,root,expected):
    assert path_is_within_allowed_root(path,root) is expected


@pytest.mark.parametrize('paths,expected',[
    (['quantity','quantity'],['quantity']),
    (['modules','modules[0].function','modules[0].glass_panes'],['modules']),
    (['modules[0].function','quantity','modules'],['quantity','modules']),
    (['modules[0].dimensions.width_mm','modules[3].dimensions.width_mm'],
     ['modules[0].dimensions.width_mm','modules[3].dimensions.width_mm']),
    (['modules[1].function','modules[10].function'],['modules[1].function','modules[10].function']),
    (['modules[0].glass_panes','modules[0].glass_panes[0].glass_specification','modules[1].function'],
     ['modules[0].glass_panes','modules[1].function']),
    (['modules[01].function','modules[1].function'],['modules[1].function']),
])
def test_allowed_root_normalization(paths,expected):
    assert normalize_allowed_change_paths(paths) == expected


@pytest.mark.parametrize('path',['','modules[-1]','modules[0].__dict__','modules..function',
    'modules[0];print(1)',"modules['0']",'modules[0].function()', ' modules'])
def test_unsafe_paths_rejected(path):
    with pytest.raises(ValueError): path_is_within_allowed_root(path,'modules')
    with pytest.raises(ValueError): path_is_within_allowed_root('modules',path)
    with pytest.raises(ValueError): normalize_allowed_change_paths([path])

import pytest
from pydantic import ValidationError

from sng_consulter.technical.audit.models import TechnicalAuditIssueType
from sng_consulter.technical.verification.models import (
    TechnicalVerificationIssueProposal, TechnicalStructureVerificationProposal,
    TechnicalVerificationResult,
)


def issue_for(target=None, **updates):
    return dict(issue_type='MISSING_SUPPORTED_VALUE', field_paths=['quantity'],
        candidate_ids=[target.identity.candidate_ids[0] if target else 'covered-0001'],
        reason='Concrete synthetic documentary observation') | updates


def proposal_for(target, issues=None):
    return dict(identity_id=target.identity_id, technical_structure_id=target.technical_structure_id,
                issues=[] if issues is None else issues)


def result_issue(number=1, **updates):
    return issue_for() | dict(issue_id=f'technical-verification-{number:04d}',
        identity_id='identity-0001', technical_structure_id='technical-structure-0001') | updates


def test_taxonomy_is_exact_existing_enum_and_empty_issues_are_valid():
    annotation = TechnicalVerificationIssueProposal.model_fields['issue_type'].annotation
    assert annotation is TechnicalAuditIssueType and len(annotation) == 11
    assert TechnicalStructureVerificationProposal(identity_id='identity-0001',
        technical_structure_id='technical-structure-0001', issues=[]).issues == []


@pytest.mark.parametrize('kind', list(TechnicalAuditIssueType))
def test_all_existing_issue_types_are_supported(kind):
    assert TechnicalVerificationIssueProposal(**issue_for(issue_type=kind)).issue_type == kind


@pytest.mark.parametrize('updates', [
    dict(reason=''), dict(reason='   '), dict(field_paths=[]), dict(field_paths=['']),
    dict(field_paths=['quantity', 'quantity']), dict(candidate_ids=[]),
    dict(candidate_ids=['covered-0001', 'covered-0001']), dict(issue_type='INVENTED'),
    dict(confidence=0.5), dict(corrected_value=2), dict(recommendation='Apply'), dict(price=1),
    dict(system='Commercial selection'),
])
def test_invalid_issue_contract(updates):
    with pytest.raises(ValidationError): TechnicalVerificationIssueProposal(**issue_for(**updates))


@pytest.mark.parametrize('changes', [
    dict(issue_count=0), dict(structures_verified=0), dict(issues=[result_issue(2)]),
    dict(issues=[result_issue(), result_issue()], issue_count=2), dict(confidence=0.9),
    dict(issues=[result_issue(issue_id='technical-audit-0001')]),
])
def test_result_count_sequence_and_extras(changes):
    data = dict(batch_id='a' * 32, verified_technical_resolution_sha256='f' * 64,
                structures_verified=1, issue_count=1, issues=[result_issue()]) | changes
    with pytest.raises(ValidationError): TechnicalVerificationResult(**data)


@pytest.mark.parametrize('extra', ['issue_id', 'current_structure', 'outcome', 'corrected_value'])
def test_provider_cannot_return_history_ids_or_corrections(extra):
    with pytest.raises(ValidationError):
        TechnicalStructureVerificationProposal(identity_id='identity-0001',
            technical_structure_id='technical-structure-0001', issues=[], **{extra: 'forbidden'})


def test_large_global_issue_sequence():
    result = TechnicalVerificationResult(batch_id='a' * 32, verified_technical_resolution_sha256='f' * 64,
        structures_verified=1, issue_count=2, issues=[result_issue(1), result_issue(2)])
    assert [i.issue_id for i in result.issues] == ['technical-verification-0001', 'technical-verification-0002']

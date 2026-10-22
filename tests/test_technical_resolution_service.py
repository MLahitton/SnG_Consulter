import json
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import ValidationError

from test_technical_resolution_inputs import gate_case, audit_case, technical_case, identity_review, review, prepare, build
from test_technical_inputs import snapshot
from sng_consulter.technical.resolution import (
    TechnicalResolutionProposalService, TechnicalResolutionProposalResult, InvalidTechnicalResolutionInputError,
    InvalidTechnicalResolutionResponseError, TechnicalResolutionAPIError, TechnicalResolutionConfigurationError,
)


def proposal_for(target, outcome='UNRESOLVED'):
    return dict(identity_id=target.identity_id, technical_structure_id=target.technical_structure_id,
        issue_resolutions=[dict(issue_id=i.issue_id,outcome=outcome,
            evidence_candidate_ids=[] if outcome=='UNRESOLVED' else [target.identity.candidate_ids[0]],
            reason='Synthetic documentary assessment') for i in target.issues],
        proposed_structure=target.current_structure.model_dump(mode='json'))


class Resolver:
    def __init__(self):
        self.calls=[]
        self.closed=False
        self.payloads={}
        self.fail_on=None
    def resolve(self,target):
        self.calls.append(target)
        if len(self.calls)==self.fail_on: raise TechnicalResolutionAPIError('Synthetic provider failure')
        return self.payloads.get(target.identity_id,proposal_for(target))
    def close(self): self.closed=True


@pytest.fixture
def resolution_case(gate_case):
    prepare(gate_case,(0,0,1))
    evidence=build(gate_case)
    resolver=Resolver()
    factory_calls=[]
    def factory():
        factory_calls.append(True)
        resolver.closed=False
        return resolver
    gate=gate_case[0]
    service=TechnicalResolutionProposalService(factory,gate.technical_root,*gate.evidence_roots)
    return service,resolver,evidence,gate_case,factory_calls


@pytest.mark.parametrize('targets',[(),(0,),(0,0),(0,0,1)])
def test_ordered_targets_proposals_exact_hashes_and_immutable_sources(resolution_case,targets,monkeypatch):
    service,resolver,_,case,factory_calls=resolution_case
    prepare(case,targets)
    evidence=build(case)
    before=snapshot(case[-1])
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    result=service.resolve(evidence.batch_id)
    if not targets:
        assert result is None and not resolver.calls and not factory_calls
        assert snapshot(case[-1])==before
        return
    assert result.targets_processed==len(evidence.targets)
    assert resolver.closed and len(factory_calls)==1
    assert [t.identity_id for t in resolver.calls]==[t.identity_id for t in evidence.targets]
    assert len(resolver.calls[0].issues)==targets.count(targets[0])
    assert [p.identity_id for p in result.proposals]==[t.identity_id for t in evidence.targets]
    for field in ['source_technical_extraction_sha256','source_technical_audit_sha256','source_technical_gate_sha256']:
        assert getattr(result,field)==getattr(evidence,field)
    output=case[1].with_name('technical_resolution_proposals.json')
    assert TechnicalResolutionProposalResult.model_validate_json(output.read_bytes())==result
    after=snapshot(case[-1])
    assert all(after[p]==data for p,data in before.items())
    assert set(after)-set(before)=={output.relative_to(case[-1])}
    assert not list(case[-1].rglob('technical_resolution.json'))


def test_ready_preserves_existing_proposals_and_never_builds_resolver(resolution_case,monkeypatch):
    service,_,evidence,case,_=resolution_case
    service.resolve(evidence.batch_id)
    prepare(case,())
    def forbidden(): pytest.fail('READY must not instantiate resolver')
    service.resolver_factory=forbidden
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    before=snapshot(case[-1])
    assert service.resolve(evidence.batch_id) is None
    assert snapshot(case[-1])==before


@pytest.mark.parametrize('failure',[
    'identity','structure','proposed-identity','proposed-structure','missing-issue','extra-issue','duplicate-issue',
    'reordered-issues','foreign-evidence','empty-corrected','empty-disputed','malformed','invalid-structure',
    'new-default','bookkeeping-missing','bookkeeping-status','foreign-structure-evidence',
])
def test_invalid_proposals_fail_without_publication_and_close(resolution_case,failure):
    service,resolver,evidence,case,_=resolution_case
    target=evidence.targets[0]
    data=proposal_for(target)
    if failure=='identity': data['identity_id']='identity-9999'
    elif failure=='structure': data['technical_structure_id']='technical-structure-9999'
    elif failure=='proposed-identity': data['proposed_structure']['identity_id']='identity-9999'
    elif failure=='proposed-structure': data['proposed_structure']['technical_structure_id']='technical-structure-9999'
    elif failure=='missing-issue': data['issue_resolutions'].pop()
    elif failure=='extra-issue': data['issue_resolutions'].append(data['issue_resolutions'][0]|dict(issue_id='technical-audit-9999'))
    elif failure=='duplicate-issue': data['issue_resolutions'][1]=data['issue_resolutions'][0].copy()
    elif failure=='reordered-issues': data['issue_resolutions'].reverse()
    elif failure=='foreign-evidence': data['issue_resolutions'][0]['evidence_candidate_ids']=evidence.targets[1].identity.candidate_ids
    elif failure in {'empty-corrected','empty-disputed'}: data['issue_resolutions'][0]['outcome']=failure.split('-')[1].upper()
    elif failure=='malformed': data={'bad':'output'}
    elif failure=='invalid-structure': data['proposed_structure']['module_count']['value']=9
    elif failure=='new-default': data['proposed_structure']['quantity']=dict(value=1,resolution='DEFAULTED',evidence_candidate_ids=[],note='Invented provider default')
    elif failure=='bookkeeping-missing': data['proposed_structure']['missing_fields']=[]
    elif failure=='bookkeeping-status': data['proposed_structure']['resolution_status']='RESOLVED'
    elif failure=='foreign-structure-evidence': data['proposed_structure']['scope']['evidence_candidate_ids']=evidence.targets[1].identity.candidate_ids
    resolver.payloads[target.identity_id]=data
    before=snapshot(case[-1])
    with pytest.raises(InvalidTechnicalResolutionResponseError): service.resolve(evidence.batch_id)
    assert resolver.closed and snapshot(case[-1])==before


@pytest.mark.parametrize('outcome',['CORRECTED','DISPUTED','UNRESOLVED'])
def test_valid_outcomes_preserve_baseline_bookkeeping(resolution_case,outcome):
    service,resolver,evidence,_,_=resolution_case
    target=evidence.targets[0]
    data=proposal_for(target,outcome)
    if outcome=='CORRECTED':
        data['proposed_structure']['quantity']=dict(value=2,resolution='EXPLICIT',
            evidence_candidate_ids=[target.identity.candidate_ids[0]],note='Synthetic explicit quantity')
    resolver.payloads[target.identity_id]=data
    result=service.resolve(evidence.batch_id)
    assert result.proposals[0].issue_resolutions[0].outcome==outcome
    assert result.proposals[0].proposed_structure.missing_fields==target.current_structure.missing_fields
    assert result.proposals[0].proposed_structure.resolution_status==target.current_structure.resolution_status


@pytest.mark.parametrize('previous',[False,True])
@pytest.mark.parametrize('failure',['request','write','replace'])
def test_atomic_failure_no_partial_and_prior_proposals_survive(resolution_case,monkeypatch,previous,failure):
    service,resolver,evidence,case,_=resolution_case
    if previous: service.resolve(evidence.batch_id)
    before=snapshot(case[-1])
    if failure=='request': resolver.fail_on=len(resolver.calls)+2
    else:
        original=Path.write_text if failure=='write' else Path.replace
        def fail(path,*args,**kwargs):
            if '.staging-' in str(path):
                if failure=='write': path.write_bytes(b'partial')
                raise OSError('Synthetic persistence failure')
            return original(path,*args,**kwargs)
        monkeypatch.setattr(Path,'write_text' if failure=='write' else 'replace',fail)
    with pytest.raises((TechnicalResolutionAPIError,OSError)): service.resolve(evidence.batch_id)
    assert resolver.closed and snapshot(case[-1])==before
    assert not list(case[1].parent.glob('.staging-*'))


def test_invalid_source_fails_before_factory(resolution_case):
    service,resolver,evidence,case,calls=resolution_case
    case[1].write_bytes(case[1].read_bytes()+b'\n')
    with pytest.raises(InvalidTechnicalResolutionInputError): service.resolve(evidence.batch_id)
    assert not calls and not resolver.calls


@pytest.mark.parametrize('change_default',[False,True])
def test_existing_default_outside_allowed_roots_preserved(resolution_case,change_default):
    service,resolver,_,case,_=resolution_case
    source=case[1]
    extraction=json.loads(source.read_bytes())
    extraction['structures'][0]['location']=dict(value='Existing baseline rule',resolution='DEFAULTED',
        evidence_candidate_ids=[],note='Previously approved deterministic rule')
    source.write_text(json.dumps(extraction),encoding='utf-8')
    audit=json.loads(case[2].read_bytes())
    audit['audited_technical_extraction_sha256']=sha256(source.read_bytes()).hexdigest()
    case[2].write_text(json.dumps(audit),encoding='utf-8')
    case[0].gate(case[4].batch_id)
    evidence=build(case)
    data=proposal_for(evidence.targets[0])
    if change_default: data['proposed_structure']['location']['value']='Changed rule'
    resolver.payloads[evidence.targets[0].identity_id]=data
    if change_default:
        with pytest.raises(InvalidTechnicalResolutionResponseError): service.resolve(evidence.batch_id)
    else:
        assert service.resolve(evidence.batch_id).proposals[0].proposed_structure.location.resolution=='DEFAULTED'


def test_result_count_contract():
    with pytest.raises(ValidationError):
        TechnicalResolutionProposalResult(batch_id='a'*32,source_technical_extraction_sha256='a'*64,
            source_technical_audit_sha256='a'*64,source_technical_gate_sha256='a'*64,targets_processed=1,proposals=[])


@pytest.mark.parametrize('state',['ready','blocked','invalid','api','configuration'])
def test_cli_resolve_does_not_execute_previous_phases(resolution_case,monkeypatch,capsys,state):
    from sng_consulter.technical import __main__ as command
    from sng_consulter.technical.resolution import service as service_module
    from sng_consulter.technical.resolution.openai_resolver import OpenAITechnicalResolver
    from sng_consulter.technical.service import TechnicalExtractionService
    from sng_consulter.technical.audit.service import TechnicalAuditService
    from sng_consulter.technical.gate import TechnicalGateService
    from sng_consulter.identity.service import IdentityResolutionService
    from sng_consulter.identity.audit.service import IdentityAuditService
    from sng_consulter.identity.gate import IdentityGateService
    from sng_consulter.discovery.service import DiscoveryService
    service,resolver,evidence,case,calls=resolution_case
    if state=='ready': prepare(case,())
    elif state=='invalid': case[1].unlink()
    elif state=='api': resolver.fail_on=2
    elif state=='configuration':
        def config_error(): raise TechnicalResolutionConfigurationError('Missing test configuration')
        service.resolver_factory=config_error
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    def forbidden(*a,**kw): pytest.fail('resolve must not instantiate other clients or execute earlier phases')
    from sng_consulter.technical import openai_extractor
    monkeypatch.setattr(openai_extractor,'OpenAITechnicalExtractor',forbidden)
    monkeypatch.setattr(OpenAITechnicalResolver,'__init__',forbidden)
    for cls,method in [(TechnicalExtractionService,'extract'),(TechnicalAuditService,'audit'),(TechnicalGateService,'gate'),
        (IdentityResolutionService,'resolve'),(IdentityAuditService,'audit'),(IdentityGateService,'gate'),(DiscoveryService,'discover')]:
        monkeypatch.setattr(cls,method,forbidden)
    monkeypatch.setattr(service_module,'TechnicalResolutionProposalService',lambda:service)
    assert command.main(['resolve',evidence.batch_id])==(0 if state in {'ready','blocked'} else 1)
    output=capsys.readouterr()
    if state=='ready': assert 'not required' in output.out and '0 targets' in output.out and not calls
    elif state=='blocked': assert 'proposals completed' in output.out and resolver.closed
    else: assert output.err

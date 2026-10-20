import base64
import json
from types import SimpleNamespace

import httpx2
import pytest
from openai import OpenAI

from test_technical_resolution_service import (
    resolution_case, gate_case, audit_case, technical_case, identity_review, review, proposal_for, prepare,
)
from test_technical_inputs import snapshot
from test_openai_analyzer import structured_response, TEST_KEY
from sng_consulter.technical.resolution import openai_resolver as module
from sng_consulter.technical.resolution.service import (
    TechnicalResolutionConfigurationError, TechnicalResolutionAPIError,
    InvalidTechnicalResolutionResponseError, TechnicalResolutionContextBudgetExceeded,
)
from sng_consulter.technical.visuals import build_technical_images


@pytest.fixture
def sdk(resolution_case,monkeypatch):
    evidence=resolution_case[2]
    monkeypatch.setenv('OPENAI_API_KEY',TEST_KEY)
    monkeypatch.delenv('SNG_TECHNICAL_RESOLUTION_MODEL',raising=False)
    state=SimpleNamespace(requests=[],constructors=[],payloads={t.identity_id:proposal_for(t) for t in evidence.targets},
        error_on=None,status='completed',refusal=False,malformed=False,connection=False)
    def handle(request):
        data=json.loads(request.content)
        state.requests.append(data)
        if state.connection: raise httpx2.ConnectError(TEST_KEY,request=request)
        if state.error_on==len(state.requests):
            return httpx2.Response(401,json=dict(error=dict(message=TEST_KEY,type='invalid_api_key')))
        iid=json.loads(data['input'][0]['content'][0]['text'])['identity_id']
        body=structured_response(state.payloads[iid],status=state.status,refusal=state.refusal)
        if state.malformed: body['output'][0]['content'][0]['text']='{invalid'
        return httpx2.Response(200,json=body)
    state.client=OpenAI(api_key=TEST_KEY,max_retries=0,base_url='https://api.openai.com/v1',
        http_client=httpx2.Client(transport=httpx2.MockTransport(handle)))
    def factory(**kwargs):
        state.constructors.append(kwargs)
        return state.client
    monkeypatch.setattr(module,'OpenAI',factory)
    yield state
    if not state.client.is_closed(): state.client.close()


def test_strict_isolated_ordered_requests_all_evidence_no_paths_or_image_writes(resolution_case,sdk,monkeypatch):
    service,_,evidence,case,_=resolution_case
    service.resolver_factory=None
    expected_calls=[]
    original=module.build_technical_images
    def images(identity):
        expected_calls.append(identity.identity_id)
        return original(identity)
    monkeypatch.setattr(module,'build_technical_images',images)
    before=snapshot(case[-1])
    result=service.resolve(evidence.batch_id)
    assert result.targets_processed==2 and sdk.client.is_closed()
    assert len(sdk.requests)==len(evidence.targets)==2
    assert expected_calls==[t.identity_id for t in evidence.targets]
    for target,request in zip(evidence.targets,sdk.requests):
        assert request['store'] is False
        assert request['text']['format']['strict'] is True
        assert request['text']['format']['name']=='TechnicalStructureResolutionProposal'
        assert request['instructions']==module.TECHNICAL_RESOLUTION_INSTRUCTIONS
        content=request['input'][0]['content']
        metadata=json.loads(content[0]['text'])
        assert metadata['identity_id']==target.identity_id
        assert metadata['technical_structure_id']==target.technical_structure_id
        assert metadata['current_structure']==target.current_structure.model_dump(mode='json')
        assert metadata['issues']==[i.model_dump(mode='json') for i in target.issues]
        assert metadata['allowed_change_paths']==target.allowed_change_paths
        assert [c['covered_candidate_id'] for c in metadata['candidates']]==target.identity.candidate_ids
        raw=json.dumps(content)
        assert all(x not in raw for x in [str(case[-1]),'image_path','filename','original_name',TEST_KEY])
        assert all(t.identity_id not in raw for t in evidence.targets if t.identity_id!=target.identity_id)
        actual=[i for i in content if i['type']=='input_image']
        expected=build_technical_images(target.identity)
        assert len(actual)==len(expected)
        for item,image in zip(actual,expected):
            assert item['detail']=='high'
            assert base64.b64decode(item['image_url'].split(',',1)[1])==image.data
        headers=[json.loads(i['text']) for i in content[1:] if i['type']=='input_text']
        assert [h['kind'] for h in headers]==['candidate_crop']*len(target.identity.candidates)+[
            'identity_page_context','associated_annotation_context','full_page','full_native_spans']*len(target.identity.pages)
        for actual,page in zip([h for h in headers if h['kind']=='full_native_spans'],target.identity.pages):
            assert actual['native_text_spans']==[[s.text,[s.bbox.x0,s.bbox.y0,s.bbox.x1,s.bbox.y1]] for s in page.native_text_spans]
    after=snapshot(case[-1])
    assert all(after[p]==data for p,data in before.items())
    assert set(after)-set(before)=={case[1].with_name('technical_resolution_proposals.json').relative_to(case[-1])}


def test_identical_target_constructs_identical_request(resolution_case,sdk):
    resolver=module.OpenAITechnicalResolver()
    target=resolution_case[2].targets[0]
    resolver.resolve(target)
    resolver.resolve(target)
    assert sdk.requests[0]==sdk.requests[1]


@pytest.mark.parametrize('key',[None,'',' '])
def test_key_required_on_instantiation(sdk,monkeypatch,key):
    if key is None: monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    else: monkeypatch.setenv('OPENAI_API_KEY',key)
    with pytest.raises(TechnicalResolutionConfigurationError): module.OpenAITechnicalResolver()
    assert not sdk.constructors and not sdk.requests


def test_independent_model_and_transport_configuration(resolution_case,sdk,monkeypatch):
    for variable in ['SNG_TECHNICAL_MODEL','SNG_TECHNICAL_AUDIT_MODEL','SNG_IDENTITY_MODEL']:
        monkeypatch.setenv(variable,'must-not-be-used')
    target=resolution_case[2].targets[0]
    module.OpenAITechnicalResolver().resolve(target)
    assert sdk.requests[-1]['model']=='gpt-6.1-sol'
    assert sdk.constructors[0]==dict(api_key=TEST_KEY,max_retries=0,timeout=180.0)
    monkeypatch.setenv('SNG_TECHNICAL_RESOLUTION_MODEL','resolution-model')
    module.OpenAITechnicalResolver().resolve(target)
    assert sdk.requests[-1]['model']=='resolution-model'
    monkeypatch.setenv('SNG_TECHNICAL_RESOLUTION_MODEL',' ')
    with pytest.raises(TechnicalResolutionConfigurationError): module.OpenAITechnicalResolver()


def test_ready_default_service_and_cli_never_create_provider(resolution_case,sdk,monkeypatch,capsys):
    from sng_consulter.technical import __main__ as command
    from sng_consulter.technical.resolution import service as service_module
    service,_,evidence,case,_=resolution_case
    prepare(case,())
    service.resolver_factory=None
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    before=snapshot(case[-1])
    monkeypatch.setattr(service_module,'TechnicalResolutionProposalService',lambda:service)
    assert command.main(['resolve',evidence.batch_id])==0
    assert 'not required' in capsys.readouterr().out
    assert not sdk.constructors and not sdk.requests
    assert snapshot(case[-1])==before


@pytest.mark.parametrize('failure',['refusal','incomplete','failed','malformed','schema','api','connection'])
@pytest.mark.parametrize('previous',[False,True])
def test_provider_failures_do_not_become_unresolved_or_overwrite_prior(resolution_case,sdk,failure,previous,caplog):
    service,_,evidence,case,_=resolution_case
    if previous: service.resolve(evidence.batch_id)
    service.resolver_factory=None
    if failure=='refusal': sdk.refusal=True
    elif failure=='incomplete': sdk.status='incomplete'
    elif failure=='failed': sdk.status='failed'
    elif failure=='malformed': sdk.malformed=True
    elif failure=='schema': sdk.payloads[evidence.targets[0].identity_id]={'bad':'contract'}
    elif failure=='api': sdk.error_on=2
    elif failure=='connection': sdk.connection=True
    before=snapshot(case[-1])
    with pytest.raises((InvalidTechnicalResolutionResponseError,TechnicalResolutionAPIError)) as caught:
        service.resolve(evidence.batch_id)
    assert sdk.client.is_closed() and snapshot(case[-1])==before
    assert TEST_KEY not in str(caught.value)+caplog.text
    assert 'data:image' not in str(caught.value)+caplog.text


@pytest.mark.parametrize('kind,count',[('candidates',24),('candidates',25),('pages',8),('pages',9)])
def test_explicit_budgets_without_truncation(resolution_case,sdk,kind,count):
    target=resolution_case[2].targets[0].model_copy(deep=True)
    identity=target.identity
    candidate,page=identity.candidates[0],identity.pages[0]
    identity.candidates=[candidate.model_copy(update=dict(covered_candidate_id=f'covered-{n:04d}',
        page_number=n if kind=='pages' else page.page_number)) for n in range(1,count+1)]
    identity.candidate_ids=[c.covered_candidate_id for c in identity.candidates]
    identity.pages=[page.model_copy(update=dict(page_number=n)) for n in range(1,count+1)] if kind=='pages' else [page]
    resolver=module.OpenAITechnicalResolver()
    if count>(24 if kind=='candidates' else 8):
        with pytest.raises(TechnicalResolutionContextBudgetExceeded): resolver.resolve(target)
        assert not sdk.requests
    else:
        resolver.resolve(target)
        content=sdk.requests[0]['input'][0]['content']
        assert len(json.loads(content[0]['text'])['candidates'])==count
        assert sum(json.loads(i['text']).get('kind')=='full_page' for i in content if i['type']=='input_text')==len(identity.pages)


@pytest.mark.parametrize('over',[False,True])
def test_metadata_exact_boundary_includes_every_text_block(resolution_case,sdk,over):
    target=resolution_case[2].targets[0].model_copy(deep=True)
    resolver=module.OpenAITechnicalResolver()
    resolver.resolve(target)
    size=sum(len(i['text'].encode('utf-8')) for i in sdk.requests[0]['input'][0]['content'] if i['type']=='input_text')
    sdk.requests.clear()
    target.identity.description+='x'*(module.MAX_RESOLUTION_METADATA_BYTES-size+int(over))
    if over:
        with pytest.raises(TechnicalResolutionContextBudgetExceeded): resolver.resolve(target)
        assert not sdk.requests
    else: assert resolver.resolve(target).identity_id==target.identity_id


def test_service_preserves_budget_error_type_and_closes_client(resolution_case,sdk,monkeypatch):
    service,_,evidence,case,_=resolution_case
    service.resolver_factory=None
    monkeypatch.setattr(module,'MAX_RESOLUTION_METADATA_BYTES',1)
    before=snapshot(case[-1])
    with pytest.raises(TechnicalResolutionContextBudgetExceeded): service.resolve(evidence.batch_id)
    assert not sdk.requests and sdk.client.is_closed() and snapshot(case[-1])==before


@pytest.mark.parametrize('outcome',['CORRECTED','DISPUTED','UNRESOLVED'])
def test_sdk_outcomes_with_issue_targeted_quantity_proposal(resolution_case,sdk,outcome):
    from test_technical_models import field
    service,_,evidence,case,_=resolution_case
    target=evidence.targets[0]
    payload=proposal_for(target,outcome)
    if outcome=='CORRECTED':
        payload['proposed_structure']['quantity']=field(2,'EXPLICIT',[target.identity.candidate_ids[0]],'Synthetic schedule quantity')
    sdk.payloads[target.identity_id]=payload
    service.resolver_factory=None
    before=snapshot(case[-1])
    result=service.resolve(evidence.batch_id)
    assert result.proposals[0].issue_resolutions[0].outcome==outcome
    assert result.proposals[0].proposed_structure.missing_fields==target.current_structure.missing_fields
    assert case[1].read_bytes()==before[case[1].relative_to(case[-1])]


def test_authorized_glass_correction_to_unknown_keeps_stale_bookkeeping(resolution_case,sdk):
    from test_technical_models import field
    from test_technical_resolution_inputs import build
    service,_,_,case,_=resolution_case
    data=json.loads(case[2].read_bytes())
    for issue in data['issues'][:2]:
        issue['field_paths']=['modules[0].glass_panes[1].glass_specification']
        issue['issue_type']='GLASS_SPECIFICATION_ERROR'
    case[2].write_text(json.dumps(data),encoding='utf-8')
    case[0].gate(case[4].batch_id)
    evidence=build(case)
    target=evidence.targets[0]
    payload=proposal_for(target,'CORRECTED')
    payload['proposed_structure']['modules'][0]['glass_panes'][1]['glass_specification']=field()
    sdk.payloads[target.identity_id]=payload
    service.resolver_factory=None
    result=service.resolve(evidence.batch_id)
    proposed=result.proposals[0].proposed_structure
    assert proposed.modules[0].glass_panes[1].glass_specification.resolution=='UNKNOWN'
    assert proposed.missing_fields==target.current_structure.missing_fields
    assert proposed.resolution_status==target.current_structure.resolution_status


@pytest.mark.parametrize('concept',[
    'Do NOT perform the extraction again from scratch', 'current_structure is the baseline',
    'a HARD authorization boundary', 'Do not fix, rewrite, normalize or improve any other field',
    'CORRECTED:', 'DISPUTED:', 'UNRESOLVED:', 'Audit is not ground truth', 'Never introduce DEFAULTED',
    'No pixel-derived measurements', 'finished-glass calculations', 'whole-set scope',
    'Preserve current missing_fields and resolution_status EXACTLY', 'Never invent IDs',
    'Composition is conservative', 'do not output applied results',
])
def test_issue_targeted_independent_prompt(concept):
    assert concept in ' '.join(module.TECHNICAL_RESOLUTION_INSTRUCTIONS.split())


@pytest.mark.parametrize('via_sdk', [False, True])
def test_validation_diagnostics_keep_all_paths_without_payload(resolution_case, sdk, via_sdk):
    from test_technical_models import field
    from sng_consulter.technical.resolution.service import validate_resolution_proposal
    target = resolution_case[2].targets[0]
    payload = proposal_for(target, 'CORRECTED')
    secret = 'PRIVATE_PROVIDER_PAYLOAD C:/private/document.pdf data:image/png;base64,SECRET ' + TEST_KEY
    structure = payload['proposed_structure']
    structure['modules'][0]['glass_panes'][0]['glass_specification'] = field(
        None, 'UNKNOWN', [target.identity.candidate_ids[0]], secret)
    structure['reference'] = field(None, 'UNKNOWN', [target.identity.candidate_ids[0]], secret)
    payload[secret] = secret
    sdk.payloads[target.identity_id] = payload
    resolver = module.OpenAITechnicalResolver() if via_sdk else None
    messages = []
    for _ in range(2):
        with pytest.raises(InvalidTechnicalResolutionResponseError) as caught:
            if via_sdk:
                resolver.resolve(target)
            else:
                validate_resolution_proposal(target, payload)
        message = str(caught.value)
        messages.append(message)
        assert 'proposed_structure.modules.0.glass_panes.0.glass_specification [value_error]' in message
        assert 'proposed_structure.reference [value_error]' in message
        assert 'UNKNOWN and DEFAULTED must not cite evidence' in message
        assert '<unknown-field> [extra_forbidden]' in message
        assert all(part not in message for part in (secret, TEST_KEY, 'PRIVATE_PROVIDER_PAYLOAD',
                                                     'C:/private', 'base64', 'input_value', 'input_type'))
    assert messages[0] == messages[1]


def test_untrusted_validation_messages_and_locations_are_not_echoed():
    from pydantic import ValidationError
    from sng_consulter.technical.resolution.service import safe_resolution_validation_summary
    secret = 'PRIVATE_PROVIDER_PAYLOAD C:/private/document.pdf ' + TEST_KEY
    error = ValidationError.from_exception_data('Provider', [dict(
        type='value_error', loc=('proposed_structure', secret), input=secret,
        ctx={'error': ValueError(secret)})])
    assert safe_resolution_validation_summary(error) == (
        'proposed_structure.<unknown-field> [value_error]: Invalid value')


def test_provider_response_validation_error_remains_sanitized(resolution_case, sdk, monkeypatch):
    from openai import APIResponseValidationError
    def fail(**kwargs):
        raise APIResponseValidationError(
            response=httpx2.Response(200, request=httpx2.Request('POST', 'https://api.openai.com/v1/responses')),
            body={'private': TEST_KEY}, message=TEST_KEY)
    resolver = module.OpenAITechnicalResolver()
    monkeypatch.setattr(resolver.client.responses, 'parse', fail)
    with pytest.raises(InvalidTechnicalResolutionResponseError) as caught:
        resolver.resolve(resolution_case[2].targets[0])
    assert str(caught.value) == 'Invalid structured resolution response (APIResponseValidationError)'
    assert TEST_KEY not in str(caught.value)


def test_prompt_separates_outcome_evidence_from_unknown_field_evidence():
    prompt = ' '.join(module.TECHNICAL_RESOLUTION_INSTRUCTIONS.split())
    for concept in (
        'issue_resolutions[].evidence_candidate_ids',
        'proposed_structure.<field>.evidence_candidate_ids',
        'EXPLICIT: value != null and documentary evidence required.',
        'INFERRED: value != null, documentary evidence and explanatory note required.',
        'UNKNOWN: value = null, evidence_candidate_ids = [], no invented value.',
        'CONFLICT: value = null, documentary evidence and explanatory note required.',
        'audit evidence is not positive evidence for UNKNOWN',
        'These two evidence lists serve different purposes.',
    ):
        assert concept in prompt

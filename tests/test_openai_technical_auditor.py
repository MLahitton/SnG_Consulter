import base64
import json
from types import SimpleNamespace

import httpx2
import pytest
from openai import OpenAI

from test_technical_audit import audit_case, technical_case, identity_review, review
from test_technical_inputs import snapshot
from test_openai_analyzer import structured_response, TEST_KEY
from sng_consulter.preparation.models import TextSpan
from sng_consulter.technical.audit import openai_auditor as module
from sng_consulter.technical.audit.models import TechnicalAuditIssueType
from sng_consulter.technical.audit.openai_auditor import (
    OpenAITechnicalAuditor, TechnicalAuditConfigurationError, TechnicalAuditAPIError,
    TechnicalAuditContextBudgetExceeded, InvalidTechnicalAuditResponseError,
)
from sng_consulter.technical.visuals import build_technical_images


@pytest.fixture
def sdk(audit_case,monkeypatch):
    evidence = audit_case[4]
    monkeypatch.setenv('OPENAI_API_KEY',TEST_KEY)
    monkeypatch.delenv('SNG_TECHNICAL_AUDIT_MODEL',raising=False)
    state = SimpleNamespace(requests=[],constructors=[],payloads={i.identity_id:dict(identity_id=i.identity_id,issues=[])
        for i in evidence.identities},error_on=None,status='completed',refusal=False,malformed=False,connection=False)
    def handle(request):
        data=json.loads(request.content)
        state.requests.append(data)
        if state.connection: raise httpx2.ConnectError(TEST_KEY,request=request)
        if state.error_on == len(state.requests):
            return httpx2.Response(401,json=dict(error=dict(message=TEST_KEY,type='invalid_api_key')))
        iid=json.loads(data['input'][0]['content'][0]['text'])['identity']['identity_id']
        body=structured_response(state.payloads[iid],status=state.status,refusal=state.refusal)
        if state.malformed: body['output'][0]['content'][0]['text']='{bad'
        return httpx2.Response(200,json=body)
    state.client=OpenAI(api_key=TEST_KEY,max_retries=0,base_url='https://api.openai.com/v1',
        http_client=httpx2.Client(transport=httpx2.MockTransport(handle)))
    def factory(**kwargs):
        state.constructors.append(kwargs)
        return state.client
    monkeypatch.setattr(module,'OpenAI',factory)
    yield state
    if not state.client.is_closed(): state.client.close()


def test_isolated_requests_strict_schema_order_images_and_no_writes(audit_case,sdk):
    service,_,source,current,evidence,root=audit_case
    service.auditor=OpenAITechnicalAuditor()
    before=snapshot(root)
    assert service.audit(evidence.batch_id).issues == []
    for identity,structure,request in zip(evidence.identities,current.structures,sdk.requests):
        assert request['store'] is False and request['model']=='gpt-6.1-sol'
        assert request['text']['format']['strict'] is True
        assert request['text']['format']['name']=='TechnicalStructureAuditProposal'
        assert request['instructions']==module.TECHNICAL_AUDIT_INSTRUCTIONS
        content=request['input'][0]['content']
        metadata=json.loads(content[0]['text'])
        assert metadata['current_structure']==structure.model_dump(mode='json')
        assert metadata['identity']['candidate_ids']==identity.candidate_ids
        assert [c['covered_candidate_id'] for c in metadata['candidates']]==identity.candidate_ids
        images=[i for i in content if i['type']=='input_image']
        expected=build_technical_images(identity)
        assert len(images)==len(expected)
        for image,original in zip(images,expected):
            assert image['detail']=='high'
            assert base64.b64decode(image['image_url'].split(',',1)[1])==original.data
        headers=[json.loads(i['text']) for i in content[1:] if i['type']=='input_text']
        assert [h['kind'] for h in headers]==['candidate_crop']*len(identity.candidates)+[
            'identity_page_context','associated_annotation_context','full_page','full_native_spans']*len(identity.pages)
        for actual,page in zip([h for h in headers if h['kind']=='full_native_spans'],identity.pages):
            assert actual['native_text_spans']==[[s.text,[s.bbox.x0,s.bbox.y0,s.bbox.x1,s.bbox.y1]] for s in page.native_text_spans]
        raw=json.dumps(content)
        assert all(secret not in raw for secret in ['image_path','original_name','filename',str(root),TEST_KEY])
        assert all(i.identity_id not in raw for i in evidence.identities if i.identity_id!=identity.identity_id)
    assert len(sdk.requests)==2
    after=snapshot(root)
    assert all(after[p]==data for p,data in before.items())
    requests=sdk.requests.copy()
    service.audit(evidence.batch_id)
    assert sdk.requests[2:]==requests
    assert source.read_bytes()==before[source.relative_to(root)]


@pytest.mark.parametrize('key',[None,'',' '])
def test_key_required(sdk,monkeypatch,key):
    if key is None: monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    else: monkeypatch.setenv('OPENAI_API_KEY',key)
    with pytest.raises(TechnicalAuditConfigurationError): OpenAITechnicalAuditor()
    assert not sdk.constructors and not sdk.requests


def test_independent_config(sdk,audit_case,monkeypatch):
    monkeypatch.setenv('SNG_TECHNICAL_MODEL','must-not-be-used')
    identity,current=audit_case[4].identities[0],audit_case[3].structures[0]
    OpenAITechnicalAuditor().audit(identity,current)
    assert sdk.requests[-1]['model']=='gpt-6.1-sol'
    monkeypatch.setenv('SNG_TECHNICAL_AUDIT_MODEL','audit-model')
    OpenAITechnicalAuditor().audit(identity,current)
    assert sdk.requests[-1]['model']=='audit-model'
    assert sdk.constructors[0]==dict(api_key=TEST_KEY,max_retries=0,timeout=180.0)
    monkeypatch.setenv('SNG_TECHNICAL_AUDIT_MODEL',' ')
    with pytest.raises(TechnicalAuditConfigurationError): OpenAITechnicalAuditor()


@pytest.mark.parametrize('failure',['refusal','incomplete','failed','malformed','contract','identity','api','connection'])
def test_explicit_failures_no_secret_leak_or_partial_output(audit_case,sdk,failure,caplog):
    service,_,_,_,evidence,root=audit_case
    target=evidence.identities[0].identity_id
    if failure=='refusal': sdk.refusal=True
    elif failure=='incomplete': sdk.status='incomplete'
    elif failure=='failed': sdk.status='failed'
    elif failure=='malformed': sdk.malformed=True
    elif failure=='contract': sdk.payloads[target]={'identity_id':target,'issues':[{'reason':' '}]}
    elif failure=='identity': sdk.payloads[target]['identity_id']='identity-9999'
    elif failure=='api': sdk.error_on=2
    elif failure=='connection': sdk.connection=True
    service.auditor=OpenAITechnicalAuditor()
    before=snapshot(root)
    with pytest.raises((TechnicalAuditAPIError,InvalidTechnicalAuditResponseError)) as caught:
        service.audit(evidence.batch_id)
    assert snapshot(root)==before
    assert TEST_KEY not in str(caught.value)+caplog.text
    assert 'data:image' not in str(caught.value)+caplog.text


@pytest.mark.parametrize('kind,count',[('candidates',9),('candidates',24),('candidates',25),('pages',8),('pages',9)])
def test_budgets_and_no_truncation(audit_case,sdk,kind,count):
    identity=audit_case[4].identities[0].model_copy(deep=True)
    page,candidate=identity.pages[0],identity.candidates[0]
    identity.candidates=[candidate.model_copy(update=dict(covered_candidate_id=f'covered-{n:04d}',
        page_number=n if kind=='pages' else page.page_number)) for n in range(1,count+1)]
    identity.candidate_ids=[c.covered_candidate_id for c in identity.candidates]
    identity.pages=[page.model_copy(update=dict(page_number=n)) for n in range(1,count+1)] if kind=='pages' else [page]
    auditor=OpenAITechnicalAuditor()
    if count>(24 if kind=='candidates' else 8):
        with pytest.raises(TechnicalAuditContextBudgetExceeded): auditor.audit(identity,audit_case[3].structures[0])
        assert not sdk.requests
    else:
        auditor.audit(identity,audit_case[3].structures[0])
        content=sdk.requests[0]['input'][0]['content']
        assert len(json.loads(content[0]['text'])['candidates'])==count
        assert sum(json.loads(i['text']).get('kind')=='full_page' for i in content if i['type']=='input_text')==len(identity.pages)


@pytest.mark.parametrize('over',[False,True])
def test_metadata_exact_boundary_including_contexts_and_structure(audit_case,sdk,over):
    identity=audit_case[4].identities[0].model_copy(deep=True)
    auditor=OpenAITechnicalAuditor()
    auditor.audit(identity,audit_case[3].structures[0])
    size=sum(len(i['text'].encode('utf-8')) for i in sdk.requests[0]['input'][0]['content'] if i['type']=='input_text')
    sdk.requests.clear()
    identity.description+='x'*(module.MAX_AUDIT_METADATA_BYTES-size+int(over))
    if over:
        with pytest.raises(TechnicalAuditContextBudgetExceeded): auditor.audit(identity,audit_case[3].structures[0])
        assert not sdk.requests
    else: assert auditor.audit(identity,audit_case[3].structures[0]).issues==[]


def test_service_preserves_budget_error_type_before_api(audit_case,sdk,monkeypatch):
    service,_,_,_,evidence,root=audit_case
    service.auditor=OpenAITechnicalAuditor()
    monkeypatch.setattr(module,'MAX_AUDIT_METADATA_BYTES',1)
    before=snapshot(root)
    with pytest.raises(TechnicalAuditContextBudgetExceeded): service.audit(evidence.batch_id)
    assert not sdk.requests and snapshot(root)==before


@pytest.mark.parametrize('case,kind,path',[
    ('supported-dimension',None,'dimensions.width_mm'),
    ('wrong-dimension','DIMENSION_ERROR','dimensions.width_mm'),
    ('missing-dimension','MISSING_SUPPORTED_VALUE','dimensions.width_mm'),
    ('sill-thickness','ATTRIBUTE_OWNERSHIP_ERROR','modules[0].glass_panes[0].glass_specification'),
    ('missing-glass','GLASS_SPECIFICATION_ERROR','modules[0].glass_panes[0].glass_specification'),
    ('fixed-three-panes',None,'modules'),
    ('panes-as-modules','MODULE_COMPOSITION_ERROR','modules'),
    ('decorative-grid',None,'modules[0].glass_panes'),
    ('grid-as-panes','PANE_COMPOSITION_ERROR','modules[0].glass_panes'),
    ('views-as-quantity','QUANTITY_ERROR','quantity'),
    ('inferred-unit','RESOLUTION_ERROR','dimensions.width_mm'),
])
def test_synthetic_semantic_audit_proposals(audit_case,sdk,case,kind,path):
    """Transport fixtures prescribe issues, not model evaluation or local inference."""
    identity=audit_case[4].identities[0].model_copy(deep=True)
    current=audit_case[3].structures[0].model_copy(deep=True)
    from test_technical_models import field, known
    document='Explicit overall width 3.45 m'
    current.dimensions.width_mm.value=3450
    if case=='wrong-dimension': current.dimensions.width_mm.value=3650
    elif case=='missing-dimension': current.dimensions.width_mm=type(current.dimensions.width_mm)(**field())
    elif case=='sill-thickness':
        document='black sill 65 mm'
        current.modules[0].glass_panes[0].glass_specification.value='65 mm glass'
    elif case=='missing-glass':
        document='Tempered glass for the glazed set'
        current.modules[0].glass_panes[0].glass_specification=type(current.modules[0].glass_panes[0].glass_specification)(**field())
    elif case in {'fixed-three-panes','panes-as-modules'}:
        document='One fixed assembly with three physical glass panes'
        if kind:
            current.modules=[current.modules[0].model_copy(deep=True) for _ in range(3)]
            current.module_count=type(current.module_count)(**known(3,identity.candidate_ids[0]))
    elif case in {'decorative-grid','grid-as-panes'}:
        document='One physical glass pane with decorative grid'
        count=6 if kind else 1
        current.modules[0].glass_panes=[current.modules[0].glass_panes[0].model_copy(deep=True) for _ in range(count)]
        current.modules[0].glass_pane_count=type(current.module_count)(**known(count,identity.candidate_ids[0]))
    elif case=='views-as-quantity':
        document='Three views of one design, no quantity specified'
        current.quantity=type(current.quantity)(**known(3,identity.candidate_ids[0]))
    elif case=='inferred-unit': document='Coherent decimal convention: .85, 1.45, 3.45; unit not printed'
    # Keep synthetic hypotheses structurally valid so issues concern documentary
    # meaning, not corruption which the pre-audit would already have rejected.
    for mi,m in enumerate(current.modules,100):
        m.module_id=f'module-{mi:04d}'
        for pi,p in enumerate(m.glass_panes): p.glass_pane_id=f'glass-pane-{mi*100+pi:04d}'
        for di,d in enumerate(m.decorative_elements): d.decorative_element_id=f'decorative-{mi*100+di:04d}'
    from sng_consulter.technical.service import _resolved_fields
    fields=list(_resolved_fields(current))
    current.missing_fields=[p for p,f in fields if f.resolution=='UNKNOWN']
    current.resolution_status='PARTIAL' if current.missing_fields else 'RESOLVED'
    from sng_consulter.technical.validation import validate_technical_result
    entire=audit_case[3].model_copy(deep=True)
    entire.structures[0]=current
    validate_technical_result(entire,audit_case[4])
    identity.pages[0].native_text_spans=[TextSpan(text=document,bbox=dict(x0=22,y0=94,x1=48,y1=99))]
    sdk.payloads[identity.identity_id]['issues']=[] if kind is None else [dict(issue_type=kind,
        field_paths=[path],candidate_ids=[identity.candidate_ids[0]],reason='Specific synthetic documentary discrepancy')]
    result=OpenAITechnicalAuditor().audit(identity,current)
    assert [i.issue_type for i in result.issues]==([] if kind is None else [kind])
    assert document in json.dumps(sdk.requests[0]['input'])


@pytest.mark.parametrize('failure',[False,True])
def test_cli_audit_only_and_client_closed(audit_case,sdk,monkeypatch,capsys,failure):
    from sng_consulter.technical import __main__ as command
    from sng_consulter.technical.audit import service as audit_service
    from sng_consulter.discovery.service import DiscoveryService
    from sng_consulter.identity.service import IdentityResolutionService
    from sng_consulter.identity.audit.service import IdentityAuditService
    from sng_consulter.identity.gate import IdentityGateService
    from sng_consulter.technical.service import TechnicalExtractionService
    service,_,_,_,evidence,root=audit_case
    def forbidden(*a,**kw): pytest.fail('Audit must not execute extraction or earlier phases')
    from sng_consulter.technical import openai_extractor
    monkeypatch.setattr(openai_extractor,'OpenAITechnicalExtractor',forbidden)
    for cls,name in [(DiscoveryService,'discover'),(IdentityResolutionService,'resolve'),
        (IdentityAuditService,'audit'),(IdentityGateService,'gate'),(TechnicalExtractionService,'extract')]:
        monkeypatch.setattr(cls,name,forbidden)
    def factory(auditor):
        service.auditor=auditor
        return service
    monkeypatch.setattr(audit_service,'TechnicalAuditService',factory)
    if failure: sdk.error_on=2
    before=snapshot(root)
    assert command.main(['audit',evidence.batch_id])==(1 if failure else 0)
    assert sdk.client.is_closed() and len(sdk.requests)==2
    after=snapshot(root)
    assert all(after[p]==data for p,data in before.items())
    output=capsys.readouterr()
    assert ('TechnicalAuditAPIError' in output.err) if failure else ('Technical Audit completed' in output.out)


@pytest.mark.parametrize('concept',[
    'Do NOT perform the extraction again from scratch', 'UNKNOWN is often correct',
    'not ground truth', 'A clean issues=[] is valid and expected',
    'Never transfer frame/profile/sill/threshold/hardware/wall/decorative attributes',
    'MOST SPECIFIC issue type', 'inferred unit is not EXPLICIT',
    'GlassPane != decorative cell', 'Repeated view != quantity',
])
def test_independent_prompt(concept):
    assert concept in ' '.join(module.TECHNICAL_AUDIT_INSTRUCTIONS.split())


@pytest.mark.parametrize('kind',list(TechnicalAuditIssueType))
def test_all_issue_types_survive_strict_sdk_and_service(audit_case,sdk,kind):
    service,_,_,_,evidence,_=audit_case
    for identity in evidence.identities:
        sdk.payloads[identity.identity_id]['issues']=[dict(issue_type=kind,field_paths=['scope'],
            candidate_ids=[identity.candidate_ids[0]],reason='Concrete synthetic documentary concern')]
    service.auditor=OpenAITechnicalAuditor()
    result=service.audit(evidence.batch_id)
    assert [i.issue_type for i in result.issues]==[kind,kind]

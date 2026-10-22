import base64
import json
from types import SimpleNamespace

import httpx2
from openai import OpenAI
import pytest

from test_technical_verification_service import (
    service_case, verification_case, application_case, resolution_case, gate_case, audit_case,
    technical_case, identity_review, review,
)
from test_technical_verification_models import issue_for, proposal_for
from test_technical_resolution_service import proposal_for as resolution_proposal
from test_technical_models import known
from test_technical_inputs import snapshot
from test_openai_analyzer import TEST_KEY, structured_response
from sng_consulter.technical.verification import openai_verifier as module
from sng_consulter.technical.verification import (
    TechnicalVerificationAPIError, TechnicalVerificationConfigurationError,
    InvalidTechnicalVerificationResponseError, TechnicalVerificationContextBudgetExceeded,
)
from sng_consulter.technical.visuals import build_technical_images


@pytest.fixture
def sdk(service_case, monkeypatch):
    targets = service_case[2].targets
    monkeypatch.setenv('OPENAI_API_KEY', TEST_KEY)
    monkeypatch.delenv('SNG_TECHNICAL_VERIFICATION_MODEL', raising=False)
    state = SimpleNamespace(requests=[], constructors=[], payloads={t.identity_id: proposal_for(t) for t in targets},
        error_on=None, status='completed', refusal=False, malformed=False, connection=False)
    def handle(request):
        data = json.loads(request.content)
        state.requests.append(data)
        if state.connection: raise httpx2.ConnectError(TEST_KEY, request=request)
        if len(state.requests) == state.error_on:
            return httpx2.Response(401, json={'error': {'message': TEST_KEY, 'type': 'invalid_api_key'}})
        identity_id = json.loads(data['input'][0]['content'][0]['text'])['identity_id']
        body = structured_response(state.payloads[identity_id], status=state.status, refusal=state.refusal)
        if state.malformed: body['output'][0]['content'][0]['text'] = '{invalid'
        return httpx2.Response(200, json=body)
    state.client = OpenAI(api_key=TEST_KEY, max_retries=0, base_url='https://api.openai.com/v1',
        http_client=httpx2.Client(transport=httpx2.MockTransport(handle)))
    def factory(**kwargs):
        state.constructors.append(kwargs)
        return state.client
    monkeypatch.setattr(module, 'OpenAI', factory)
    yield state
    if not state.client.is_closed(): state.client.close()


def test_strict_isolated_requests_reuse_all_visual_evidence_without_writes(service_case, sdk):
    service, _, evidence, case, _ = service_case
    service.verifier_factory = None
    before = snapshot(case[2][-1])
    result = service.verify(evidence.batch_id)
    assert result.structures_verified == 2 and result.issue_count == 0 and sdk.client.is_closed()
    assert len(sdk.requests) == 2
    for target, request in zip(evidence.targets, sdk.requests):
        assert request['model'] == 'gpt-6.1-sol' and request['store'] is False
        assert request['instructions'] == module.TECHNICAL_VERIFICATION_INSTRUCTIONS
        assert request['text']['format']['strict'] is True
        assert request['text']['format']['name'] == 'TechnicalStructureVerificationProposal'
        content = request['input'][0]['content']
        metadata = json.loads(content[0]['text'])
        assert metadata['current_structure'] == target.current_structure.model_dump(mode='json')
        assert metadata['identity'] == dict(identity_id=target.identity_id, reference_hint=target.identity.reference_hint,
            description=target.identity.description, candidate_ids=target.identity.candidate_ids)
        assert metadata['technical_structure_id'] == target.technical_structure_id
        assert metadata['candidates'] == [c.model_dump(mode='json', exclude={'image_path'}) for c in target.identity.candidates]
        images = [item for item in content if item['type'] == 'input_image']
        expected = build_technical_images(target.identity)
        assert len(images) == len(expected)
        for actual, image in zip(images, expected):
            assert actual['detail'] == 'high'
            assert base64.b64decode(actual['image_url'].split(',', 1)[1]) == image.data
        headers = [json.loads(item['text']) for item in content[1:] if item['type'] == 'input_text']
        assert [h['kind'] for h in headers] == ['candidate_crop'] * len(target.identity.candidates) + [
            'identity_page_context', 'associated_annotation_context', 'full_page', 'full_native_spans'] * len(target.identity.pages)
        assert sum(h['kind'] == 'full_page' for h in headers) == len(target.identity.pages)
        for header, page in zip([h for h in headers if h['kind'] == 'full_native_spans'], target.identity.pages):
            assert header['native_text_spans'] == [[span.text, [span.bbox.x0, span.bbox.y0, span.bbox.x1, span.bbox.y1]]
                                                   for span in page.native_text_spans]
        raw = json.dumps(content)
        assert all(t.identity_id not in raw for t in evidence.targets if t.identity_id != target.identity_id)
        assert all(token not in raw for token in (str(case[2][-1]), 'image_path', 'filename', 'original_name', TEST_KEY))
    after = snapshot(case[2][-1])
    assert all(after[path] == raw for path, raw in before.items())
    assert set(after) - set(before) == {case[-1].with_name('technical_verification.json').relative_to(case[2][-1])}


def test_prior_audit_resolver_and_apply_history_never_reaches_request(service_case, sdk, resolution_case):
    service, _, _, case, _ = service_case
    app, applied, source_case, _, _ = case
    audit = json.loads(source_case[2].read_bytes())
    audit_reason = 'PRIVATE AUDIT HISTORY technical-audit-0001 blocking_issue_ids'
    resolver_reason = 'PRIVATE RESOLVER HISTORY CORRECTED DISPUTED UNRESOLVED changed_paths'
    for issue in audit['issues']: issue['reason'] = audit_reason
    source_case[2].write_text(json.dumps(audit), encoding='utf-8')
    source_case[0].gate(applied.batch_id)
    proposal_service, resolver = resolution_case[:2]
    from test_technical_resolution_inputs import build as build_resolution
    work = build_resolution(source_case)
    for index, target in enumerate(work.targets):
        payload = resolution_proposal(target, 'CORRECTED' if index == 0 else 'UNRESOLVED')
        for outcome in payload['issue_resolutions']: outcome['reason'] = resolver_reason
        if index == 0: payload['proposed_structure']['quantity'] = known(2, target.identity.candidate_ids[0])
        resolver.payloads[target.identity_id] = payload
    proposal_service.resolve(applied.batch_id)
    app.apply(applied.batch_id)
    service.verifier_factory = None
    service.verify(applied.batch_id)
    for request in sdk.requests:
        raw = json.dumps(request)
        assert 'UNKNOWN' in raw
        for token in (audit_reason, resolver_reason, 'technical-audit', 'technical-gate', 'technical-verification-0001',
                      'technical_resolution_proposals', 'issue_resolutions', 'changed_paths', 'blocking_issue_ids',
                      'CORRECTED', 'DISPUTED', 'UNRESOLVED', 'source_technical_', 'resolution_required',
                      'changes_applied_count'):
            assert token not in raw


def test_independent_configuration(service_case, sdk, monkeypatch):
    target = service_case[2].targets[0]
    for name in ('SNG_TECHNICAL_MODEL', 'SNG_TECHNICAL_AUDIT_MODEL', 'SNG_TECHNICAL_RESOLUTION_MODEL'):
        monkeypatch.setenv(name, 'must-not-be-used')
    module.OpenAITechnicalVerifier().verify(target)
    assert sdk.requests[-1]['model'] == 'gpt-6.1-sol'
    assert sdk.constructors[0] == dict(api_key=TEST_KEY, max_retries=0, timeout=180.0)
    monkeypatch.setenv('SNG_TECHNICAL_VERIFICATION_MODEL', 'verification-model')
    module.OpenAITechnicalVerifier().verify(target)
    assert sdk.requests[-1]['model'] == 'verification-model'
    assert module.OpenAITechnicalVerifier(model='explicit-model').model == 'explicit-model'
    monkeypatch.setenv('SNG_TECHNICAL_VERIFICATION_MODEL', ' ')
    with pytest.raises(TechnicalVerificationConfigurationError): module.OpenAITechnicalVerifier()


@pytest.mark.parametrize('key', [None, '', ' '])
def test_api_key_required(sdk, monkeypatch, key):
    if key is None: monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    else: monkeypatch.setenv('OPENAI_API_KEY', key)
    with pytest.raises(TechnicalVerificationConfigurationError): module.OpenAITechnicalVerifier()
    assert not sdk.requests and not sdk.constructors


@pytest.mark.parametrize('failure', ['refusal', 'incomplete', 'failed', 'malformed', 'schema', 'api', 'connection'])
@pytest.mark.parametrize('previous', [False, True])
def test_provider_errors_sanitized_never_become_clean_and_preserve_sources(service_case, sdk, caplog, failure, previous):
    service, _, evidence, case, _ = service_case
    if previous: service.verify(evidence.batch_id)
    service.verifier_factory = None
    if failure == 'refusal': sdk.refusal = True
    elif failure == 'incomplete': sdk.status = 'incomplete'
    elif failure == 'failed': sdk.status = 'failed'
    elif failure == 'malformed': sdk.malformed = True
    elif failure == 'schema': sdk.payloads[evidence.targets[1].identity_id] = {'raw': TEST_KEY}
    elif failure == 'api': sdk.error_on = 2
    elif failure == 'connection': sdk.connection = True
    before = snapshot(case[2][-1])
    with pytest.raises((InvalidTechnicalVerificationResponseError, TechnicalVerificationAPIError)) as caught:
        service.verify(evidence.batch_id)
    assert sdk.client.is_closed() and snapshot(case[2][-1]) == before
    assert all(token not in str(caught.value) + caplog.text for token in (TEST_KEY, 'data:image', str(case[2][-1])))


@pytest.mark.parametrize('kind,count', [('candidates', 24), ('candidates', 25), ('pages', 8), ('pages', 9)])
def test_candidate_and_page_budgets_before_api_without_truncation(service_case, sdk, kind, count):
    target = service_case[2].targets[0].model_copy(deep=True)
    identity = target.identity
    candidate, page = identity.candidates[0], identity.pages[0]
    identity.candidates = [candidate.model_copy(update=dict(covered_candidate_id=f'covered-{n:04d}',
        page_number=n if kind == 'pages' else page.page_number)) for n in range(1, count + 1)]
    identity.candidate_ids = [c.covered_candidate_id for c in identity.candidates]
    identity.pages = [page.model_copy(update=dict(page_number=n)) for n in range(1, count + 1)] if kind == 'pages' else [page]
    verifier = module.OpenAITechnicalVerifier()
    if count > (24 if kind == 'candidates' else 8):
        with pytest.raises(TechnicalVerificationContextBudgetExceeded): verifier.verify(target)
        assert not sdk.requests
    else:
        verifier.verify(target)
        content = sdk.requests[0]['input'][0]['content']
        assert len(json.loads(content[0]['text'])['candidates']) == count
        assert sum(json.loads(item['text']).get('kind') == 'full_page' for item in content if item['type'] == 'input_text') == len(identity.pages)


@pytest.mark.parametrize('over', [False, True])
def test_utf8_metadata_budget_counts_all_headers_without_chunking(service_case, sdk, over):
    target = service_case[2].targets[0].model_copy(deep=True)
    verifier = module.OpenAITechnicalVerifier()
    verifier.verify(target)
    size = sum(len(item['text'].encode('utf-8')) for item in sdk.requests[0]['input'][0]['content'] if item['type'] == 'input_text')
    sdk.requests.clear()
    target.identity.description += 'x' * (module.MAX_VERIFICATION_METADATA_BYTES - size + int(over))
    if over:
        with pytest.raises(TechnicalVerificationContextBudgetExceeded): verifier.verify(target)
        assert not sdk.requests
    else:
        verifier.verify(target)
        assert len(sdk.requests) == 1


def test_initial_metadata_budget_fails_before_building_images(service_case, sdk, monkeypatch):
    target = service_case[2].targets[0].model_copy(deep=True)
    target.identity.description = 'x' * module.MAX_VERIFICATION_METADATA_BYTES
    def forbidden(*args): pytest.fail('Oversized metadata must fail before image construction')
    monkeypatch.setattr(module, 'build_technical_images', forbidden)
    with pytest.raises(TechnicalVerificationContextBudgetExceeded): module.OpenAITechnicalVerifier().verify(target)
    assert not sdk.requests


@pytest.mark.parametrize('concept', [
    'independently verifying the current applied technical result', 'current structure is not presumed correct',
    'not resolving a previous audit', 'not told which fields were previously questioned or changed',
    'Report only concrete, documentary-supported technical issues', 'UNKNOWN and CONFLICT can be correct',
    'MISSING_SUPPORTED_VALUE only when available evidence clearly supports a value', 'EXPLICIT requires the value AND its association',
    'INFERRED requires legitimate supported inference', 'Do not propose DEFAULTED', 'Do not measure pixels',
    'No finished-glass calculations', 'correct ownership', 'whole-set note', 'Never transfer frame/profile',
    'Physical quantity != views/references/modules/panes', 'visible rectangle as automatically a module or pane',
    'Do not infer commercial systems or prices', 'Do not produce corrected values', 'No external knowledge',
    'Do not audit missing_fields or resolution_status bookkeeping', 'No OCR', 'issues=[] is valid',
])
def test_independent_verifier_prompt_contract(concept):
    assert concept in ' '.join(module.TECHNICAL_VERIFICATION_INSTRUCTIONS.split())


@pytest.mark.parametrize('failure', [False, True])
def test_cli_default_adapter_closes_on_success_and_error(service_case, sdk, monkeypatch, capsys, failure):
    from sng_consulter.technical import __main__ as command
    from sng_consulter.technical import verification
    service, _, evidence, _, _ = service_case
    service.verifier_factory = None
    if failure: sdk.error_on = 2
    monkeypatch.setattr(verification, 'TechnicalVerificationService', lambda: service)
    assert command.main(['verify', evidence.batch_id]) == (1 if failure else 0)
    assert sdk.client.is_closed()
    output = capsys.readouterr()
    assert output.err if failure else '0 issues' in output.out


def test_sdk_valid_documentary_issue_is_observation_not_execution_failure(service_case, sdk):
    service, _, evidence, _, _ = service_case
    service.verifier_factory = None
    target = evidence.targets[0]
    sdk.payloads[target.identity_id] = proposal_for(target, [issue_for(target)])
    result = service.verify(evidence.batch_id)
    assert result.issue_count == 1 and result.structures_verified == 2
    assert result.issues[0].issue_id == 'technical-verification-0001'


def test_invalid_input_fails_before_default_provider_creation(service_case, sdk):
    from sng_consulter.technical.verification import InvalidTechnicalVerificationInputError
    service, _, evidence, case, _ = service_case
    service.verifier_factory = None
    case[-1].write_bytes(b'{invalid')
    with pytest.raises(InvalidTechnicalVerificationInputError): service.verify(evidence.batch_id)
    assert not sdk.requests and not sdk.constructors


def test_service_preserves_budget_error_and_closes_default_client(service_case, sdk, monkeypatch):
    service, _, evidence, case, _ = service_case
    service.verifier_factory = None
    monkeypatch.setattr(module, 'MAX_VERIFICATION_METADATA_BYTES', 1)
    before = snapshot(case[2][-1])
    with pytest.raises(TechnicalVerificationContextBudgetExceeded): service.verify(evidence.batch_id)
    assert sdk.client.is_closed() and not sdk.requests
    assert snapshot(case[2][-1]) == before


@pytest.mark.parametrize('error_type', [ValueError, OSError])
def test_visual_evidence_failure_is_sanitized_before_api(service_case, sdk, monkeypatch, error_type):
    from sng_consulter.technical.verification import InvalidTechnicalVerificationInputError
    service, _, evidence, case, _ = service_case
    service.verifier_factory = None
    def fail_images(*args): raise error_type('PRIVATE_FILESYSTEM_PATH ' + TEST_KEY)
    monkeypatch.setattr(module, 'build_technical_images', fail_images)
    before = snapshot(case[2][-1])
    with pytest.raises(InvalidTechnicalVerificationInputError) as caught:
        service.verify(evidence.batch_id)
    assert all(token not in str(caught.value) for token in ('PRIVATE_FILESYSTEM_PATH', TEST_KEY))
    assert not sdk.requests and sdk.client.is_closed() and snapshot(case[2][-1]) == before

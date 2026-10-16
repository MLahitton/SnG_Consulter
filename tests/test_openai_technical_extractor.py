import base64
import json
import logging
import traceback
from io import BytesIO
from types import SimpleNamespace

import httpx2
import pytest
from openai import OpenAI
from PIL import Image

from test_technical_inputs import technical_case, identity_review, review, snapshot, build
from test_technical_models import structure, field, known
from test_openai_analyzer import structured_response, TEST_KEY
from sng_consulter.technical import TechnicalExtractionService
from sng_consulter.technical import openai_extractor
from sng_consulter.technical.openai_extractor import (
    OpenAITechnicalExtractor, TechnicalConfigurationError, TechnicalAPIError, InvalidTechnicalResponseError,
    TechnicalContextBudgetExceeded, UnexpectedTechnicalDefaultError,
)
from sng_consulter.technical.models import TechnicalExtractionProposal
from sng_consulter.technical.visuals import build_technical_images
from sng_consulter.technical.__main__ import main


@pytest.fixture
def sdk(technical_case,monkeypatch):
    evidence = technical_case[0]
    monkeypatch.setenv("OPENAI_API_KEY",TEST_KEY)
    monkeypatch.delenv("SNG_TECHNICAL_MODEL",raising=False)
    state = SimpleNamespace(requests=[],constructors=[],payloads={g.identity_id:structure(g.identity_id,g.candidate_ids[0])
        for g in evidence.identities},error_on=None,refusal=False,status="completed",malformed=False,connection=False)
    def handle(request):
        data = json.loads(request.content)
        state.requests.append(data)
        assert request.headers["authorization"] == f"Bearer {TEST_KEY}"
        assert request.url.path == "/v1/responses"
        if state.connection: raise httpx2.ConnectError(TEST_KEY,request=request)
        if len(state.requests) == state.error_on:
            return httpx2.Response(401,json=dict(error=dict(message=TEST_KEY,type="invalid_api_key")))
        identity_id = json.loads(data["input"][0]["content"][0]["text"])["identity"]["identity_id"]
        body = structured_response(state.payloads[identity_id],status=state.status,refusal=state.refusal)
        if state.malformed: body["output"][0]["content"][0]["text"] = "{broken"
        return httpx2.Response(200,json=body)
    client = OpenAI(api_key=TEST_KEY,max_retries=0,base_url="https://api.openai.com/v1",
                    http_client=httpx2.Client(transport=httpx2.MockTransport(handle)))
    def factory(**kwargs):
        state.constructors.append(kwargs)
        return client
    state.client = client
    monkeypatch.setattr(openai_extractor,"OpenAI",factory)
    yield state
    if not client.is_closed(): client.close()


def test_isolated_ordered_requests_exact_schema_visuals_native_text_and_no_writes(sdk,technical_case):
    evidence,_,_,_,_,root = technical_case
    before = snapshot(root)
    result = OpenAITechnicalExtractor().extract(evidence)
    assert isinstance(result,TechnicalExtractionProposal)
    assert [s.identity_id for s in result.structures] == [g.identity_id for g in evidence.identities]
    assert len(sdk.requests) == len(evidence.identities) == 2
    for identity,request in zip(evidence.identities,sdk.requests):
        assert request["store"] is False
        assert request["text"]["format"]["strict"] is True
        assert request["text"]["format"]["name"] == "TechnicalStructureProposal"
        assert "technical_structure_id" not in request["text"]["format"]["schema"]["properties"]
        content = request["input"][0]["content"]
        metadata = json.loads(content[0]["text"])
        assert metadata["identity"]["identity_id"] == identity.identity_id
        assert [c["covered_candidate_id"] for c in metadata["candidates"]] == identity.candidate_ids
        full_spans = [json.loads(i["text"]) for i in content[1:] if i["type"] == "input_text" and json.loads(i["text"])["kind"] == "full_native_spans"]
        assert len(full_spans) == len(identity.pages)
        for actual,page in zip(full_spans,identity.pages):
            assert actual["native_text_spans"] == [[s.text,[s.bbox.x0,s.bbox.y0,s.bbox.x1,s.bbox.y1]] for s in page.native_text_spans]
        raw = json.dumps(content)
        assert all(value not in raw for value in ["filename","original_name","image_path",str(root),TEST_KEY])
        images = [item for item in content if item["type"] == "input_image"]
        assert len(images) == len(identity.candidates)+3*len(identity.pages)
        expected = build_technical_images(identity)
        for item,image in zip(images,expected):
            assert item["detail"] == "high"
            assert base64.b64decode(item["image_url"].split(",",1)[1]) == image.data
        full = [i for i in expected if i.metadata["kind"] == "full_page"]
        assert [image.data for image in full] == [p.image_path.read_bytes() for p in identity.pages]
        other_ids = {g.identity_id for g in evidence.identities}-{identity.identity_id}
        assert all(other not in raw for other in other_ids)
    assert snapshot(root) == before
    original = sdk.requests.copy()
    OpenAITechnicalExtractor().extract(evidence)
    assert sdk.requests[2:] == original


def test_crop_uses_canonical_helper_and_labels_outside_evidence(technical_case,monkeypatch):
    from sng_consulter.technical import visuals
    original = visuals.candidate_crop
    calls = []
    def track(image,candidate):
        calls.append(candidate.covered_candidate_id)
        return original(image,candidate)
    monkeypatch.setattr(visuals,"candidate_crop",track)
    identity = technical_case[0].identities[0]
    images = build_technical_images(identity)
    assert calls == identity.candidate_ids
    for image,color in zip(images[:2],[(255,0,0),(0,128,0)]):
        with Image.open(BytesIO(image.data)) as crop:
            assert crop.height == 44+32
            assert crop.getpixel((crop.width//2,32+22)) == color
            assert crop.crop((0,0,crop.width,32)).convert("L").getextrema()[0] == 0
    assert [i.metadata["covered_candidate_id"] for i in images[:2]] == identity.candidate_ids


def test_multiple_candidates_share_one_full_page(technical_case,sdk):
    evidence = technical_case[0].model_copy(deep=True)
    target = evidence.identities[0]
    candidate = target.candidates[0].model_copy(update=dict(covered_candidate_id="covered-9999"))
    target.candidates.append(candidate)
    target.candidate_ids.append(candidate.covered_candidate_id)
    OpenAITechnicalExtractor().extract(evidence)
    content = sdk.requests[0]["input"][0]["content"]
    headers = [json.loads(i["text"]) for i in content[1:] if i["type"] == "input_text"]
    assert sum(h["kind"] == "candidate_crop" for h in headers) == 3
    assert sum(h["kind"] == "full_page" for h in headers) == 2


def test_model_configuration_independent_and_transport_settings(sdk,technical_case,monkeypatch):
    for variable in ["SNG_DISCOVERY_MODEL","SNG_IDENTITY_MODEL","SNG_IDENTITY_AUDIT_MODEL"]:
        monkeypatch.setenv(variable,"must-not-be-used")
    OpenAITechnicalExtractor().extract(technical_case[0])
    assert sdk.requests[0]["model"] == "gpt-6.1-sol"
    monkeypatch.setenv("SNG_TECHNICAL_MODEL","technical-model")
    OpenAITechnicalExtractor().extract(technical_case[0])
    assert sdk.requests[-1]["model"] == "technical-model"
    assert sdk.constructors[0] == dict(api_key=TEST_KEY,max_retries=0,timeout=180.0)
    monkeypatch.setenv("SNG_TECHNICAL_MODEL"," ")
    with pytest.raises(TechnicalConfigurationError): OpenAITechnicalExtractor()


@pytest.mark.parametrize("key",[None,""," "])
def test_api_key_required(sdk,monkeypatch,key):
    if key is None: monkeypatch.delenv("OPENAI_API_KEY",raising=False)
    else: monkeypatch.setenv("OPENAI_API_KEY",key)
    with pytest.raises(TechnicalConfigurationError): OpenAITechnicalExtractor()
    assert sdk.constructors == [] and sdk.requests == []


@pytest.mark.parametrize("path",[
    ("scope",),("reference",),("name",),("category",),("location",),("quantity",),("module_count",),
    ("dimensions","width_mm"),("dimensions","height_mm"),("modules",0,"position_hint"),
    ("modules",0,"function"),("modules",0,"opening_direction"),("modules",0,"glass_pane_count"),
    ("modules",0,"glass_panes",0,"glass_specification"),("modules",0,"glass_panes",0,"dimensions","height_mm"),
    ("modules",0,"decorative_elements",0,"kind"),
])
def test_any_nested_default_is_explicitly_rejected(sdk,technical_case,path):
    identity = technical_case[0].identities[0]
    data = structure(identity.identity_id,identity.candidate_ids[0],decorations=1)
    target = data
    for part in path: target = target[part]
    target.update(value=target["value"] if target["value"] is not None else 1,resolution="DEFAULTED",
                  evidence_candidate_ids=[],note="Rule forbidden for provider")
    sdk.payloads[identity.identity_id] = data
    with pytest.raises(UnexpectedTechnicalDefaultError): OpenAITechnicalExtractor().extract(technical_case[0])
    assert len(sdk.requests) == 1


def test_domain_outputs_resolution_unit_normalization_and_not_glass_retained(sdk,technical_case,tmp_path):
    evidence = technical_case[0]
    first = sdk.payloads[evidence.identities[0].identity_id]
    first["dimensions"]["width_mm"] = {**known(3000),"note":"Document explicitly shows 3.00 m; normalized to 3000 mm."}
    first["location"] = field(None,"CONFLICT",["covered-0001"],"Conflicting location notes")
    second = sdk.payloads[evidence.identities[1].identity_id]
    second.update(scope=field("NOT_GLASS_RELATED","INFERRED",["covered-0004"],"Observable opaque item"),module_count=field(),modules=[])
    result = TechnicalExtractionService(OpenAITechnicalExtractor(),tmp_path / "technical").extract(evidence)
    a,b = result.structures
    assert a.module_count.value == len(a.modules) == 1
    assert a.modules[0].glass_pane_count.value == len(a.modules[0].glass_panes) == 3
    assert a.quantity.value is None and a.quantity.resolution == "UNKNOWN"
    assert a.dimensions.width_mm.value == 3000 and a.dimensions.width_mm.resolution == "EXPLICIT"
    assert "3.00 m" in a.dimensions.width_mm.note
    assert a.resolution_status == "AMBIGUOUS"
    assert b.scope.value == "NOT_GLASS_RELATED" and b.module_count.value is None and b.modules == []
    assert len(result.structures) == 2


def test_decorations_do_not_increase_panes(sdk,technical_case):
    first = technical_case[0].identities[0]
    sdk.payloads[first.identity_id] = structure(first.identity_id,first.candidate_ids[0],panes=1,decorations=5)
    result = OpenAITechnicalExtractor().extract(technical_case[0]).structures[0]
    assert result.module_count.value == 1
    assert result.modules[0].glass_pane_count.value == len(result.modules[0].glass_panes) == 1
    assert len(result.modules[0].decorative_elements) == 5


@pytest.mark.parametrize("previous",[False,True])
@pytest.mark.parametrize("mode",["wrong-identity","module-count","pane-count","invalid","api","refusal","incomplete","malformed","connection","failed"])
def test_failure_in_identity_n_preserves_prior_and_all_sources_without_secret_leaks(sdk,technical_case,tmp_path,caplog,previous,mode):
    evidence,_,_,_,_,root = technical_case
    caplog.set_level(logging.DEBUG)
    output_root = tmp_path / "technical"
    service = TechnicalExtractionService(OpenAITechnicalExtractor(),output_root)
    if previous: service.extract(evidence)
    before = snapshot(root)
    second = sdk.payloads[evidence.identities[1].identity_id]
    if mode == "wrong-identity": second["identity_id"] = "identity-9999"
    elif mode == "module-count": second["module_count"]["value"] = 2
    elif mode == "pane-count": second["modules"][0]["glass_panes"].pop()
    elif mode == "invalid": second["price"] = TEST_KEY
    elif mode == "api": sdk.error_on = len(sdk.requests)+2
    elif mode == "refusal": sdk.refusal = True
    elif mode == "incomplete": sdk.status = "incomplete"
    elif mode == "malformed": sdk.malformed = True
    elif mode == "connection": sdk.connection = True
    elif mode == "failed": sdk.status = "failed"
    with pytest.raises((InvalidTechnicalResponseError,TechnicalAPIError)) as caught:
        service.extract(evidence)
    assert snapshot(root) == before
    assert not list(output_root.rglob(".staging-*"))
    assert TEST_KEY not in "".join(traceback.format_exception(caught.value))
    assert TEST_KEY not in caplog.text
    assert "data:image" not in caplog.text
    assert "Native dimensional annotation" not in caplog.text
    assert "data:image" not in str(caught.value)


@pytest.mark.parametrize("kind,count",[("candidates",9),("candidates",24),("candidates",25),("pages",8),("pages",9)])
def test_per_identity_budgets_and_no_truncation(sdk,technical_case,kind,count):
    evidence = technical_case[0].model_copy(deep=True)
    evidence.identities = [evidence.identities[0]]
    evidence.unresolved_groups = []
    target = evidence.identities[0]
    c,p = target.candidates[0],target.pages[0]
    target.candidates = [c.model_copy(update=dict(covered_candidate_id=f"covered-{i:04d}",page_number=i if kind == "pages" else p.page_number)) for i in range(1,count+1)]
    target.candidate_ids = [c.covered_candidate_id for c in target.candidates]
    target.pages = [p.model_copy(update=dict(page_number=i)) for i in range(1,count+1)] if kind == "pages" else [p]
    if count > (24 if kind == "candidates" else 8):
        with pytest.raises(TechnicalContextBudgetExceeded): OpenAITechnicalExtractor().extract(evidence)
        assert sdk.requests == []
    else:
        result = OpenAITechnicalExtractor().extract(evidence)
        assert len(result.structures) == 1 and len(sdk.requests) == 1
        metadata = json.loads(sdk.requests[0]["input"][0]["content"][0]["text"])
        assert len(metadata["candidates"]) == count
        assert sum(json.loads(i["text"]).get("kind") == "identity_page_context" for i in sdk.requests[0]["input"][0]["content"] if i["type"] == "input_text") == (count if kind == "pages" else 1)


@pytest.mark.parametrize("over",[False,True])
def test_exact_metadata_budget_boundary_without_dropping_spans(sdk,technical_case,over):
    evidence = technical_case[0].model_copy(deep=True)
    evidence.identities = [evidence.identities[0]]
    extractor = OpenAITechnicalExtractor()
    extractor.extract(evidence)
    size = sum(len(item["text"].encode("utf-8")) for item in sdk.requests[0]["input"][0]["content"] if item["type"] == "input_text")
    sdk.requests.clear()
    evidence.identities[0].description += "x" * (openai_extractor.MAX_METADATA_BYTES_PER_IDENTITY-size+(1 if over else 0))
    if over:
        with pytest.raises(TechnicalContextBudgetExceeded): extractor.extract(evidence)
        assert sdk.requests == []
    else:
        assert len(extractor.extract(evidence).structures) == 1
        assert len(sdk.requests) == 1


@pytest.mark.parametrize("failure",[False,True])
def test_cli_only_technical_pipeline_and_client_closed(sdk,technical_case,tmp_path,monkeypatch,capsys,failure):
    from sng_consulter.technical import __main__ as command
    from sng_consulter.discovery.service import DiscoveryService
    from sng_consulter.identity.service import IdentityResolutionService
    from sng_consulter.identity.audit.service import IdentityAuditService
    from sng_consulter.identity.gate import IdentityGateService
    evidence,batch_id,_,_,_,root = technical_case
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(command,"build_technical_extraction_input",build)
    def forbidden(*args,**kwargs): pytest.fail("Technical CLI must not execute earlier phases")
    for cls,method in [(DiscoveryService,"discover"),(IdentityResolutionService,"resolve"),(IdentityAuditService,"audit"),(IdentityGateService,"gate")]:
        monkeypatch.setattr(cls,method,forbidden)
    if failure: sdk.error_on = 2
    before = snapshot(root)
    assert main([batch_id]) == (1 if failure else 0)
    assert sdk.client.is_closed()
    assert len(sdk.requests) == 2
    after = snapshot(root)
    assert all(after[path] == b for path,b in before.items())
    output = tmp_path / "runtime/technical" / batch_id / "technical_extraction.json"
    assert output.exists() is not failure
    captured = capsys.readouterr()
    if not failure:
        assert "Technical Extraction completed" in captured.out
    else:
        assert "TechnicalAPIError" in captured.err


def test_cli_blocked_gate_rejected_before_client_creation(sdk,technical_case,monkeypatch,capsys):
    from sng_consulter.technical import __main__ as command
    _,batch_id,identity,_,_,_ = technical_case
    gate_path = identity.parent / "identity_gate.json"
    data = json.loads(gate_path.read_text())
    data["status"] = "BLOCKED_BY_IDENTITY_ISSUES"
    gate_path.write_text(json.dumps(data))
    monkeypatch.setattr(command,"build_technical_extraction_input",build)
    assert main([batch_id]) == 1
    assert sdk.constructors == [] and sdk.requests == []
    assert "READY_FOR_EXTRACTION" in capsys.readouterr().err

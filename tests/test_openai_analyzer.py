import base64
import json
import logging
import traceback
from types import SimpleNamespace

import httpx2
import pymupdf as fitz
import pytest
from openai import OpenAI
from PIL import Image

from sng_consulter.discovery import DiscoveryInput, DiscoveryService, PageDiscoveryInput
from sng_consulter.discovery import openai_analyzer
from sng_consulter.discovery.__main__ import main
from sng_consulter.discovery.openai_analyzer import (
    DEFAULT_MODEL, InvalidDiscoveryResponseError, OpenAIAnalyzerAPIError,
    OpenAIAnalyzerConfigurationError, OpenAIPageAnalyzer,
)
from sng_consulter.preparation import DocumentPreparationService
from sng_consulter.preparation.models import BoundingBox, TextSpan

TEST_KEY = "sk-test-only-not-a-real-key"


def proposal(**changes):
    data = dict(reference_hint=None, description="Plausible glazed opening",
                region=dict(x0=0.1, y0=0.2, x1=0.4, y1=0.6),
                glass_relevance="POSSIBLE", evidence_kind="VISUAL")
    data.update(changes)
    return data


def structured_response(data, *, status="completed", refusal=False):
    content = ([dict(type="refusal", refusal="Cannot analyze")]
               if refusal else [dict(type="output_text", text=json.dumps(data), annotations=[])])
    return dict(id="resp_synthetic", object="response", created_at=0, status=status,
                model=DEFAULT_MODEL, error=None, incomplete_details=None,
                output=[dict(id="msg_synthetic", type="message", role="assistant",
                             status="completed", content=content)])


@pytest.fixture
def sdk(monkeypatch):
    """Use the actual SDK/parser; fake only its outbound HTTP boundary."""
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    monkeypatch.delenv("SNG_DISCOVERY_MODEL", raising=False)
    state = SimpleNamespace(requests=[], constructor_calls=[], payload={"proposals": [proposal()]},
                            status="completed", refusal=False, error_on=None, malformed_text=False,
                            connection_failure=False)

    def handle(request):
        state.requests.append(json.loads(request.content))
        assert request.url.path == "/v1/responses"
        assert request.headers["authorization"] == f"Bearer {TEST_KEY}"
        if state.connection_failure:
            raise httpx2.ConnectError(TEST_KEY, request=request)
        if len(state.requests) == state.error_on:
            return httpx2.Response(401, json={"error": {"message": TEST_KEY, "type": "invalid_api_key"}})
        body = structured_response(state.payload, status=state.status, refusal=state.refusal)
        if state.malformed_text:
            body["output"][0]["content"][0]["text"] = "{broken"
        return httpx2.Response(200, json=body)

    client = OpenAI(api_key=TEST_KEY, base_url="https://api.openai.com/v1", max_retries=0,
                    http_client=httpx2.Client(transport=httpx2.MockTransport(handle)))

    def create_client(**kwargs):
        state.constructor_calls.append(kwargs)
        return client

    state.client = client
    monkeypatch.setattr(openai_analyzer, "OpenAI", create_client)
    yield state
    if not client.is_closed():
        client.close()


@pytest.fixture
def page(tmp_path):
    path = tmp_path / "prepared.webp"
    with Image.new("RGB", (80, 120), "white") as image:
        image.save(path, format="WEBP")
    return PageDiscoveryInput(document_id="a" * 16, occurrence=0, page_number=1,
                              width_px=80, height_px=120, image_path=path, native_text_spans=[])


def test_complete_prepared_webp_and_strict_schema_are_sent_without_native_text(sdk, page):
    before = page.image_path.read_bytes()
    result = OpenAIPageAnalyzer().discover(page)
    assert len(result) == 1
    assert result[0].reference_hint is None
    request = sdk.requests[0]
    assert request["model"] == DEFAULT_MODEL
    assert request["store"] is False
    content = request["input"][0]["content"]
    assert content[0]["type"] == "input_image"
    assert content[0]["detail"] == "high"
    prefix, encoded = content[0]["image_url"].split(",", 1)
    assert prefix == "data:image/webp;base64"
    assert base64.b64decode(encoded) == before
    assert json.loads(content[1]["text"]) == {"render_size": [80, 120], "native_text_spans": []}
    assert page.image_path.read_bytes() == before
    assert page.document_id not in json.dumps(request)
    assert page.image_path.name not in json.dumps(request)
    assert "Prioritize recall" in request["instructions"]
    assert "RAW PDF" in request["instructions"]
    schema = request["text"]["format"]
    assert schema["type"] == "json_schema"
    assert schema["strict"] is True
    assert schema["schema"]["additionalProperties"] is False
    assert schema["schema"]["required"] == ["proposals"]
    assert "candidate_id" not in schema["schema"]["$defs"]["CandidateProposal"]["properties"]


def test_native_evidence_is_compact_visual_text_bbox_only_and_keeps_raw_objects(sdk, page):
    page.native_text_spans = [TextSpan(text="Native glass reference", bbox=BoundingBox(
        x0=10.12345, y0=20, x1=40, y1=50), block_index=9, line_index=8, span_index=7)]
    before = page.model_dump()
    sdk.payload = {"proposals": [proposal(evidence_kind="COMBINED")]}
    analyzer = OpenAIPageAnalyzer()
    first = analyzer.discover(page)
    analyzer.discover(page)
    evidence = sdk.requests[0]["input"][0]["content"][1]["text"]
    assert evidence == sdk.requests[1]["input"][0]["content"][1]["text"]
    assert json.loads(evidence)["native_text_spans"] == [["Native glass reference", [10.123, 20.0, 40.0, 50.0]]]
    assert "block_index" not in evidence and "line_index" not in evidence and "span_index" not in evidence
    assert page.model_dump() == before
    assert first[0].evidence_kind == "COMBINED"


def test_multiple_proposals_same_reference_and_null_are_preserved(sdk, page):
    sdk.payload = {"proposals": [proposal(reference_hint="provisional"), proposal(reference_hint="provisional"), proposal()]}
    result = OpenAIPageAnalyzer().discover(page)
    assert [item.reference_hint for item in result] == ["provisional", "provisional", None]


def test_valid_empty_proposals_remains_empty(sdk, page):
    sdk.payload = {"proposals": []}
    assert OpenAIPageAnalyzer().discover(page) == []


@pytest.mark.parametrize("data", [
    {"proposals": [proposal(region=dict(x0=-0.1, y0=0, x1=1, y1=1))]},
    {"proposals": [proposal(region=dict(x0=0.8, y0=0, x1=0.1, y1=1))]},
    {"proposals": [proposal(glass_relevance="UNKNOWN")]},
    {"proposals": [proposal(quantity=2)]}, {"proposals": [proposal(candidate_id="model-id")]},
    {"proposals": "not a list"}, {}, [],
])
def test_invalid_structured_response_fails_without_repair(sdk, page, data):
    sdk.payload = data
    with pytest.raises(InvalidDiscoveryResponseError):
        OpenAIPageAnalyzer().discover(page)


@pytest.mark.parametrize("mode", ["refusal", "incomplete", "malformed"])
def test_refusal_incomplete_or_non_json_output_fails(sdk, page, mode):
    sdk.refusal = mode == "refusal"
    sdk.status = "incomplete" if mode == "incomplete" else "completed"
    sdk.malformed_text = mode == "malformed"
    with pytest.raises(InvalidDiscoveryResponseError):
        OpenAIPageAnalyzer().discover(page)


def test_model_from_environment_and_detail_are_replaceable(sdk, page, monkeypatch):
    monkeypatch.setenv("SNG_DISCOVERY_MODEL", "replacement-model")
    analyzer = OpenAIPageAnalyzer(image_detail="original")
    analyzer.discover(page)
    assert sdk.requests[0]["model"] == "replacement-model"
    assert sdk.requests[0]["input"][0]["content"][0]["detail"] == "original"
    assert sdk.constructor_calls[0]["max_retries"] == 0
    assert sdk.constructor_calls[0]["timeout"] == 180.0


@pytest.mark.parametrize("key", [None, "", "   "])
def test_missing_api_key_is_configuration_error(sdk, monkeypatch, key):
    if key is None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENAI_API_KEY", key)
    with pytest.raises(OpenAIAnalyzerConfigurationError, match="OPENAI_API_KEY"):
        OpenAIPageAnalyzer()
    assert sdk.constructor_calls == []
    assert sdk.requests == []


def test_invalid_configuration_does_not_call_sdk(sdk, monkeypatch):
    monkeypatch.setenv("SNG_DISCOVERY_MODEL", "  ")
    with pytest.raises(OpenAIAnalyzerConfigurationError):
        OpenAIPageAnalyzer()
    with pytest.raises(OpenAIAnalyzerConfigurationError):
        OpenAIPageAnalyzer(model=DEFAULT_MODEL, image_detail="low")
    assert sdk.requests == []


def test_failed_api_response_and_connection_error_are_distinct_from_invalid_output(sdk, page):
    sdk.status = "failed"
    with pytest.raises(OpenAIAnalyzerAPIError, match="failed response"):
        OpenAIPageAnalyzer().discover(page)
    sdk.connection_failure = True
    with pytest.raises(OpenAIAnalyzerAPIError, match="APIConnectionError") as caught:
        OpenAIPageAnalyzer().discover(page)
    assert TEST_KEY not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("previous", [False, True])
def test_api_failure_keeps_old_discovery_and_does_not_expose_api_key(sdk, page, tmp_path, caplog, previous):
    caplog.set_level(logging.DEBUG)
    second = page.model_copy(update={"page_number": 2})
    evidence = DiscoveryInput(batch_id="b" * 32, pages=[page, second])
    service = DiscoveryService(OpenAIPageAnalyzer(), tmp_path / "discovery")
    result_path = tmp_path / "discovery" / evidence.batch_id / "discovery.json"
    if previous:
        service.discover(evidence)
    before = result_path.read_bytes() if previous else None
    sdk.error_on = len(sdk.requests) + 2
    with pytest.raises(OpenAIAnalyzerAPIError) as caught:
        service.discover(evidence)
    assert "HTTP 401" in str(caught.value)
    assert TEST_KEY not in "".join(traceback.format_exception(caught.value))
    assert TEST_KEY not in caplog.text
    assert (result_path.read_bytes() if result_path.exists() else None) == before
    assert not list(tmp_path.rglob(".staging-*"))
    assert all(TEST_KEY.encode() not in path.read_bytes() for path in tmp_path.rglob("*") if path.is_file())


def test_invalid_response_does_not_publish_discovery(sdk, page, tmp_path):
    sdk.payload = {"proposals": [proposal(region=dict(x0=1, y0=0, x1=0, y1=1))]}
    evidence = DiscoveryInput(batch_id="b" * 32, pages=[page])
    with pytest.raises(InvalidDiscoveryResponseError):
        DiscoveryService(OpenAIPageAnalyzer(), tmp_path / "discovery").discover(evidence)
    assert not (tmp_path / "discovery" / evidence.batch_id / "discovery.json").exists()


def test_manual_command_runs_existing_pipeline_with_sdk_boundary_only(sdk, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "synthetic.pdf"
    with fitz.open() as pdf:
        page = pdf.new_page(width=40, height=60)
        page.insert_text((5, 15), "Native text", fontsize=5)
        pdf.save(source)
    prepared = DocumentPreparationService(render_dpi=72).prepare(source, tmp_path / "runtime/review/prepared")
    batch_id = "c" * 32
    directory = tmp_path / "runtime/review/batches" / batch_id
    directory.mkdir(parents=True)
    manifest = directory / "manifest.json"
    manifest.write_text(json.dumps({"batch_id": batch_id, "documents": [
        dict(document_id=prepared.document_id, original_name="synthetic.pdf", status="prepared"),
    ]}))
    before = {path: path.read_bytes() for path in (tmp_path / "runtime/review").rglob("*") if path.is_file()}
    assert main([batch_id]) == 0
    assert sdk.client.is_closed()
    assert len(sdk.requests) == 1
    output = tmp_path / "runtime/discovery" / batch_id / "discovery.json"
    data = json.loads(output.read_text())
    assert data["candidates"][0]["candidate_id"] == "candidate-0001"
    assert all(path.read_bytes() == content for path, content in before.items())
    captured = capsys.readouterr()
    assert "1 pages, 1 candidates" in captured.out
    assert TEST_KEY not in captured.out + captured.err + output.read_text()

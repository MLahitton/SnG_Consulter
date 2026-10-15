import base64
import json
import logging
import traceback
from io import BytesIO

import pytest
from PIL import Image, ImageChops, ImageDraw

from test_openai_analyzer import sdk, TEST_KEY
from test_identity import artifacts, batch
from sng_consulter.identity import IdentityInput, IdentityResolutionProposal, IdentityResolutionService
from sng_consulter.identity.service import InvalidIdentityResolutionError
from sng_consulter.identity import openai_resolver
from sng_consulter.identity.openai_resolver import (
    OpenAIIdentityResolver, OpenAIIdentityConfigurationError, OpenAIIdentityAPIError,
    InvalidIdentityResponseError, IdentityContextBudgetExceeded, MAX_CANDIDATES,
)
from sng_consulter.identity.visuals import (
    candidate_crop, build_contact_sheets, CANDIDATES_PER_SHEET, TILE_WIDTH, IMAGE_HEIGHT, LABEL_HEIGHT,
)
from sng_consulter.identity.__main__ import main


def group(ids, reference=None):
    return dict(candidate_ids=ids, reference_hint=reference, description="Provisional design", reason="Visual correspondence")


def proposal(evidence):
    ids = [c.covered_candidate_id for c in evidence.candidates]
    return dict(groups=[group(ids[:1]), group(ids[1:2]), group(ids[2:4])],
                unresolved=[dict(candidate_ids=ids[4:], reason="Compound or uncertain association")])


@pytest.fixture
def evidence(tmp_path):
    path = tmp_path / "sensitive-local-name.webp"
    with Image.new("RGB", (400, 200), "red") as image:
        ImageDraw.Draw(image).rectangle((200, 0, 399, 199), fill="blue")
        image.save(path, format="WEBP", lossless=True)
    return IdentityInput(batch_id="a" * 32, source_covered_discovery_sha256="b" * 64, candidates=[dict(
        covered_candidate_id=f"covered-{i:04d}", origin="DISCOVERY", source_candidate_id=f"candidate-{i:04d}",
        reference_hint=["V3", "V3", "VENTANA 1", "ALZADO VENTANA 1", None, "V2 y V3", None, None][i-1],
        description="Possible architectural observation", glass_relevance="POSSIBLE",
        document_id="c" * 16, occurrence=(i-1)//4, page_number=1,
        region=dict(x0=0.05, y0=0.2, x1=0.4, y1=0.8), width_px=400, height_px=200, image_path=path,
    ) for i in range(1, 9)])


@pytest.fixture
def identity_sdk(sdk, monkeypatch, evidence):
    monkeypatch.delenv("SNG_IDENTITY_MODEL", raising=False)
    def factory(**kwargs):
        sdk.constructor_calls.append(kwargs)
        return sdk.client
    monkeypatch.setattr(openai_resolver, "OpenAI", factory)
    sdk.payload = proposal(evidence)
    return sdk


@pytest.mark.parametrize("key", [None, "", "   "])
def test_missing_key_fails_before_sdk(identity_sdk, monkeypatch, key):
    if key is None: monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    else: monkeypatch.setenv("OPENAI_API_KEY", key)
    with pytest.raises(OpenAIIdentityConfigurationError, match="OPENAI_API_KEY"):
        OpenAIIdentityResolver()
    assert identity_sdk.constructor_calls == []
    assert identity_sdk.requests == []


def test_independent_model_configuration_and_explicit_transport(identity_sdk, monkeypatch, evidence):
    monkeypatch.setenv("SNG_DISCOVERY_MODEL", "must-not-be-used")
    resolver = OpenAIIdentityResolver()
    resolver.resolve(evidence)
    assert identity_sdk.requests[-1]["model"] == "gpt-6.1-sol"
    monkeypatch.setenv("SNG_IDENTITY_MODEL", "configured-identity-model")
    OpenAIIdentityResolver().resolve(evidence)
    assert identity_sdk.requests[-1]["model"] == "configured-identity-model"
    assert identity_sdk.constructor_calls[0] == dict(api_key=TEST_KEY, max_retries=0, timeout=180.0)
    monkeypatch.setenv("SNG_IDENTITY_MODEL", " ")
    with pytest.raises(OpenAIIdentityConfigurationError): OpenAIIdentityResolver()


def test_crop_region_converter_margin_and_edges(evidence, monkeypatch):
    from sng_consulter.identity import visuals
    original = visuals.normalized_region_to_render_bbox
    calls = []
    def convert(*args):
        calls.append(args)
        return original(*args)
    monkeypatch.setattr(visuals, "normalized_region_to_render_bbox", convert)
    candidate = evidence.candidates[0]
    with Image.open(candidate.image_path) as image:
        with candidate_crop(image, candidate) as crop:
            assert crop.size == (154, 132)
            assert crop.getextrema() == ((255,255), (0,0), (0,0))
        candidate.region = dict_region(x0=0, y0=0, x1=0.1, y1=0.1)
        with candidate_crop(image, candidate) as crop:
            assert crop.size == (42, 21)
        candidate.region = dict_region(x0=0.9, y0=0.9, x1=1, y1=1)
        with candidate_crop(image, candidate) as crop:
            assert crop.size == (42, 21)
            assert crop.getextrema() == ((0,0), (0,0), (255,255))
    assert len(calls) == 3


def dict_region(**values):
    from sng_consulter.discovery.models import NormalizedRegion
    return NormalizedRegion(**values)


def test_sheets_in_memory_deterministic_labels_positions_and_aspect_ratio(evidence, tmp_path, monkeypatch):
    from sng_consulter.identity import visuals
    original_text = visuals.ImageDraw.ImageDraw.text
    labels = []
    def track(draw, xy, text, *args, **kwargs):
        labels.append((xy, text))
        return original_text(draw, xy, text, *args, **kwargs)
    monkeypatch.setattr(visuals.ImageDraw.ImageDraw, "text", track)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    sheets = build_contact_sheets(evidence)
    assert len(sheets) == 2
    ids = [c.covered_candidate_id for c in evidence.candidates]
    assert [cid for sheet in sheets for cid in sheet.candidate_ids] == ids
    assert [label for _, label in labels] == ids
    assert labels[0][0] == (12, 4)
    assert labels[1][0] == (TILE_WIDTH + 12, 4)
    with Image.open(BytesIO(sheets[0].png_bytes)) as board:
        assert board.size == (2*TILE_WIDTH, 3*(LABEL_HEIGHT + IMAGE_HEIGHT))
        # Solid red crop retains its 154x132 aspect ratio and is centered below label.
        tile = board.crop((0, LABEL_HEIGHT, TILE_WIDTH, LABEL_HEIGHT + IMAGE_HEIGHT))
        bounds = ImageChops.difference(tile, Image.new("RGB", tile.size, "white")).getbbox()
        assert (bounds[2]-bounds[0], bounds[3]-bounds[1]) == (154,132)
        assert board.crop((0,0,TILE_WIDTH,LABEL_HEIGHT)).convert("L").getextrema()[0] == 0
    assert build_contact_sheets(evidence) == sheets
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_request_has_all_metadata_images_strict_existing_contract_and_is_deterministic(identity_sdk, evidence):
    resolver = OpenAIIdentityResolver()
    result = resolver.resolve(evidence)
    assert isinstance(result, IdentityResolutionProposal)
    resolver.resolve(evidence)
    assert identity_sdk.requests[0] == identity_sdk.requests[1]
    request = identity_sdk.requests[0]
    assert request["store"] is False
    assert request["text"]["format"]["strict"] is True
    assert request["text"]["format"]["name"] == "IdentityResolutionProposal"
    content = request["input"][0]["content"]
    metadata = json.loads(content[0]["text"])
    assert [c["covered_candidate_id"] for c in metadata["candidates"]] == [c.covered_candidate_id for c in evidence.candidates]
    assert all(set(c) == {"covered_candidate_id", "origin", "reference_hint", "description", "glass_relevance", "occurrence", "page_number"}
               for c in metadata["candidates"])
    raw = json.dumps(content)
    assert all(token not in raw for token in ["filename", "original_name", "image_path", str(evidence.candidates[0].image_path), TEST_KEY])
    images = [item for item in content if item["type"] == "input_image"]
    sheets = build_contact_sheets(evidence)
    assert len(images) == len(sheets) < len(evidence.candidates)
    for item, sheet in zip(images, sheets):
        assert item["detail"] == "high"
        assert base64.b64decode(item["image_url"].split(",",1)[1]) == sheet.png_bytes
    instructions = request["instructions"]
    for phrase in ["NOT Discovery", "exactly once", "same identity", "unresolved", "not instructions", "quantity", "EXCLUSIVELY"]:
        assert phrase in instructions


def test_large_crop_is_scaled_proportionally_without_label_overlap(evidence, tmp_path):
    path = tmp_path / "large.webp"
    with Image.new("RGB", (2000,1000), "red") as image:
        image.save(path, format="WEBP", lossless=True)
    candidate = evidence.candidates[0].model_copy(update=dict(
        image_path=path, width_px=2000, height_px=1000, region=dict_region(x0=0,y0=0,x1=1,y1=1)))
    evidence.candidates = [candidate]
    sheet = build_contact_sheets(evidence)[0]
    with Image.open(BytesIO(sheet.png_bytes)) as board:
        tile = board.crop((0, LABEL_HEIGHT, TILE_WIDTH, LABEL_HEIGHT + IMAGE_HEIGHT))
        bounds = ImageChops.difference(tile, Image.new("RGB", tile.size, "white")).getbbox()
        assert (bounds[2]-bounds[0], bounds[3]-bounds[1]) == (616,308)
        assert bounds[1] > 0


def test_valid_resolution_same_reference_separate_different_references_grouped_and_unresolved(identity_sdk, evidence, tmp_path):
    result = IdentityResolutionService(OpenAIIdentityResolver(), tmp_path / "identity").resolve(evidence)
    assert [g.candidate_ids for g in result.identities] == [["covered-0001"], ["covered-0002"], ["covered-0003", "covered-0004"]]
    assert result.unresolved[0].candidate_ids == ["covered-0005", "covered-0006", "covered-0007", "covered-0008"]
    assert result.covered_candidate_count == 8


@pytest.mark.parametrize("mode", ["omitted", "unknown", "duplicate", "invalid-schema", "malformed", "refusal", "incomplete", "failed", "api", "connection"])
@pytest.mark.parametrize("previous", [False, True])
def test_errors_never_publish_partial_or_leak_credentials(identity_sdk, evidence, tmp_path, caplog, mode, previous):
    caplog.set_level(logging.DEBUG)
    service = IdentityResolutionService(OpenAIIdentityResolver(), tmp_path / "identity")
    output = tmp_path / "identity" / evidence.batch_id / "identity.json"
    if previous: service.resolve(evidence)
    before = output.read_bytes() if previous else None
    sdk = identity_sdk
    if mode == "omitted": sdk.payload["unresolved"][0]["candidate_ids"].pop()
    elif mode == "unknown": sdk.payload["unresolved"][0]["candidate_ids"].append("covered-9999")
    elif mode == "duplicate": sdk.payload["groups"][0]["candidate_ids"].append("covered-0001")
    elif mode == "invalid-schema": sdk.payload["groups"][0]["quantity"] = TEST_KEY
    elif mode == "malformed": sdk.malformed_text = True
    elif mode == "refusal": sdk.refusal = True
    elif mode == "incomplete": sdk.status = "incomplete"
    elif mode == "failed": sdk.status = "failed"
    elif mode == "api": sdk.error_on = len(sdk.requests) + 1
    elif mode == "connection": sdk.connection_failure = True
    with pytest.raises((InvalidIdentityResponseError, OpenAIIdentityAPIError, InvalidIdentityResolutionError)) as caught:
        service.resolve(evidence)
    assert (output.read_bytes() if output.exists() else None) == before
    assert not list(tmp_path.rglob(".staging-*"))
    assert TEST_KEY not in "".join(traceback.format_exception(caught.value))
    assert TEST_KEY not in caplog.text


@pytest.mark.parametrize("count", [25, 51, 80, 81])
def test_operational_budget_never_truncates(identity_sdk, evidence, count):
    evidence.candidates = [evidence.candidates[0].model_copy(update={"covered_candidate_id": f"covered-{i:04d}"}) for i in range(1,count+1)]
    identity_sdk.payload = dict(groups=[], unresolved=[dict(candidate_ids=[c.covered_candidate_id for c in evidence.candidates], reason="Uncertain")])
    resolver = OpenAIIdentityResolver()
    if count > MAX_CANDIDATES:
        with pytest.raises(IdentityContextBudgetExceeded): resolver.resolve(evidence)
        assert identity_sdk.requests == []
    else:
        assert len(resolver.resolve(evidence).unresolved[0].candidate_ids) == count
        content = identity_sdk.requests[0]["input"][0]["content"]
        assert len(json.loads(content[0]["text"])["candidates"]) == count
        assert sum(item["type"] == "input_image" for item in content) == (count+CANDIDATES_PER_SHEET-1)//CANDIDATES_PER_SHEET


def test_metadata_budget_fails_before_reading_images_or_request(identity_sdk, evidence, monkeypatch):
    evidence.candidates[0].description = "x" * 120_001
    def forbidden(*args): pytest.fail("Over-budget input must not build sheets")
    monkeypatch.setattr(openai_resolver, "build_contact_sheets", forbidden)
    with pytest.raises(IdentityContextBudgetExceeded): OpenAIIdentityResolver().resolve(evidence)
    assert identity_sdk.requests == []


def test_cli_real_pipeline_preserves_artifacts_and_closes_sdk(identity_sdk, artifacts, tmp_path, monkeypatch, capsys):
    from sng_consulter.identity import __main__ as command
    from sng_consulter.identity import build_identity_input
    evidence, source, _, batch_root, prepared_root = artifacts
    identity_sdk.payload = dict(groups=[group([c.covered_candidate_id for c in evidence.candidates])], unresolved=[])
    # CLI uses real assembly/service; only roots differ from their runtime defaults.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(command, "build_identity_input", lambda batch_id: build_identity_input(
        batch_id, source.parent.parent, batch_root, prepared_root))
    before = {p: p.read_bytes() for root in (source.parent.parent, batch_root, prepared_root) for p in root.rglob("*") if p.is_file()}
    assert main([evidence.batch_id]) == 0
    assert identity_sdk.client.is_closed()
    assert len(identity_sdk.requests) == 1
    assert all(p.read_bytes() == content for p, content in before.items())
    output = tmp_path / "runtime/identity" / evidence.batch_id / "identity.json"
    assert json.loads(output.read_text())["covered_candidate_count"] == len(evidence.candidates)
    assert "Identity completed" in capsys.readouterr().out
    source.unlink()
    identity_sdk.requests.clear()
    assert main([evidence.batch_id]) == 1
    assert identity_sdk.requests == []
    assert "FileNotFoundError" in capsys.readouterr().err

import json
from io import BytesIO
from pathlib import Path

import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from sng_consulter.api import app
from sng_consulter.discovery import DiscoveryCandidate, DiscoveryEvidence, DiscoveryResult, NormalizedRegion
from sng_consulter.preparation import DocumentPreparationService
from sng_consulter.review import routes


def snapshot(root):
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.fixture
def review(tmp_path, monkeypatch):
    for variable, directory in [("BATCH_DIR", "batches"), ("PREPARED_DIR", "prepared"),
                                ("DISCOVERY_DIR", "discovery")]:
        monkeypatch.setattr(routes, variable, tmp_path / directory)
    source = tmp_path / "synthetic.pdf"
    with fitz.open() as pdf:
        pdf.new_page(width=200, height=160)
        pdf.new_page(width=200, height=160)
        pdf.save(source)
    document = DocumentPreparationService(render_dpi=72).prepare(source, routes.PREPARED_DIR)
    batch_id = "a" * 32
    batch_dir = routes.BATCH_DIR / batch_id
    batch_dir.mkdir(parents=True)
    (batch_dir / "manifest.json").write_text(json.dumps({"batch_id": batch_id, "documents": [
        dict(document_id=document.document_id, original_name="first.pdf", status="prepared"),
        dict(document_id=None, original_name="invalid.pdf", status="error", status_code=400, error="invalid"),
        dict(document_id=document.document_id, original_name="second.pdf", status="prepared"),
    ]}))
    candidates = []
    for index, (occurrence, page, relevance, hint, region) in enumerate([
        (0, 1, "LIKELY", "provisional", (0.1, 0.25, 0.4, 0.5)),
        (0, 1, "POSSIBLE", None, (0.5, 0.65, 0.9, 0.9)),
        (0, 2, "LIKELY", "other page", (0.1, 0.25, 0.4, 0.5)),
        (2, 1, "POSSIBLE", "other occurrence", (0.1, 0.25, 0.4, 0.5)),
    ], start=1):
        candidates.append(DiscoveryCandidate(candidate_id=f"candidate-{index:04d}", reference_hint=hint,
            description=f"Observation {index}", glass_relevance=relevance,
            evidence=DiscoveryEvidence(document_id=document.document_id, occurrence=occurrence,
                page_number=page, region=NormalizedRegion(**dict(zip(("x0", "y0", "x1", "y1"), region))),
                evidence_kind="VISUAL")))
    result = DiscoveryResult(batch_id=batch_id, candidates=candidates, pages_analyzed=4)
    discovery_dir = routes.DISCOVERY_DIR / batch_id
    discovery_dir.mkdir(parents=True)
    discovery_path = discovery_dir / "discovery.json"
    discovery_path.write_text(result.model_dump_json(), encoding="utf-8")
    with TestClient(app) as client:
        yield client, batch_id, document, discovery_path, tmp_path


def test_diagnostic_filters_occurrence_page_draws_expected_regions_and_changes_no_files(review, monkeypatch):
    client, batch_id, document, discovery_path, root = review
    before = snapshot(root)
    batch_url = f"/review/batches/{batch_id}"
    url = batch_url + "/discovery/0/1"
    summary = client.get(batch_url)
    assert summary.status_code == 200
    assert f'href="{url}"' in summary.text
    assert f'href="{batch_url}/discovery/0/2"' in summary.text
    assert f'href="{batch_url}/discovery/2/1"' in summary.text
    view = client.get(url)
    assert view.status_code == 200
    assert f'src="{url}/overlay"' in view.text
    assert "candidate-0001 · provisional · LIKELY" in view.text
    assert "candidate-0002 · POSSIBLE" in view.text
    assert "Observation 1" in view.text and "Observation 2" in view.text
    assert "candidate-0003" not in view.text and "candidate-0004" not in view.text
    assert "None" not in view.text
    assert f'/review/{document.document_id}/pages/1/image' in view.text
    convert = routes.normalized_region_to_render_bbox
    calls, labels = [], []
    text = ImageDraw.ImageDraw.multiline_text

    def track_conversion(region, width, height):
        calls.append((region, width, height))
        return convert(region, width, height)

    def track_text(draw, xy, label, **kwargs):
        labels.append(label)
        return text(draw, xy, label, **kwargs)

    monkeypatch.setattr(routes, "normalized_region_to_render_bbox", track_conversion)
    monkeypatch.setattr(ImageDraw.ImageDraw, "multiline_text", track_text)
    overlay = client.get(url + "/overlay")
    assert overlay.status_code == 200
    assert overlay.headers["content-type"] == "image/png"
    assert overlay.headers["cache-control"] == "no-store"
    with Image.open(BytesIO(overlay.content)) as image:
        assert image.size == (200, 160)
        assert image.getpixel((20, 60)) == (0, 128, 0)
        assert image.getpixel((80, 60)) == (0, 128, 0)
        assert image.getpixel((180, 130)) == (255, 140, 0)
        assert image.getpixel((190, 155)) == (255, 255, 255)
    assert len(calls) == 2
    assert [label.replace("\n", "") for label in labels] == ["candidate-0001 · provisional · LIKELY", "candidate-0002 · POSSIBLE"]
    for occurrence, page, included, excluded in [(0, 2, "candidate-0003", "candidate-0004"),
                                                (2, 1, "candidate-0004", "candidate-0001")]:
        other = client.get(f"{batch_url}/discovery/{occurrence}/{page}")
        assert included in other.text and excluded not in other.text
    assert snapshot(root) == before


@pytest.mark.parametrize("changes", [
    {"batch_id": "b" * 32}, {"occurrence": 9}, {"occurrence": 1},
    {"document_id": "0" * 16}, {"page_number": 9},
])
def test_invalid_discovery_references_fail_cleanly(review, changes):
    client, batch_id, _, path, _ = review
    data = json.loads(path.read_text())
    if "batch_id" in changes:
        data.update(changes)
    else:
        data["candidates"][3]["evidence"].update(changes)
    path.write_text(json.dumps(data))
    # Invalid evidence on another occurrence is still rejected globally.
    url = f"/review/batches/{batch_id}/discovery/0/1"
    assert client.get(url).status_code == 409
    assert client.get(url + "/overlay").status_code == 409


@pytest.mark.parametrize("content", ["{broken", "[]", "null"])
def test_malformed_discovery_is_contract_error(review, content):
    client, batch_id, _, path, _ = review
    path.write_text(content)
    assert client.get(f"/review/batches/{batch_id}/discovery/0/1").status_code == 409


def test_missing_discovery_batch_or_selected_page_is_clear(review):
    client, batch_id, _, path, _ = review
    base = f"/review/batches/{batch_id}"
    assert client.get(base + "/discovery/0/9").status_code == 404
    assert client.get(base + "/discovery/1/1").status_code == 404
    assert client.get(base + "/discovery/2/2").status_code == 404
    assert client.get("/review/batches/" + "0" * 32 + "/discovery/0/1").status_code == 404
    path.unlink()
    missing = client.get(base + "/discovery/0/1")
    assert missing.status_code == 404
    assert "Discovery no existe" in missing.json()["detail"]
    batch = client.get(base)
    assert batch.status_code == 200
    assert "No hay un resultado de Discovery" in batch.text


def test_unsafe_prepared_image_path_and_document_id_are_rejected(review):
    client, batch_id, document, path, root = review
    outside = root / "outside.webp"
    Image.new("RGB", (200, 160), "red").save(outside, format="WEBP")
    manifest = document.output_dir / "manifest.json"
    data = json.loads(manifest.read_text())
    data["pages"][0]["image_path"] = str(outside)
    manifest.write_text(json.dumps(data))
    url = f"/review/batches/{batch_id}/discovery/0/1"
    assert client.get(url + "/overlay").status_code == 404
    result = json.loads(path.read_text())
    result["candidates"][0]["evidence"]["document_id"] = "../../outside"
    path.write_text(json.dumps(result))
    assert client.get(url).status_code == 409
    assert client.get("/review/batches/invalid/discovery/0/1").status_code == 404


def test_resolved_discovery_path_outside_root_is_rejected(review, monkeypatch):
    client, batch_id, _, path, root = review
    outside = root / "outside.json"
    outside.write_bytes(path.read_bytes())
    resolve = Path.resolve

    def escaped(candidate, *args, **kwargs):
        if candidate == path:
            return resolve(outside)
        return resolve(candidate, *args, **kwargs)

    # Deterministically exercise symlink resolution without Windows symlink privileges.
    monkeypatch.setattr(Path, "resolve", escaped)
    assert client.get(f"/review/batches/{batch_id}/discovery/0/1/overlay").status_code == 404


def test_invalid_region_duplicate_ids_and_html_escaping(review):
    client, batch_id, _, path, _ = review
    data = json.loads(path.read_text())
    data["candidates"][0]["reference_hint"] = "<script>alert(1)</script>"
    data["candidates"][0]["description"] = "<b>untrusted</b>"
    path.write_text(json.dumps(data))
    url = f"/review/batches/{batch_id}/discovery/0/1"
    html = client.get(url).text
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "<b>untrusted</b>" not in html and "&lt;b&gt;untrusted&lt;/b&gt;" in html
    data["candidates"][1]["candidate_id"] = data["candidates"][0]["candidate_id"]
    path.write_text(json.dumps(data))
    assert client.get(url).status_code == 409
    data["candidates"][1]["candidate_id"] = "candidate-0002"
    data["candidates"][0]["evidence"]["region"]["x0"] = -0.1
    path.write_text(json.dumps(data))
    assert client.get(url).status_code == 409

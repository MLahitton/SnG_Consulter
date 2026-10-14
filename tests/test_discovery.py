import json
from pathlib import Path

import pymupdf as fitz
import pytest
from PIL import Image
from pydantic import ValidationError

from sng_consulter.discovery import (
    CandidateProposal, DiscoveryResult, DiscoveryService, NormalizedRegion,
    build_discovery_input, normalized_region_to_render_bbox, render_bbox_to_normalized_region,
)
from sng_consulter.preparation import DocumentPreparationService, source_bbox_to_render_pixels
from sng_consulter.preparation.models import BoundingBox
from sng_consulter.storage import IncompatiblePreparedDocumentError


def snapshot(root):
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.fixture
def batch(tmp_path):
    prepared_root = tmp_path / "prepared"
    batch_root = tmp_path / "batches"
    source = tmp_path / "input.pdf"
    with fitz.open() as pdf:
        page = pdf.new_page(width=100, height=140)
        page.insert_text((20, 30), "Native text", fontsize=8)
        page.insert_text((20, 30), "Native text", fontsize=8)
        page.set_rotation(90)
        pdf.new_page(width=100, height=140)
        pdf.save(source)
    service = DocumentPreparationService(render_dpi=72)
    document = service.prepare(source, prepared_root)
    image_source = tmp_path / "input.png"
    Image.new("RGB", (40, 60), "white").save(image_source)
    image = service.prepare(image_source, prepared_root)
    batch_id = "a" * 32
    directory = batch_root / batch_id
    directory.mkdir(parents=True)
    data = {"batch_id": batch_id, "documents": [
        {"document_id": document.document_id, "original_name": "input.pdf", "status": "prepared"},
        {"document_id": None, "original_name": "invalid.pdf", "status": "error", "status_code": 400, "error": "invalid"},
        {"document_id": image.document_id, "original_name": "first.png", "status": "prepared"},
        {"document_id": image.document_id, "original_name": "second.png", "status": "prepared"},
    ]}
    (directory / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    return batch_id, batch_root, prepared_root, document, image


class FakeAnalyzer:
    def __init__(self, fail_on=None):
        self.pages = []
        self.fail_on = fail_on

    def discover(self, page):
        self.pages.append(page)
        if len(self.pages) == self.fail_on:
            raise RuntimeError("intentional analyzer failure")
        return [CandidateProposal(
            reference_hint="shared hint", description="Possible glass-related observation",
            region=NormalizedRegion(x0=0.1, y0=0.2, x1=0.4, y1=0.6),
            glass_relevance=relevance, evidence_kind="VISUAL",
        ) for relevance in ("LIKELY", "POSSIBLE")]


def test_batch_assembly_all_pages_occurrences_and_native_visual_text(batch, tmp_path):
    batch_id, batch_root, prepared_root, document, image = batch
    before = snapshot(batch_root), snapshot(prepared_root)
    evidence = build_discovery_input(batch_id, batch_root, prepared_root)
    assert [(page.occurrence, page.page_number) for page in evidence.pages] == [(0, 1), (0, 2), (2, 1), (3, 1)]
    assert evidence.pages[2].document_id == evidence.pages[3].document_id == image.document_id
    assert evidence.pages[2].image_path == evidence.pages[3].image_path
    assert all(page.image_path.is_file() for page in evidence.pages)
    raw = json.loads((document.output_dir / document.pages[0].text_path).read_text(encoding="utf-8"))
    assert len(raw) == 2
    assert len(evidence.pages[0].native_text_spans) == 1
    assert evidence.pages[0].native_text_spans[0].text == raw[0]["text"]
    expected = source_bbox_to_render_pixels(BoundingBox.model_validate(raw[0]["bbox"]), document.pages[0])
    assert evidence.pages[0].native_text_spans[0].bbox == expected
    assert all(not page.native_text_spans for page in evidence.pages[1:])
    assert all("source_name" not in type(page).model_fields and "original_name" not in type(page).model_fields for page in evidence.pages)
    analyzer = FakeAnalyzer()
    result = DiscoveryService(analyzer, tmp_path / "discovery").discover(evidence)
    assert [(page.occurrence, page.page_number) for page in analyzer.pages] == [(0, 1), (0, 2), (2, 1), (3, 1)]
    assert result.pages_analyzed == 4
    assert [candidate.candidate_id for candidate in result.candidates] == [f"candidate-{index:04d}" for index in range(1, 9)]
    assert len(result.candidates) == 8  # Same hint is not reconciled across pages or occurrences.
    assert all(candidate.reference_hint == "shared hint" for candidate in result.candidates)
    assert [(candidate.evidence.occurrence, candidate.evidence.page_number) for candidate in result.candidates] == [
        (0, 1), (0, 1), (0, 2), (0, 2), (2, 1), (2, 1), (3, 1), (3, 1),
    ]
    output = tmp_path / "discovery" / batch_id / "discovery.json"
    assert DiscoveryResult.model_validate_json(output.read_text(encoding="utf-8")) == result
    repeated = DiscoveryService(FakeAnalyzer(), tmp_path / "discovery").discover(evidence)
    assert repeated == result
    assert (snapshot(batch_root), snapshot(prepared_root)) == before


@pytest.mark.parametrize("bbox", [(0, 0, 400, 200), (100, 50, 300, 150), (1.25, 2.5, 10.5, 20.75)])
def test_normalized_regions_round_trip(bbox):
    original = BoundingBox(**dict(zip(("x0", "y0", "x1", "y1"), bbox)))
    before = original.model_dump()
    region = render_bbox_to_normalized_region(original, 400, 200)
    restored = normalized_region_to_render_bbox(region, 400, 200)
    assert tuple(restored.model_dump().values()) == pytest.approx(bbox)
    assert original.model_dump() == before
    if bbox == (0, 0, 400, 200):
        assert region == NormalizedRegion(x0=0, y0=0, x1=1, y1=1)


@pytest.mark.parametrize("changes", [
    {"x0": -0.1}, {"y1": 1.1}, {"x0": 0.8}, {"x1": 0.1},
    {"y0": 0.9}, {"y1": 0.2}, {"x1": float("nan")}, {"y0": float("inf")},
])
def test_invalid_normalized_regions(changes):
    values = dict(x0=0.1, y0=0.2, x1=0.8, y1=0.9)
    values.update(changes)
    with pytest.raises(ValidationError):
        NormalizedRegion(**values)


@pytest.mark.parametrize("dimensions", [(0, 200), (400, -1), (float("inf"), 200)])
def test_region_helpers_reject_invalid_dimensions(dimensions):
    bbox = BoundingBox(x0=0, y0=0, x1=10, y1=10)
    region = NormalizedRegion(x0=0, y0=0, x1=1, y1=1)
    with pytest.raises(ValueError):
        render_bbox_to_normalized_region(bbox, *dimensions)
    with pytest.raises(ValueError):
        normalized_region_to_render_bbox(region, *dimensions)


def test_pixel_bbox_outside_render_is_rejected_without_clipping():
    with pytest.raises(ValidationError):
        render_bbox_to_normalized_region(BoundingBox(x0=0, y0=0, x1=401, y1=200), 400, 200)


@pytest.mark.parametrize("previous", [False, True])
def test_analyzer_failure_leaves_no_partial_result_and_keeps_previous(batch, tmp_path, previous):
    batch_id, batch_root, prepared_root, _, _ = batch
    evidence = build_discovery_input(batch_id, batch_root, prepared_root)
    output = tmp_path / "discovery"
    if previous:
        DiscoveryService(FakeAnalyzer(), output).discover(evidence)
    before = snapshot(output)
    with pytest.raises(RuntimeError, match="intentional analyzer failure"):
        DiscoveryService(FakeAnalyzer(fail_on=2), output).discover(evidence)
    assert snapshot(output) == before
    assert not list(output.rglob(".staging-*"))


@pytest.mark.parametrize("stage", ["write", "publish"])
def test_persistence_failure_keeps_prior_result_and_cleans_temp(batch, tmp_path, monkeypatch, stage):
    batch_id, batch_root, prepared_root, _, _ = batch
    evidence = build_discovery_input(batch_id, batch_root, prepared_root)
    output = tmp_path / "discovery"
    DiscoveryService(FakeAnalyzer(), output).discover(evidence)
    before = snapshot(output)
    if stage == "write":
        write = Path.write_text

        def fail(path, data, **kwargs):
            if path.name == "discovery.json":
                write(path, "partial", **kwargs)
                raise OSError("intentional write failure")
            return write(path, data, **kwargs)

        monkeypatch.setattr(Path, "write_text", fail)
    else:
        def fail(*args, **kwargs):
            raise OSError("intentional publish failure")

        monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError, match="intentional"):
        DiscoveryService(FakeAnalyzer(), output).discover(evidence)
    assert snapshot(output) == before
    assert not list(output.rglob(".staging-*"))


def test_empty_batch_analyzes_zero_pages_without_fictitious_candidates(batch, tmp_path):
    batch_id, batch_root, prepared_root, _, _ = batch
    manifest = batch_root / batch_id / "manifest.json"
    data = json.loads(manifest.read_text())
    data["documents"] = [data["documents"][1]]
    manifest.write_text(json.dumps(data))
    evidence = build_discovery_input(batch_id, batch_root, prepared_root)
    analyzer = FakeAnalyzer()
    result = DiscoveryService(analyzer, tmp_path / "discovery").discover(evidence)
    assert result.pages_analyzed == 0
    assert result.candidates == []
    assert analyzer.pages == []


def test_assembly_rejects_legacy_contract_and_unsafe_paths(batch):
    batch_id, batch_root, prepared_root, document, _ = batch
    manifest = document.output_dir / "manifest.json"
    data = json.loads(manifest.read_text())
    del data["pages"][0]["source_to_visible_matrix"]
    manifest.write_text(json.dumps(data))
    with pytest.raises(IncompatiblePreparedDocumentError):
        build_discovery_input(batch_id, batch_root, prepared_root)
    with pytest.raises(FileNotFoundError):
        build_discovery_input("../invalid", batch_root, prepared_root)
    data = json.loads(manifest.read_text())
    data["pages"][0]["source_to_visible_matrix"] = [1, 0, 0, 1, 0, 0]
    data["pages"][0]["image_path"] = str(batch_root / batch_id / "manifest.json")
    manifest.write_text(json.dumps(data))
    with pytest.raises(FileNotFoundError):
        build_discovery_input(batch_id, batch_root, prepared_root)


def test_invalid_analyzer_proposal_does_not_publish_result(batch, tmp_path):
    batch_id, batch_root, prepared_root, _, _ = batch
    evidence = build_discovery_input(batch_id, batch_root, prepared_root)

    class InvalidAnalyzer:
        def discover(self, page):
            return [dict(description="Observation", reference_hint=None,
                         region=dict(x0=-0.1, y0=0, x1=1, y1=1),
                         glass_relevance="POSSIBLE", evidence_kind="VISUAL")]

    root = tmp_path / "discovery"
    with pytest.raises(ValidationError):
        DiscoveryService(InvalidAnalyzer(), root).discover(evidence)
    assert not (root / batch_id / "discovery.json").exists()


def test_batch_identity_and_successful_entry_are_validated(batch):
    batch_id, batch_root, prepared_root, _, _ = batch
    manifest = batch_root / batch_id / "manifest.json"
    data = json.loads(manifest.read_text())
    data["batch_id"] = "b" * 32
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="identity mismatch"):
        build_discovery_input(batch_id, batch_root, prepared_root)
    data["batch_id"] = batch_id
    data["documents"][0]["document_id"] = None
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValidationError):
        build_discovery_input(batch_id, batch_root, prepared_root)


def test_analyzer_cannot_supply_candidate_identity_or_technical_fields():
    proposal = dict(description="Observation", region=dict(x0=0, y0=0, x1=1, y1=1),
                    glass_relevance="POSSIBLE", evidence_kind="VISUAL")
    for forbidden in ("candidate_id", "quantity", "moduleCount", "price"):
        with pytest.raises(ValidationError):
            CandidateProposal(**proposal, **{forbidden: 1})

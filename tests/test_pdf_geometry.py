import json
from io import BytesIO
from pathlib import Path

import pymupdf as fitz
import pytest
from PIL import Image
from fastapi.testclient import TestClient

from sng_consulter.preparation import DocumentPreparationService, source_bbox_to_render_pixels
from sng_consulter.preparation.models import BoundingBox, PreparedDocument
from sng_consulter.api import app
from sng_consulter.review import routes


def synthetic_pdf(path: Path, rotation: int, cropped: bool, shifted: bool) -> None:
    with fitz.open() as pdf:
        page = pdf.new_page(width=240, height=180)
        if shifted:
            page.set_mediabox(fitz.Rect(30, 40, 270, 220))
        page.insert_text((70, 80), "TEST", fontsize=18)
        if cropped:
            page.set_cropbox(fitz.Rect(40, 30, 220, 160))
        page.set_rotation(rotation)
        pdf.save(path)


def ink_bounds(image: Image.Image) -> tuple:
    bounds = image.convert("L").point(lambda value: 255 if value < 100 else 0).getbbox()
    assert bounds is not None
    return bounds


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("cropped,shifted", [(False, False), (True, False), (False, True), (True, True)])
def test_pymupdf_raw_text_space_and_visible_render(tmp_path, rotation, cropped, shifted):
    source = tmp_path / "geometry.pdf"
    synthetic_pdf(source, rotation, cropped, shifted)
    with fitz.open(source) as pdf:
        page = pdf[0]
        raw = fitz.Rect(page.get_text("dict")["blocks"][0]["lines"][0]["spans"][0]["bbox"])
        with fitz.open(source) as comparison:
            comparison[0].set_rotation(0)
            unrotated = comparison[0].get_text("dict")["blocks"][0]["lines"][0]["spans"][0]["bbox"]
        assert tuple(raw) == pytest.approx(unrotated)
        pix = page.get_pixmap(alpha=False)
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        ink = ink_bounds(image)
        visible = raw * page.rotation_matrix
        assert visible.x0 <= ink[0] <= ink[2] <= visible.x1 + 1
        assert visible.y0 <= ink[1] <= ink[3] <= visible.y1 + 1
        assert page.rect.x0 == page.rect.y0 == 0
        if cropped:
            # PyMuPDF already localizes native text to the crop origin.
            assert raw.x0 == pytest.approx(70 - (page.cropbox.x0 - page.mediabox.x0))
        if rotation:
            assert tuple(raw) != pytest.approx(tuple(visible))


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("cropped,shifted", [(False, False), (True, False), (False, True), (True, True)])
def test_prepared_bbox_matches_render_and_preserves_raw(tmp_path, rotation, cropped, shifted):
    source = tmp_path / "geometry.pdf"
    synthetic_pdf(source, rotation, cropped, shifted)
    result = DocumentPreparationService().prepare(source, tmp_path / "out")
    persisted = PreparedDocument.model_validate_json((result.output_dir / "manifest.json").read_text(encoding="utf-8"))
    page = persisted.pages[0]
    raw = json.loads((result.output_dir / page.text_path).read_text(encoding="utf-8"))
    with fitz.open(source) as pdf:
        expected = pdf[0].get_text("dict")["blocks"][0]["lines"][0]["spans"][0]["bbox"]
        assert page.source_to_visible_matrix == tuple(pdf[0].rotation_matrix)
    assert tuple(raw[0]["bbox"].values()) == pytest.approx(expected)
    bbox = BoundingBox.model_validate(raw[0]["bbox"])
    before = bbox.model_dump()
    transformed = source_bbox_to_render_pixels(bbox, page)
    with Image.open(result.output_dir / page.image_path) as image:
        ink = ink_bounds(image)
    assert transformed.x0 <= ink[0] <= ink[2] <= transformed.x1 + 2
    assert transformed.y0 <= ink[1] <= ink[3] <= transformed.y1 + 2
    assert bbox.model_dump() == before


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_overlay_uses_correct_transformed_bbox_on_cropped_page(tmp_path, monkeypatch, rotation):
    source = tmp_path / "geometry.pdf"
    synthetic_pdf(source, rotation, cropped=True, shifted=True)
    monkeypatch.setattr(routes, "PREPARED_DIR", tmp_path / "prepared")
    result = DocumentPreparationService().prepare(source, routes.PREPARED_DIR)
    page = result.pages[0]
    raw = json.loads((result.output_dir / page.text_path).read_text(encoding="utf-8"))
    bbox = source_bbox_to_render_pixels(BoundingBox.model_validate(raw[0]["bbox"]), page)
    before = {path.relative_to(result.output_dir): path.read_bytes()
              for path in result.output_dir.rglob("*") if path.is_file()}
    with TestClient(app) as client:
        response = client.get(f"/review/{result.document_id}/page/1/bbox-overlay")
    assert response.status_code == 200
    with Image.open(BytesIO(response.content)) as image:
        assert image.size == (page.width_px, page.height_px)
        assert image.getpixel((int(bbox.x0), int(bbox.y0))) == (255, 0, 0)
    after = {path.relative_to(result.output_dir): path.read_bytes()
             for path in result.output_dir.rglob("*") if path.is_file()}
    assert before == after

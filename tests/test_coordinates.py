import pytest
from pydantic import ValidationError

from sng_consulter.preparation import PreparedPage, source_bbox_to_render_pixels
from sng_consulter.preparation.models import BoundingBox


def make_page(**overrides) -> PreparedPage:
    values = dict(
        page_number=1, width_px=1000, height_px=600,
        coordinate_width=400, coordinate_height=300, coordinate_unit="PDF_POINT",
        image_path="pages/0001.webp", text_path="text/0001.json",
        native_text_available=False, text_span_count=0,
    )
    values.update(overrides)
    return PreparedPage(**values)


@pytest.mark.parametrize("source,expected", [
    ((0, 0, 40, 30), (0, 0, 100, 60)),
    ((200, 150, 240, 180), (500, 300, 600, 360)),
    ((0, 0, 400, 300), (0, 0, 1000, 600)),
    ((1.25, 2.5, 3.75, 4.5), (3.125, 5, 9.375, 9)),
])
def test_bbox_conversion_uses_independent_axis_scales(source, expected):
    bbox = BoundingBox(**dict(zip(("x0", "y0", "x1", "y1"), source)))
    before = bbox.model_dump()
    result = source_bbox_to_render_pixels(bbox, make_page())
    assert tuple(result.model_dump().values()) == pytest.approx(expected)
    assert bbox.model_dump() == before
    assert result is not bbox


def test_image_pixel_coordinates_at_one_to_one_scale():
    page = make_page(coordinate_unit="PIXEL", coordinate_width=1000, coordinate_height=600)
    bbox = BoundingBox(x0=0, y0=0, x1=50, y1=20)
    assert source_bbox_to_render_pixels(bbox, page) == bbox


@pytest.mark.parametrize("field", ["width_px", "height_px", "coordinate_width", "coordinate_height"])
@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_dimensions_must_be_finite_and_positive(field, value):
    with pytest.raises(ValidationError):
        make_page(**{field: value})


@pytest.mark.parametrize("changes", [
    {"x0": -1}, {"y0": -1}, {"x1": float("inf")}, {"y1": float("nan")},
    {"x0": 30}, {"y0": 30},
])
def test_conversion_rejects_invalid_coordinates(changes):
    values = dict(x0=0, y0=0, x1=20, y1=20)
    values.update(changes)
    with pytest.raises(ValueError):
        source_bbox_to_render_pixels(BoundingBox(**values), make_page())

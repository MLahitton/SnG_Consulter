import json

import pytest
from PIL import Image, ImageDraw

from sng_consulter.preparation import DocumentPreparationService


@pytest.mark.parametrize("orientation,expected_corners", [
    (1, (0, 1, 2, 3)), (2, (1, 0, 3, 2)),
    (3, (3, 2, 1, 0)), (4, (2, 3, 0, 1)),
    (5, (0, 2, 1, 3)), (6, (2, 0, 3, 1)),
    (7, (3, 1, 2, 0)), (8, (1, 3, 0, 2)),
])
def test_jpeg_exif_orientation_normalizes_visible_pixels_without_changing_source(
    tmp_path, orientation, expected_corners
):
    source = tmp_path / "photo.jpg"
    colors = [(230, 20, 20), (20, 230, 20), (20, 20, 230), (230, 230, 20)]
    with Image.new("RGB", (80, 48)) as image:
        draw = ImageDraw.Draw(image)
        for box, color in zip([(0, 0, 39, 23), (40, 0, 79, 23),
                               (0, 24, 39, 47), (40, 24, 79, 47)], colors, strict=True):
            draw.rectangle(box, fill=color)
        exif = Image.Exif()
        exif[274] = orientation
        image.save(source, quality=100, subsampling=0, exif=exif)
    original_bytes = source.read_bytes()
    result = DocumentPreparationService().prepare(source, tmp_path / "out")
    page = result.pages[0]
    expected_size = (48, 80) if orientation >= 5 else (80, 48)
    assert (page.width_px, page.height_px) == expected_size
    assert (page.coordinate_width, page.coordinate_height) == expected_size
    assert page.coordinate_unit == "PIXEL"
    assert page.source_to_visible_matrix == (1, 0, 0, 1, 0, 0)
    manifest = json.loads((result.output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["preparation_contract_version"] == 1
    assert manifest["pages"][0]["source_to_visible_matrix"] == [1, 0, 0, 1, 0, 0]
    with Image.open(result.output_dir / page.image_path) as prepared:
        assert prepared.size == expected_size
        width, height = prepared.size
        for position, color_index in zip([(10, 10), (width - 11, 10),
                                           (10, height - 11), (width - 11, height - 11)],
                                          expected_corners, strict=True):
            actual = prepared.getpixel(position)
            assert max(abs(a - b) for a, b in zip(actual, colors[color_index], strict=True)) < 25
        assert prepared.getexif().get(274, 1) == 1
    assert source.read_bytes() == original_bytes
    assert (result.output_dir / result.source_path).read_bytes() == original_bytes
    assert (result.output_dir / page.text_path).read_text().strip() == "[]"

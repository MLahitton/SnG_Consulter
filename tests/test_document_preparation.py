from pathlib import Path

import fitz
from PIL import Image

from sng_consulter.preparation import DocumentPreparationService


def test_prepare_pdf_preserves_pages_and_native_text(tmp_path: Path) -> None:
    source = tmp_path / "sample.pdf"
    pdf = fitz.open()
    page1 = pdf.new_page(width=600, height=800)
    page1.insert_text((72, 72), "V-01 3000 x 2500")
    pdf.new_page(width=600, height=800)
    pdf.save(source)
    pdf.close()

    result = DocumentPreparationService(render_dpi=96).prepare(source, tmp_path / "out")

    assert result.page_count == 2
    assert result.pages[0].native_text_available is True
    assert result.pages[0].text_span_count >= 1
    assert result.pages[1].native_text_available is False

    document_dir = result.output_dir
    assert (document_dir / "manifest.json").is_file()
    assert (document_dir / result.pages[0].image_path).is_file()
    assert (document_dir / result.pages[1].image_path).is_file()
    assert (document_dir / result.pages[0].text_path).is_file()


def test_prepare_image_creates_single_visual_page_without_fake_text(tmp_path: Path) -> None:
    source = tmp_path / "sample.png"
    Image.new("RGB", (320, 180), "white").save(source)

    result = DocumentPreparationService().prepare(source, tmp_path / "out")

    assert result.page_count == 1
    assert result.pages[0].native_text_available is False
    assert result.pages[0].text_span_count == 0
    assert (result.output_dir / result.pages[0].image_path).suffix == ".webp"

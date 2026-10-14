from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

import pymupdf as fitz
from PIL import Image

from .models import (
    BoundingBox,
    CoordinateUnit,
    DocumentKind,
    PreparedDocument,
    PreparedPage,
    TextSpan,
)


class UnsupportedDocumentError(ValueError):
    pass


class DocumentPreparationService:
    """Phase 1 document preparation.

    The service preserves source semantics instead of trying to interpret
    architecture. OCR is deliberately outside this phase: a scanned PDF with no
    native text remains a visual page with native_text_available=False.
    """

    _IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

    def __init__(self, render_dpi: int = 180, webp_quality: int = 90) -> None:
        if render_dpi <= 0:
            raise ValueError("render_dpi must be positive")
        if not 1 <= webp_quality <= 100:
            raise ValueError("webp_quality must be between 1 and 100")
        self._render_dpi = render_dpi
        self._webp_quality = webp_quality

    def prepare(self, source: str | Path, output_root: str | Path) -> PreparedDocument:
        source_path = Path(source).resolve()
        output_root_path = Path(output_root).resolve()
        if not source_path.is_file():
            raise FileNotFoundError(source_path)

        sha256 = _sha256(source_path)
        document_id = sha256[:16]
        document_dir = output_root_path / document_id
        suffix = source_path.suffix.lower()
        if suffix != ".pdf" and suffix not in self._IMAGE_SUFFIXES:
            raise UnsupportedDocumentError(f"Unsupported document type: {suffix or '<no extension>'}")

        output_root_path.mkdir(parents=True, exist_ok=True)
        # Same filesystem as the final directory, so publication uses renames.
        with TemporaryDirectory(prefix=f".staging-{document_id}-", dir=output_root_path) as temporary:
            staging = Path(temporary)
            staged_document = staging / "document"
            source_dir = staged_document / "source"
            pages_dir = staged_document / "pages"
            text_dir = staged_document / "text"
            for directory in (source_dir, pages_dir, text_dir):
                directory.mkdir(parents=True)
            preserved_source = source_dir / source_path.name
            shutil.copy2(source_path, preserved_source)

            if suffix == ".pdf":
                result = self._prepare_pdf(source_path, document_id, sha256, staged_document,
                                           preserved_source, pages_dir, text_dir)
            else:
                result = self._prepare_image(source_path, document_id, sha256, staged_document,
                                             preserved_source, pages_dir, text_dir)
            result.output_dir = document_dir
            (staged_document / "manifest.json").write_text(
                result.model_dump_json(indent=2), encoding="utf-8"
            )

            # Keep the prior complete version until the new version is ready.
            # Outside staging so a failed rollback cannot discard the prior version.
            backup = output_root_path / f".previous-{staging.name}"
            had_previous = document_dir.exists()
            if had_previous:
                document_dir.rename(backup)
            try:
                staged_document.rename(document_dir)
            except BaseException:
                if had_previous:
                    backup.rename(document_dir)
                raise
            if had_previous:
                shutil.rmtree(backup)
        return result

    def _prepare_pdf(
        self,
        source: Path,
        document_id: str,
        sha256: str,
        document_dir: Path,
        preserved_source: Path,
        pages_dir: Path,
        text_dir: Path,
    ) -> PreparedDocument:
        pages: list[PreparedPage] = []
        zoom = self._render_dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)

        # Opening malformed PDFs by filename can leave a Windows file handle
        # alive in the exception traceback, preventing temporary upload cleanup.
        with fitz.open(stream=source.read_bytes(), filetype="pdf") as pdf:
            if pdf.page_count == 0:
                raise ValueError("PDF has no pages")

            for page_index in range(pdf.page_count):
                page_number = page_index + 1
                page = pdf.load_page(page_index)
                pix = page.get_pixmap(matrix=matrix, alpha=False)
                image_path = pages_dir / f"{page_number:04d}.webp"
                _save_pixmap_as_webp(pix, image_path, self._webp_quality)

                spans = _extract_native_text_spans(page)
                text_path = text_dir / f"{page_number:04d}.json"
                text_path.write_text(
                    json.dumps(
                        [span.model_dump(mode="json") for span in spans],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )

                pages.append(
                    PreparedPage(
                        page_number=page_number,
                        width_px=pix.width,
                        height_px=pix.height,
                        coordinate_width=page.rect.width,
                        coordinate_height=page.rect.height,
                        coordinate_unit=CoordinateUnit.PDF_POINT,
                        source_to_visible_matrix=tuple(page.rotation_matrix),
                        image_path=image_path.relative_to(document_dir),
                        text_path=text_path.relative_to(document_dir),
                        native_text_available=bool(spans),
                        text_span_count=len(spans),
                    )
                )

        return PreparedDocument(
            document_id=document_id,
            source_name=source.name,
            source_sha256=sha256,
            source_path=preserved_source.relative_to(document_dir),
            kind=DocumentKind.PDF,
            page_count=len(pages),
            output_dir=document_dir,
            pages=pages,
        )

    def _prepare_image(
        self,
        source: Path,
        document_id: str,
        sha256: str,
        document_dir: Path,
        preserved_source: Path,
        pages_dir: Path,
        text_dir: Path,
    ) -> PreparedDocument:
        image_path = pages_dir / "0001.webp"
        with Image.open(source) as image:
            rgb = image.convert("RGB")
            width, height = rgb.size
            rgb.save(image_path, format="WEBP", quality=self._webp_quality, method=6)

        text_path = text_dir / "0001.json"
        text_path.write_text("[]\n", encoding="utf-8")

        page = PreparedPage(
            page_number=1,
            width_px=width,
            height_px=height,
            coordinate_width=width,
            coordinate_height=height,
            coordinate_unit=CoordinateUnit.PIXEL,
            image_path=image_path.relative_to(document_dir),
            text_path=text_path.relative_to(document_dir),
            native_text_available=False,
            text_span_count=0,
        )
        return PreparedDocument(
            document_id=document_id,
            source_name=source.name,
            source_sha256=sha256,
            source_path=preserved_source.relative_to(document_dir),
            kind=DocumentKind.IMAGE,
            page_count=1,
            output_dir=document_dir,
            pages=[page],
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_native_text_spans(page: fitz.Page) -> list[TextSpan]:
    raw = page.get_text("dict")
    result: list[TextSpan] = []
    for block_index, block in enumerate(raw.get("blocks", [])):
        if block.get("type") != 0:
            continue
        for line_index, line in enumerate(block.get("lines", [])):
            for span_index, span in enumerate(line.get("spans", [])):
                text = str(span.get("text", "")).strip()
                bbox = span.get("bbox")
                if not text or not bbox or len(bbox) != 4:
                    continue
                result.append(
                    TextSpan(
                        text=text,
                        bbox=BoundingBox(
                            x0=float(bbox[0]),
                            y0=float(bbox[1]),
                            x1=float(bbox[2]),
                            y1=float(bbox[3]),
                        ),
                        block_index=block_index,
                        line_index=line_index,
                        span_index=span_index,
                    )
                )
    return result


def _save_pixmap_as_webp(pix: fitz.Pixmap, target: Path, quality: int) -> None:
    mode = "RGB" if pix.n < 4 else "RGBA"
    image = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
    if mode == "RGBA":
        image = image.convert("RGB")
    image.save(target, format="WEBP", quality=quality, method=6)

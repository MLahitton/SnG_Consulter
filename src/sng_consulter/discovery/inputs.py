"""Assemble prepared evidence and convert regions without changing Phase 1."""

import json
from math import isfinite
from pathlib import Path

from sng_consulter.preparation import deduplicate_text_spans, source_bbox_to_render_pixels
from sng_consulter.preparation.models import BoundingBox, TextSpan
from sng_consulter.storage import prepared_file, read_prepared_document, read_review_batch

from .models import DiscoveryInput, NormalizedRegion, PageDiscoveryInput


def _validate_dimensions(width_px: int, height_px: int) -> None:
    if any(not isfinite(value) or value <= 0 for value in (width_px, height_px)):
        raise ValueError("Render dimensions must be finite and positive")


def render_bbox_to_normalized_region(bbox: BoundingBox, width_px: int, height_px: int) -> NormalizedRegion:
    _validate_dimensions(width_px, height_px)
    return NormalizedRegion(x0=bbox.x0 / width_px, y0=bbox.y0 / height_px,
                            x1=bbox.x1 / width_px, y1=bbox.y1 / height_px)


def normalized_region_to_render_bbox(region: NormalizedRegion, width_px: int, height_px: int) -> BoundingBox:
    _validate_dimensions(width_px, height_px)
    region = NormalizedRegion.model_validate(region)
    return BoundingBox(x0=region.x0 * width_px, y0=region.y0 * height_px,
                       x1=region.x1 * width_px, y1=region.y1 * height_px)


def build_discovery_input(
    batch_id: str, batch_root: Path = Path("runtime/review/batches"),
    prepared_root: Path = Path("runtime/review/prepared"),
) -> DiscoveryInput:
    batch = read_review_batch(batch_root, batch_id)
    pages = []
    for occurrence, entry in enumerate(batch.documents):
        if entry.status == "error":
            continue
        directory, document = read_prepared_document(prepared_root, entry.document_id)
        for page in document.pages:
            image_path = prepared_file(prepared_root, directory, page.image_path)
            text_path = prepared_file(prepared_root, directory, page.text_path)
            raw = [TextSpan.model_validate(span) for span in json.loads(text_path.read_text(encoding="utf-8"))]
            native = [span.model_copy(update={"bbox": source_bbox_to_render_pixels(span.bbox, page)})
                      for span in deduplicate_text_spans(raw)]
            pages.append(PageDiscoveryInput(
                document_id=document.document_id, occurrence=occurrence, page_number=page.page_number,
                width_px=page.width_px, height_px=page.height_px, image_path=image_path,
                native_text_spans=native,
            ))
    return DiscoveryInput(batch_id=batch.batch_id, pages=pages)

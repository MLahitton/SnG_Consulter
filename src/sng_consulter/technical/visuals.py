"""Local and Identity/page context crops plus exact full pages; no persistence."""

from dataclasses import dataclass
from io import BytesIO
from math import ceil, floor

from PIL import Image, ImageDraw, ImageFont

from sng_consulter.identity.models import IdentityCandidateInput
from sng_consulter.identity.visuals import candidate_crop
from sng_consulter.discovery.inputs import normalized_region_to_render_bbox

from .models import TechnicalIdentityInput, TechnicalPageEvidence

CONTEXT_PAGE_MARGIN = 0.06
ANNOTATION_HORIZONTAL_MARGIN = 0.08
ANNOTATION_TOP_MARGIN = 0.06
ANNOTATION_BOTTOM_MARGIN = 0.12


def native_spans_payload(spans) -> list:
    return [[s.text, [s.bbox.x0, s.bbox.y0, s.bbox.x1, s.bbox.y1]] for s in spans]


def _identity_page_union_bbox(identity: TechnicalIdentityInput, page: TechnicalPageEvidence) -> tuple[float, float, float, float]:
    """Union only this Identity's evidence on this occurrence/page."""
    boxes = [normalized_region_to_render_bbox(c.region, page.width_px, page.height_px)
             for c in identity.candidates
             if (c.document_id, c.occurrence, c.page_number) ==
                (page.document_id, page.occurrence, page.page_number)]
    if not boxes:
        raise ValueError("Identity has no candidates on context page")
    return (min(b.x0 for b in boxes), min(b.y0 for b in boxes),
            max(b.x1 for b in boxes), max(b.y1 for b in boxes))


def _expanded_page_bounds(union_bbox, page: TechnicalPageEvidence, horizontal: float,
                          top: float, bottom: float) -> tuple[int, int, int, int]:
    left, y0, right, y1 = union_bbox
    return (max(0, floor(left - page.width_px * horizontal)),
            max(0, floor(y0 - page.height_px * top)),
            min(page.width_px, ceil(right + page.width_px * horizontal)),
            min(page.height_px, ceil(y1 + page.height_px * bottom)))


def identity_page_context_bounds(identity: TechnicalIdentityInput, page: TechnicalPageEvidence, *,
                                 union_bbox=None) -> tuple[int, int, int, int]:
    return _expanded_page_bounds(union_bbox if union_bbox is not None else _identity_page_union_bbox(identity, page), page,
                                 CONTEXT_PAGE_MARGIN, CONTEXT_PAGE_MARGIN, CONTEXT_PAGE_MARGIN)


def _page_context_image(page: TechnicalPageEvidence, bounds: tuple[int, int, int, int], kind: str) -> "TechnicalImage":
    left, top, right, bottom = bounds
    # Positive-area intersection; touching the boundary alone is not evidence inside.
    spans = [s for s in page.native_text_spans if
             s.bbox.x0 < right and s.bbox.x1 > left and s.bbox.y0 < bottom and s.bbox.y1 > top]
    with Image.open(page.image_path) as source:
        if source.size != (page.width_px, page.height_px):
            raise ValueError("Context image dimensions disagree with page evidence")
        with source.crop(bounds) as crop, BytesIO() as buffer:
            crop.save(buffer, format="PNG")
            metadata = dict(kind=kind, document_id=page.document_id,
                occurrence=page.occurrence, page_number=page.page_number,
                render_size=[page.width_px, page.height_px])
            context = dict(context_bbox=list(bounds), local_native_spans=native_spans_payload(spans))
            if kind == "associated_annotation_context":
                metadata["annotation_context"] = context
            else:
                metadata.update(context)
            return TechnicalImage(metadata, "image/png", buffer.getvalue())


@dataclass(frozen=True)
class TechnicalImage:
    metadata: dict
    mime_type: str
    data: bytes


def _labeled_crop(candidate: IdentityCandidateInput) -> bytes:
    font = ImageFont.load_default(size=20)
    label = candidate.covered_candidate_id
    label_width = int(font.getlength(label)) + 24
    with Image.open(candidate.image_path) as source, source.convert("RGB") as image:
        with candidate_crop(image,candidate) as crop:
            with Image.new("RGB",(max(crop.width,label_width),crop.height+32),"white") as board:
                ImageDraw.Draw(board).text((12,4),label,font=font,fill="black")
                board.paste(crop,((board.width-crop.width)//2,32))
                with BytesIO() as buffer:
                    board.save(buffer,format="PNG")
                    return buffer.getvalue()


def build_technical_images(identity: TechnicalIdentityInput) -> list[TechnicalImage]:
    images = []
    for candidate in identity.candidates:
        images.append(TechnicalImage(dict(kind="candidate_crop",covered_candidate_id=candidate.covered_candidate_id,
            document_id=candidate.document_id,occurrence=candidate.occurrence,page_number=candidate.page_number),
            "image/png",_labeled_crop(candidate)))
    for page in identity.pages:
        union_bbox = _identity_page_union_bbox(identity, page)
        bounds = identity_page_context_bounds(identity, page, union_bbox=union_bbox)
        images.append(_page_context_image(page, bounds, "identity_page_context"))
        annotation_bounds = _expanded_page_bounds(union_bbox, page, ANNOTATION_HORIZONTAL_MARGIN,
                                                  ANNOTATION_TOP_MARGIN, ANNOTATION_BOTTOM_MARGIN)
        images.append(_page_context_image(page, annotation_bounds, "associated_annotation_context"))
        images.append(TechnicalImage(dict(kind="full_page",document_id=page.document_id,
            occurrence=page.occurrence,page_number=page.page_number),"image/webp",page.image_path.read_bytes()))
    return images

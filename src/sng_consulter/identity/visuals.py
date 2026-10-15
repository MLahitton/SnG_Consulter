"""Deterministic contact sheets from prepared renders, entirely in memory."""

from dataclasses import dataclass
from io import BytesIO
from math import ceil, floor

from PIL import Image, ImageDraw, ImageFont

from sng_consulter.discovery.inputs import normalized_region_to_render_bbox

from .models import IdentityCandidateInput, IdentityInput

TILE_WIDTH = 640
IMAGE_HEIGHT = 480
LABEL_HEIGHT = 32
PADDING = 12
COLUMNS = 2
ROWS = 3
CANDIDATES_PER_SHEET = COLUMNS * ROWS
CONTEXT_MARGIN = 0.05


@dataclass(frozen=True)
class ContactSheet:
    candidate_ids: tuple[str, ...]
    png_bytes: bytes


def candidate_crop(image: Image.Image, candidate: IdentityCandidateInput) -> Image.Image:
    if image.size != (candidate.width_px, candidate.height_px):
        raise ValueError("Prepared render dimensions do not match Identity input")
    bbox = normalized_region_to_render_bbox(candidate.region, candidate.width_px, candidate.height_px)
    margin_x = (bbox.x1 - bbox.x0) * CONTEXT_MARGIN
    margin_y = (bbox.y1 - bbox.y0) * CONTEXT_MARGIN
    bounds = (max(0, floor(bbox.x0 - margin_x)), max(0, floor(bbox.y0 - margin_y)),
              min(image.width, ceil(bbox.x1 + margin_x)), min(image.height, ceil(bbox.y1 + margin_y)))
    return image.crop(bounds)


def build_contact_sheets(identity_input: IdentityInput) -> list[ContactSheet]:
    evidence = IdentityInput.model_validate(identity_input)
    sheets = []
    font = ImageFont.load_default(size=20)
    page = None
    page_path = None
    try:
        for start in range(0, len(evidence.candidates), CANDIDATES_PER_SHEET):
            candidates = evidence.candidates[start:start + CANDIDATES_PER_SHEET]
            rows = ceil(len(candidates) / COLUMNS)
            with Image.new("RGB", (COLUMNS * TILE_WIDTH, rows * (LABEL_HEIGHT + IMAGE_HEIGHT)), "white") as board:
                draw = ImageDraw.Draw(board)
                for index, candidate in enumerate(candidates):
                    if candidate.image_path != page_path:
                        if page is not None:
                            page.close()
                        with Image.open(candidate.image_path) as source:
                            page = source.convert("RGB")
                        page_path = candidate.image_path
                    x = (index % COLUMNS) * TILE_WIDTH
                    y = (index // COLUMNS) * (LABEL_HEIGHT + IMAGE_HEIGHT)
                    draw.text((x + PADDING, y + 4), candidate.covered_candidate_id, font=font, fill="black")
                    with candidate_crop(page, candidate) as crop:
                        crop.thumbnail((TILE_WIDTH - 2 * PADDING, IMAGE_HEIGHT - 2 * PADDING), Image.Resampling.LANCZOS)
                        board.paste(crop, (x + (TILE_WIDTH - crop.width) // 2,
                                           y + LABEL_HEIGHT + (IMAGE_HEIGHT - crop.height) // 2))
                buffer = BytesIO()
                board.save(buffer, format="PNG")
                sheets.append(ContactSheet(tuple(c.covered_candidate_id for c in candidates), buffer.getvalue()))
    finally:
        if page is not None:
            page.close()
    return sheets

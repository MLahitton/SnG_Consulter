from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, FiniteFloat


class DocumentKind(StrEnum):
    PDF = "PDF"
    IMAGE = "IMAGE"


class CoordinateUnit(StrEnum):
    PDF_POINT = "PDF_POINT"
    PIXEL = "PIXEL"


class BoundingBox(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float


class TextSpan(BaseModel):
    text: str
    bbox: BoundingBox
    block_index: int | None = None
    line_index: int | None = None
    span_index: int | None = None


class PreparedPage(BaseModel):
    page_number: int = Field(ge=1)
    width_px: int = Field(gt=0)
    height_px: int = Field(gt=0)
    coordinate_width: float = Field(gt=0, allow_inf_nan=False)
    coordinate_height: float = Field(gt=0, allow_inf_nan=False)
    coordinate_unit: CoordinateUnit
    # Native text is unrotated; PDF rendering applies page.rotation_matrix.
    source_to_visible_matrix: tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat]
    image_path: Path
    text_path: Path
    native_text_available: bool
    text_span_count: int = Field(ge=0)


class PreparedDocument(BaseModel):
    preparation_contract_version: Literal[1] = 1
    document_id: str
    source_name: str
    source_sha256: str
    source_path: Path
    kind: DocumentKind
    page_count: int = Field(ge=1)
    output_dir: Path
    pages: list[PreparedPage]

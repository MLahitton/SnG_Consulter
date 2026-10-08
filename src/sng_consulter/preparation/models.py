from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field


class DocumentKind(StrEnum):
    PDF = "PDF"
    IMAGE = "IMAGE"


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
    image_path: Path
    text_path: Path
    native_text_available: bool
    text_span_count: int = Field(ge=0)


class PreparedDocument(BaseModel):
    document_id: str
    source_name: str
    source_sha256: str
    kind: DocumentKind
    page_count: int = Field(ge=1)
    output_dir: Path
    pages: list[PreparedPage]

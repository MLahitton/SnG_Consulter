"""Shared, read-only access to persisted review evidence; no HTTP dependency."""

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

from .preparation.models import PreparedDocument


class IncompatiblePreparedDocumentError(ValueError):
    pass


class ContentIdentityConflictError(ValueError):
    pass


class ReviewBatchEntry(BaseModel):
    document_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{16}$")
    original_name: str
    status: Literal["prepared", "error"]
    status_code: int | None = None
    error: str | None = None

    @model_validator(mode="after")
    def validate_result(self):
        if self.status == "prepared" and self.document_id is None:
            raise ValueError("A prepared occurrence requires document_id")
        if self.status == "error" and (self.document_id is not None or self.error is None or self.status_code is None):
            raise ValueError("An error occurrence requires error/status_code and no document_id")
        return self


class ReviewBatch(BaseModel):
    batch_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    documents: list[ReviewBatchEntry]


def prepared_file(root: Path, directory: Path, relative: Path) -> Path:
    directory = directory.resolve()
    candidate = (directory / relative).resolve()
    if (not directory.is_relative_to(root.resolve()) or not candidate.is_relative_to(directory)
            or not candidate.is_file()):
        raise FileNotFoundError("Prepared evidence file not found")
    return candidate


def read_review_batch(root: Path, batch_id: str) -> ReviewBatch:
    if not re.fullmatch(r"[0-9a-f]{32}", batch_id):
        raise FileNotFoundError("Review batch not found")
    manifest = prepared_file(root, root / batch_id, Path("manifest.json"))
    batch = ReviewBatch.model_validate_json(manifest.read_text(encoding="utf-8"))
    if batch.batch_id != batch_id:
        raise ValueError("Review batch identity mismatch")
    return batch


def read_prepared_document(
    root: Path, document_id: str, expected_sha256: str | None = None
) -> tuple[Path, PreparedDocument]:
    if not re.fullmatch(r"[0-9a-f]{16}", document_id):
        raise FileNotFoundError("Prepared document not found")
    directory = root / document_id
    manifest = prepared_file(root, directory, Path("manifest.json"))
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if expected_sha256 is not None and data["source_sha256"] != expected_sha256:
        raise ContentIdentityConflictError("Content identity conflict")
    fields = {"coordinate_width", "coordinate_height", "coordinate_unit", "source_to_visible_matrix"}
    if (data.get("preparation_contract_version") != PreparedDocument.model_fields["preparation_contract_version"].default
            or any(not fields.issubset(page) for page in data["pages"])):
        raise IncompatiblePreparedDocumentError("Incomplete or incompatible preparation contract")
    try:
        document = PreparedDocument.model_validate(data)
    except ValidationError as exc:
        raise IncompatiblePreparedDocumentError("Invalid preparation contract") from exc
    if document.document_id != document_id or document.source_sha256[:16] != document_id:
        raise ContentIdentityConflictError("Prepared document identity mismatch")
    if (len(document.pages) != document.page_count
            or {page.page_number for page in document.pages} != set(range(1, document.page_count + 1))):
        raise IncompatiblePreparedDocumentError("Incomplete page list")
    return directory, document

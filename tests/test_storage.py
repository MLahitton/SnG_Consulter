import json

import pymupdf as fitz
import pytest

from sng_consulter.preparation import DocumentPreparationService
from sng_consulter.storage import IncompatiblePreparedDocumentError, read_prepared_document


@pytest.fixture
def persisted(tmp_path):
    source = tmp_path / "synthetic.pdf"
    with fitz.open() as pdf:
        pdf.new_page(width=20, height=20)
        pdf.save(source)
    root = tmp_path / "prepared"
    result = DocumentPreparationService(render_dpi=72).prepare(source, root)
    return root, result, result.output_dir / "manifest.json"


@pytest.mark.parametrize("content", ["{broken", "null", "[]", "{}", '"not an object"'])
def test_malformed_manifests_raise_contract_error_instead_of_key_error(persisted, content):
    root, result, manifest = persisted
    manifest.write_text(content, encoding="utf-8")
    with pytest.raises(IncompatiblePreparedDocumentError):
        read_prepared_document(root, result.document_id, expected_sha256=result.source_sha256)


@pytest.mark.parametrize("field,value", [
    ("source_sha256", None), ("pages", None), ("pages", {}), ("pages", [None]),
    ("pages", []), ("source_sha256", []),
])
def test_incomplete_manifest_is_validated_before_field_access(persisted, field, value):
    root, result, manifest = persisted
    data = json.loads(manifest.read_text())
    if value is None:
        del data[field]
    else:
        data[field] = value
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(IncompatiblePreparedDocumentError):
        read_prepared_document(root, result.document_id, expected_sha256=result.source_sha256)


def test_manifest_with_invalid_utf8_is_contract_error(persisted):
    root, result, manifest = persisted
    manifest.write_bytes(b"\xff")
    with pytest.raises(IncompatiblePreparedDocumentError):
        read_prepared_document(root, result.document_id)

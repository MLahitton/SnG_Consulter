import hashlib
from pathlib import Path

import pymupdf as fitz
import pytest

from sng_consulter.preparation import DocumentPreparationService
from sng_consulter.preparation import service as preparation
from sng_consulter.preparation.models import PreparedDocument


def make_pdf(path: Path) -> None:
    with fitz.open() as pdf:
        for index in range(2):
            page = pdf.new_page(width=80, height=100)
            page.insert_text((10, 20), f"Page {index}")
        pdf.save(path)


def snapshot(directory: Path) -> dict:
    return {path.relative_to(directory): path.read_bytes()
            for path in directory.rglob("*") if path.is_file()}


@pytest.mark.parametrize("previous", [False, True])
def test_failure_after_first_page_cleans_staging_and_keeps_previous(tmp_path, monkeypatch, previous):
    source = tmp_path / "sample.pdf"
    make_pdf(source)
    output = tmp_path / "out"
    service = DocumentPreparationService(render_dpi=72)
    document_id = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    if previous:
        service.prepare(source, output)
    before = snapshot(output)
    save = preparation._save_pixmap_as_webp
    calls = []

    def fail_on_second(pix, target, quality):
        calls.append(target)
        if len(calls) == 2:
            assert calls[0].is_file()
            assert (calls[0].parent.parent / "text/0001.json").is_file()
            if previous:
                assert snapshot(output / document_id) == {
                    path.relative_to(document_id): content for path, content in before.items()
                }
            else:
                assert not (output / document_id).exists()
            raise RuntimeError("intentional page failure")
        save(pix, target, quality)

    monkeypatch.setattr(preparation, "_save_pixmap_as_webp", fail_on_second)
    with pytest.raises(RuntimeError, match="intentional page failure"):
        service.prepare(source, output)
    assert len(calls) == 2
    assert snapshot(output) == before
    assert list(output.glob(".staging-*")) == []
    assert list(output.iterdir()) == ([output / document_id] if previous else [])


def test_replacement_publishes_complete_manifest_with_final_paths(tmp_path):
    source = tmp_path / "sample.pdf"
    make_pdf(source)
    output = tmp_path / "out"
    first = DocumentPreparationService(render_dpi=72).prepare(source, output)
    second = DocumentPreparationService(render_dpi=96).prepare(source, output)
    assert first.document_id == second.document_id
    persisted = PreparedDocument.model_validate_json((second.output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert persisted.output_dir == second.output_dir
    assert persisted.pages[0].width_px != first.pages[0].width_px
    assert (persisted.output_dir / persisted.source_path).read_bytes() == source.read_bytes()
    for page in persisted.pages:
        assert (persisted.output_dir / page.image_path).is_file()
        assert (persisted.output_dir / page.text_path).is_file()
    assert list(output.iterdir()) == [second.output_dir]


def test_publish_rename_failure_restores_previous(tmp_path, monkeypatch):
    source = tmp_path / "sample.pdf"
    make_pdf(source)
    output = tmp_path / "out"
    service = DocumentPreparationService(render_dpi=72)
    service.prepare(source, output)
    before = snapshot(output)
    rename = Path.rename

    def fail_publication(path, target):
        if path.name == "document" and path.parent.name.startswith(".staging-"):
            raise OSError("intentional rename failure")
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_publication)
    with pytest.raises(OSError, match="intentional rename failure"):
        service.prepare(source, output)
    assert snapshot(output) == before
    assert list(output.glob(".staging-*")) == []


def test_failed_rollback_keeps_prior_bytes_in_recovery_directory(tmp_path, monkeypatch):
    source = tmp_path / "sample.pdf"
    make_pdf(source)
    output = tmp_path / "out"
    service = DocumentPreparationService(render_dpi=72)
    previous = service.prepare(source, output)
    before = snapshot(previous.output_dir)
    rename = Path.rename

    def fail_publish_and_restore(path, target):
        if path.name == "document" or path.name.startswith(".previous-"):
            raise OSError("intentional publication and rollback failure")
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_publish_and_restore)
    with pytest.raises(OSError, match="intentional publication and rollback failure"):
        service.prepare(source, output)
    backups = list(output.glob(".previous-*"))
    assert len(backups) == 1
    assert snapshot(backups[0]) == before
    assert list(output.glob(".staging-*")) == []

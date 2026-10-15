import json
from io import BytesIO
from pathlib import Path

import pymupdf as fitz
import pytest
from PIL import Image
from fastapi.testclient import TestClient

from sng_consulter.api import app
from sng_consulter.review import routes


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(routes, "UPLOAD_DIR", tmp_path / "runtime/review/uploads")
    monkeypatch.setattr(routes, "PREPARED_DIR", tmp_path / "runtime/review/prepared")
    monkeypatch.setattr(routes, "BATCH_DIR", tmp_path / "runtime/review/batches")
    with TestClient(app) as client:
        yield client


def pdf_bytes() -> bytes:
    with fitz.open() as pdf:
        page = pdf.new_page(width=100, height=100)
        page.insert_text((10, 20), "Native text")
        pdf.new_page(width=100, height=100)
        return pdf.tobytes()


def prepared_url(response):
    batch_url = response.headers.get("location", str(response.url))
    assert "/review/batches/" in batch_url
    batch_id = batch_url.rsplit("/", 1)[1]
    batch = json.loads((routes.BATCH_DIR / batch_id / "manifest.json").read_text(encoding="utf-8"))
    assert len(batch["documents"]) == 1
    assert batch["documents"][0]["status"] == "prepared"
    return f'/review/{batch["documents"][0]["document_id"]}'


@pytest.mark.parametrize("kind", ["pdf", "png"])
def test_single_upload_always_creates_batch_with_occurrence_zero_and_discovery_input(client, kind):
    from sng_consulter.discovery.inputs import build_discovery_input

    content = pdf_bytes()
    if kind == "png":
        buffer = BytesIO()
        Image.new("RGB", (20, 30), "white").save(buffer, format="PNG")
        content = buffer.getvalue()
    name = f"original.{kind}"
    response = client.post("/review/upload", files={"document": (name, content)}, follow_redirects=False)
    assert response.status_code == 303
    batch_url = response.headers["location"]
    assert batch_url.startswith("/review/batches/")
    batch_id = batch_url.rsplit("/", 1)[1]
    batch = json.loads((routes.BATCH_DIR / batch_id / "manifest.json").read_text())
    url = prepared_url(response)
    assert batch["documents"] == [dict(document_id=url.rsplit("/", 1)[1], original_name=name, status="prepared")]
    page = client.get(batch_url)
    assert "Preparados: 1 / 1. Fallidos: 0." in page.text
    context = f"?batch_id={batch_id}&occurrence=0"
    assert client.get(url + context).status_code == 200
    download = client.get(url + "/source" + context)
    assert download.content == content
    assert f'filename="{name}"' in download.headers["content-disposition"]
    evidence = build_discovery_input(batch_id, routes.BATCH_DIR, routes.PREPARED_DIR)
    assert evidence.batch_id == batch_id
    assert len(evidence.pages) == (2 if kind == "pdf" else 1)
    assert all(p.occurrence == 0 and p.document_id == batch["documents"][0]["document_id"] for p in evidence.pages)


def test_single_invalid_upload_creates_error_batch_and_closes_stream_once(client):
    from fastapi import UploadFile

    class Stream(BytesIO):
        closes = 0

        def close(self):
            self.closes += 1
            super().close()

    stream = Stream(b"invalid")
    response = routes.upload_documents([UploadFile(stream, filename="bad.pdf")])
    assert response.status_code == 303
    assert stream.closed and stream.closes == 1
    url = response.headers["location"]
    batch_id = url.rsplit("/", 1)[1]
    batch = json.loads((routes.BATCH_DIR / batch_id / "manifest.json").read_text())
    assert len(batch["documents"]) == 1
    assert batch["documents"][0]["original_name"] == "bad.pdf"
    assert batch["documents"][0]["document_id"] is None
    assert batch["documents"][0]["status"] == "error"
    assert batch["documents"][0]["status_code"] == 400
    assert "Preparados: 0 / 1. Fallidos: 1." in client.get(url).text


def test_home_contains_upload_form(client):
    response = client.get("/review")
    assert response.status_code == 200
    assert 'action="/review/upload"' in response.text
    assert 'method="post"' in response.text
    assert 'enctype="multipart/form-data"' in response.text
    assert 'name="document"' in response.text
    assert "Procesar documentos" in response.text
    assert 'required multiple' in response.text


def test_pdf_review_flow(client, monkeypatch):
    content = pdf_bytes()
    response = client.post("/review/upload", files={"document": ("sample.pdf", content)},
                           follow_redirects=False)
    assert response.status_code == 303
    url = prepared_url(response)
    directory = routes.PREPARED_DIR / url.rsplit("/", 1)[1]
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["page_count"] == 2
    assert manifest["source_name"] == "sample.pdf"
    assert (directory / manifest["source_path"]).read_bytes() == content
    assert list(routes.UPLOAD_DIR.iterdir()) == []

    def no_reprocessing(*args, **kwargs):
        pytest.fail("Navigation must not prepare again")

    monkeypatch.setattr(routes.DocumentPreparationService, "prepare", no_reprocessing)
    first = client.get(url)
    assert first.status_code == 200
    assert f'<img src="{url}/pages/1/image"' in first.text
    assert 'max-width:100%;max-height:80vh;width:auto;height:auto;object-fit:contain' in first.text
    assert f'href="{url}/pages/1/image" target="_blank" rel="noopener"' in first.text
    assert "Ver imagen preparada en tamaño completo" in first.text
    assert "Página 1 / 2" in first.text
    assert "Native text" in first.text
    assert "Texto nativo extraído" in first.text
    assert "Text spans RAW y bounding boxes originales" in first.text
    assert "bbox" in first.text
    assert f'href="{url}/page/1/bbox-overlay"' in first.text
    assert "Ver bounding boxes" in first.text
    assert "Dimensiones de coordenadas: 100.0 × 100.0" in first.text
    assert "Unidad de coordenadas: PDF_POINT" in first.text
    assert "Dimensiones del render: 250 × 250 px" in first.text
    second = client.get(url + "?page=2")
    assert second.status_code == 200
    assert "Página 2 / 2" in second.text
    assert "Texto nativo: No" in second.text
    assert "Ver bounding boxes" not in second.text
    assert "Texto nativo extraído" not in second.text
    assert "Text spans RAW y bounding boxes originales" not in second.text
    image = client.get(url + "/pages/2/image")
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/webp"
    assert image.content[8:12] == b"WEBP"
    assert client.get(url + "/source").content == content
    assert client.get(url + "?page=3").status_code == 404
    assert client.get(url + "/pages/0/image").status_code == 404


@pytest.mark.parametrize("filename", ["file.txt", "file.tiff", "file.tif"])
def test_unsupported_format(client, filename):
    response = client.post("/review/upload", files={"document": (filename, b"data")})
    assert response.status_code == 200
    assert "Error (415)" in response.text
    assert "Preparados: 0 / 1. Fallidos: 1." in response.text


def test_invalid_pdf_cleans_upload(client):
    response = client.post("/review/upload", files={"document": ("bad.pdf", b"invalid")})
    assert response.status_code == 200
    assert "Preparados: 0 / 1. Fallidos: 1." in response.text
    assert list(routes.UPLOAD_DIR.iterdir()) == []


@pytest.mark.parametrize("filename", ["sample.pdf", "plano con espacios.pdf", "Fachada José áéñ.pdf"])
def test_upload_delivers_complete_pdf_bytes(client, monkeypatch, filename):
    content = pdf_bytes()
    prepare = routes.DocumentPreparationService.prepare
    observed = []

    def inspect_upload(service, source, output_root):
        assert source.is_file()
        assert source.stat().st_size == len(content) > 0
        assert source.read_bytes() == content
        assert source.read_bytes().startswith(b"%PDF-")
        with source.open("r+b"):
            pass
        observed.append(source)
        return prepare(service, source, output_root)

    monkeypatch.setattr(routes.DocumentPreparationService, "prepare", inspect_upload)
    response = client.post("/review/upload", files={"document": (filename, content)})
    assert response.status_code == 200
    assert filename in response.text
    assert len(observed) == 1
    assert not observed[0].exists()
    assert list(routes.UPLOAD_DIR.iterdir()) == []


def test_upload_rewinds_stream_before_copy(client):
    from fastapi import UploadFile

    stream = BytesIO(pdf_bytes())
    stream.seek(0, 2)
    result = routes.upload_documents([UploadFile(stream, filename="at-end.pdf")])
    assert result.status_code == 303
    assert stream.closed
    assert list(routes.UPLOAD_DIR.iterdir()) == []


@pytest.mark.parametrize("content,detail", [
    (b"", "vacío"), (b"not a pdf", "cabecera PDF"),
])
def test_upload_validation_before_preparation(client, monkeypatch, caplog, content, detail):
    def must_not_prepare(*args, **kwargs):
        pytest.fail("Invalid upload must be rejected before preparation")

    monkeypatch.setattr(routes.DocumentPreparationService, "prepare", must_not_prepare)
    response = client.post("/review/upload", files={"document": ("invalid.pdf", content)})
    assert response.status_code == 200
    assert "Preparados: 0 / 1. Fallidos: 1." in response.text
    assert detail in response.text
    assert "filename='invalid.pdf'" in caplog.text
    assert f"size={len(content)}" in caplog.text
    assert "prefix_hex=" in caplog.text
    assert list(routes.UPLOAD_DIR.iterdir()) == []


def test_pdf_header_does_not_replace_pymupdf_validation(client, caplog):
    response = client.post("/review/upload", files={"document": ("broken.pdf", b"%PDF-1.7\nbroken")})
    assert response.status_code == 200
    assert "Preparados: 0 / 1. Fallidos: 1." in response.text
    assert "FileDataError" in response.text
    assert "FileDataError" in caplog.text
    assert list(routes.UPLOAD_DIR.iterdir()) == []


@pytest.mark.parametrize("suffix,format", [
    ("jpg", "JPEG"), ("jpeg", "JPEG"), ("png", "PNG"),
    ("webp", "WEBP"), ("bmp", "BMP"),
])
def test_image_upload_without_invented_text(client, suffix, format):
    buffer = BytesIO()
    Image.new("RGB", (20, 10), "white").save(buffer, format=format)
    response = client.post("/review/upload", files={"document": (f"image.{suffix}", buffer.getvalue())})
    assert response.status_code == 200
    response = client.get(prepared_url(response))
    assert "Tipo de documento: IMAGE" in response.text
    assert "Página 1 / 1" in response.text
    assert "20 × 10 px" in response.text
    assert "Dimensiones de coordenadas: 20.0 × 10.0" in response.text
    assert "Unidad de coordenadas: PIXEL" in response.text
    assert "Texto nativo: No" in response.text
    assert "Text spans raw: 0" in response.text
    assert "Text spans únicos: 0" in response.text
    assert "Ver bounding boxes" not in response.text
    assert "Texto nativo extraído" not in response.text
    assert "Text spans RAW y bounding boxes originales" not in response.text


def test_untrusted_name_and_manifest_paths(client, tmp_path):
    response = client.post("/review/upload", files={"document": ("../../<script>.pdf", pdf_bytes())},
                           follow_redirects=False)
    assert response.status_code == 303
    url = prepared_url(response)
    review = client.get(url)
    assert "<script>" not in review.text
    assert "&lt;script&gt;" in review.text
    manifest_path = routes.PREPARED_DIR / url.rsplit("/", 1)[1] / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    manifest["source_path"] = str(outside)
    manifest["pages"][0]["image_path"] = "../../../secret.txt"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert client.get(url + "/source").status_code == 404
    assert client.get(url + "/pages/1/image").status_code == 404
    assert client.get("/review/invalid").status_code == 404


def test_old_manifest_requests_new_upload_without_guessing_coordinates(client):
    response = client.post("/review/upload", files={"document": ("sample.pdf", pdf_bytes())},
                           follow_redirects=False)
    url = prepared_url(response)
    manifest_path = routes.PREPARED_DIR / url.rsplit("/", 1)[1] / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    for page in data["pages"]:
        for field in ("coordinate_width", "coordinate_height", "coordinate_unit"):
            del page[field]
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    original_bytes = manifest_path.read_bytes()
    review = client.get(url)
    assert review.status_code == 409
    assert "Vuelve a subir" in review.json()["detail"]
    assert manifest_path.read_bytes() == original_bytes


def test_bbox_overlay_uses_converter_without_changing_prepared_files(client, monkeypatch):
    response = client.post("/review/upload", files={"document": ("sample.pdf", pdf_bytes())},
                           follow_redirects=False)
    url = prepared_url(response)
    directory = routes.PREPARED_DIR / url.rsplit("/", 1)[1]
    before = {path.relative_to(directory): path.read_bytes() for path in directory.rglob("*") if path.is_file()}
    manifest = json.loads(before[Path("manifest.json")])
    page = manifest["pages"][0]
    spans = json.loads(before[Path(page["text_path"])])
    convert = routes.source_bbox_to_render_pixels
    converted = []

    def track_conversion(bbox, prepared_page):
        result = convert(bbox, prepared_page)
        converted.append(result)
        return result

    monkeypatch.setattr(routes, "source_bbox_to_render_pixels", track_conversion)
    overlay = client.get(url + "/page/1/bbox-overlay")
    assert overlay.status_code == 200
    assert overlay.headers["content-type"] == "image/png"
    assert len(converted) == len(spans) > 0
    with Image.open(BytesIO(overlay.content)) as image:
        image.load()
        assert image.size == (page["width_px"], page["height_px"])
        bbox = converted[0]
        assert image.getpixel((int(bbox.x0), int(bbox.y0))) == (255, 0, 0)
    after = {path.relative_to(directory): path.read_bytes() for path in directory.rglob("*") if path.is_file()}
    assert after == before


def test_bbox_overlay_ignores_outside_and_invalid_rectangles(client, caplog):
    response = client.post("/review/upload", files={"document": ("sample.pdf", pdf_bytes())},
                           follow_redirects=False)
    url = prepared_url(response)
    directory = routes.PREPARED_DIR / url.rsplit("/", 1)[1]
    text_path = directory / "text/0001.json"
    spans = json.loads(text_path.read_text(encoding="utf-8"))
    for bbox in [dict(x0=90, y0=90, x1=110, y1=110), dict(x0=-1, y0=0, x1=10, y1=10)]:
        spans.append(dict(text="debug", bbox=bbox))
    text_path.write_text(json.dumps(spans), encoding="utf-8")
    before = text_path.read_bytes()
    overlay = client.get(url + "/page/1/bbox-overlay")
    assert overlay.status_code == 200
    assert "fuera del render" in caplog.text
    assert "coordenadas inválidas" in caplog.text
    assert text_path.read_bytes() == before


def test_bbox_overlay_missing_page_or_document_and_empty_page(client):
    response = client.post("/review/upload", files={"document": ("sample.pdf", pdf_bytes())},
                           follow_redirects=False)
    url = prepared_url(response)
    assert client.get(url + "/page/3/bbox-overlay").status_code == 404
    assert client.get("/review/0000000000000000/page/1/bbox-overlay").status_code == 404
    assert client.get("/review/invalid/page/1/bbox-overlay").status_code == 404
    overlay = client.get(url + "/page/2/bbox-overlay")
    assert overlay.status_code == 200
    original = client.get(url + "/pages/2/image")
    with Image.open(BytesIO(overlay.content)) as image, Image.open(BytesIO(original.content)) as source:
        assert image.tobytes() == source.convert("RGB").tobytes()


def test_review_normalized_view_keeps_raw_pdf_spans_and_files(client, monkeypatch):
    with fitz.open() as pdf:
        page = pdf.new_page(width=200, height=100)
        page.insert_text((10, 20), "HARAS")
        page.insert_text((10, 20), "HARAS")
        page.insert_text((100, 60), "HARAS")
        content = pdf.tobytes()
    response = client.post("/review/upload", files={"document": ("duplicate.pdf", content)},
                           follow_redirects=False)
    url = prepared_url(response)
    directory = routes.PREPARED_DIR / url.rsplit("/", 1)[1]
    before = {path.relative_to(directory): path.read_bytes() for path in directory.rglob("*") if path.is_file()}
    manifest = json.loads(before[Path("manifest.json")])
    assert manifest["pages"][0]["text_span_count"] == 3
    raw = json.loads(before[Path("text/0001.json")])
    assert len(raw) == 3
    assert raw[0]["bbox"] == raw[1]["bbox"]
    review = client.get(url)
    assert review.status_code == 200
    assert "Text spans raw: 3" in review.text
    assert "Text spans únicos: 2" in review.text
    assert "RAW siguen preservados" in review.text
    assert "<pre>HARAS\nHARAS</pre>" in review.text
    assert "Ver bounding boxes" in review.text
    convert = routes.source_bbox_to_render_pixels
    converted = []

    def track(bbox, page):
        converted.append(bbox)
        return convert(bbox, page)

    monkeypatch.setattr(routes, "source_bbox_to_render_pixels", track)
    assert client.get(url + "/page/1/bbox-overlay").status_code == 200
    assert len(converted) == 2
    after = {path.relative_to(directory): path.read_bytes() for path in directory.rglob("*") if path.is_file()}
    assert after == before


def test_multiple_documents_and_photos_in_one_upload(client):
    image = BytesIO()
    Image.new("RGB", (30, 20), "white").save(image, format="PNG")
    content = pdf_bytes()
    response = client.post("/review/upload", files=[
        ("document", ("plano.pdf", content)),
        ("document", ("foto José.png", image.getvalue())),
    ])
    assert response.status_code == 200
    assert "Preparados: 2 / 2" in response.text
    assert "Fallidos: 0" in response.text
    assert "plano.pdf" in response.text
    assert "foto José.png" in response.text
    assert response.text.count("Revisar documento") == 2
    batch_id = str(response.url).rsplit("/", 1)[1]
    batch = json.loads((routes.BATCH_DIR / batch_id / "manifest.json").read_text(encoding="utf-8"))
    assert batch["batch_id"] == batch_id
    assert len(batch["documents"]) == 2
    assert [entry["original_name"] for entry in batch["documents"]] == ["plano.pdf", "foto José.png"]
    manifests = list(routes.PREPARED_DIR.glob("*/manifest.json"))
    assert len(manifests) == 2
    originals = {"plano.pdf": content, "foto José.png": image.getvalue()}
    for path in manifests:
        data = json.loads(path.read_text(encoding="utf-8"))
        assert (path.parent / data["source_path"]).read_bytes() == originals[data["source_name"]]
        assert client.get(f'/review/{data["document_id"]}').status_code == 200
        assert client.get(f'/review/{data["document_id"]}/pages/1/image').status_code == 200
        assert data["document_id"] in {entry["document_id"] for entry in batch["documents"]}
    assert list(routes.UPLOAD_DIR.iterdir()) == []


def test_batch_continues_after_invalid_files_and_escapes_names(client):
    response = client.post("/review/upload", files=[
        ("document", ("<script>.pdf", b"bad")),
        ("document", ("valid.pdf", pdf_bytes())),
        ("document", ("empty.pdf", b"")),
        ("document", ("unsupported.tiff", b"bad")),
    ])
    assert response.status_code == 200
    assert "Preparados: 1 / 4" in response.text
    assert "Fallidos: 3" in response.text
    assert "<script>" not in response.text
    assert "&lt;script&gt;.pdf" in response.text
    assert "Error (400)" in response.text
    assert "Error (415)" in response.text
    assert len(list(routes.PREPARED_DIR.glob("*/manifest.json"))) == 1
    assert list(routes.UPLOAD_DIR.iterdir()) == []
    batch_id = str(response.url).rsplit("/", 1)[1]
    batch = json.loads((routes.BATCH_DIR / batch_id / "manifest.json").read_text(encoding="utf-8"))
    assert [entry["status"] for entry in batch["documents"]] == ["error", "prepared", "error", "error"]
    assert batch["documents"][0]["document_id"] is None


def test_batch_closes_all_uploads_on_unexpected_error(client, monkeypatch):
    from fastapi import UploadFile

    uploads = [UploadFile(BytesIO(pdf_bytes()), filename=f"{index}.pdf") for index in range(2)]

    def fail(*args):
        raise OSError("disk error")

    monkeypatch.setattr(routes, "_prepare_upload", fail)
    with pytest.raises(OSError, match="disk error"):
        routes.upload_documents(uploads)
    assert all(upload.file.closed for upload in uploads)


def test_identical_content_preserves_occurrences_and_prepares_only_once(client, monkeypatch):
    from urllib.parse import unquote

    prepare = routes.DocumentPreparationService.prepare
    calls = []

    def track(service, source, output_root):
        calls.append(source)
        return prepare(service, source, output_root)

    monkeypatch.setattr(routes.DocumentPreparationService, "prepare", track)
    content = pdf_bytes()
    names = ["primero.pdf", "../../Planos/Fachada José.pdf"]
    response = client.post("/review/upload", files=[("document", (name, content)) for name in names],
                           follow_redirects=False)
    assert response.status_code == 303
    batch_url = response.headers["location"]
    batch_id = batch_url.rsplit("/", 1)[1]
    path = routes.BATCH_DIR / batch_id / "manifest.json"
    batch = json.loads(path.read_text(encoding="utf-8"))
    assert len(calls) == 1
    entries = batch["documents"]
    assert [entry["original_name"] for entry in entries] == names
    assert entries[0]["document_id"] == entries[1]["document_id"]
    assert client.get(batch_url).text.count("Revisar documento") == 2
    manifests = list(routes.PREPARED_DIR.glob("*/manifest.json"))
    assert len(manifests) == 1
    canonical = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert canonical["source_name"] == names[0]
    assert "batch_id" not in canonical
    for index, name in enumerate(names):
        base = f'/review/{entries[index]["document_id"]}'
        context = f"?batch_id={batch_id}&occurrence={index}"
        review = client.get(base + context)
        assert review.status_code == 200
        assert name in review.text
        assert f'href="{batch_url}"' in review.text
        assert f"page=2&batch_id={batch_id}&occurrence={index}" in review.text
        assert client.get(base + context + "&page=2").status_code == 200
        original = client.get(base + "/source" + context)
        assert original.content == content
        disposition = unquote(original.headers["content-disposition"])
        assert name.rsplit("/", 1)[-1] in disposition
        assert "../" not in disposition
    assert client.get(base + f"?batch_id={batch_id}&occurrence=8").status_code == 404
    assert client.get("/review/batches/not-a-batch").status_code == 404
    assert client.get("/review/batches/" + "0" * 32).status_code == 404
    assert list(routes.UPLOAD_DIR.iterdir()) == []


@pytest.mark.parametrize("name,expected", [
    ("../../original.pdf", "original.pdf"),
    (r"C:\secret\original.pdf", "original.pdf"),
    ("plano con espacios.pdf", "plano con espacios.pdf"),
    ("plano\r\n.pdf", "plano.pdf"),
])
def test_download_name_is_safe_basename(client, name, expected):
    from urllib.parse import unquote

    assert routes._download_name(name) == expected
    response = client.post("/review/upload", files={"document": ("original.pdf", pdf_bytes())},
                           follow_redirects=False)
    url = prepared_url(response)
    manifest = routes.PREPARED_DIR / url.rsplit("/", 1)[1] / "manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["source_name"] = name
    manifest.write_text(json.dumps(data), encoding="utf-8")
    download = client.get(url + "/source")
    assert download.status_code == 200
    assert expected in unquote(download.headers["content-disposition"])
    assert "document.pdf" not in download.headers["content-disposition"]


def test_upload_handler_closes_each_stream_once_on_success_and_validation_error(client):
    from fastapi import UploadFile

    class TrackedStream(BytesIO):
        def __init__(self, content):
            super().__init__(content)
            self.close_count = 0

        def close(self):
            self.close_count += 1
            super().close()

    streams = [TrackedStream(pdf_bytes()), TrackedStream(b"invalid")]
    result = routes.upload_documents([
        UploadFile(streams[0], filename="valid.pdf"), UploadFile(streams[1], filename="invalid.pdf"),
    ])
    assert result.status_code == 303
    assert [stream.close_count for stream in streams] == [1, 1]
    assert all(stream.closed for stream in streams)
    assert list(routes.UPLOAD_DIR.iterdir()) == []


def test_single_reupload_with_new_name_keeps_canonical_metadata(client, monkeypatch):
    content = pdf_bytes()
    first = client.post("/review/upload", files={"document": ("first.pdf", content)}, follow_redirects=False)
    document_id = prepared_url(first).rsplit("/", 1)[1]
    manifest = routes.PREPARED_DIR / document_id / "manifest.json"
    before = manifest.read_bytes()

    def no_repreparation(*args, **kwargs):
        pytest.fail("Existing identical content should be reused")

    monkeypatch.setattr(routes.DocumentPreparationService, "prepare", no_repreparation)
    second = client.post("/review/upload", files={"document": ("renamed.pdf", content)}, follow_redirects=False)
    batch_url = second.headers["location"]
    assert batch_url.startswith("/review/batches/")
    batch_id = batch_url.rsplit("/", 1)[1]
    download = client.get(f"/review/{document_id}/source?batch_id={batch_id}&occurrence=0")
    assert download.content == content
    assert 'filename="renamed.pdf"' in download.headers["content-disposition"]
    assert manifest.read_bytes() == before


@pytest.mark.parametrize("missing_field", ["source_to_visible_matrix", "coordinate_width"])
def test_legacy_rotated_manifest_is_rejected_then_regenerated_atomically(client, monkeypatch, missing_field):
    with fitz.open() as pdf:
        for rotation in (90, 270):
            page = pdf.new_page(width=100, height=140)
            page.insert_text((10, 30), "Native text")
            page.set_rotation(rotation)
        content = pdf.tobytes()
    response = client.post("/review/upload", files={"document": ("rotated.pdf", content)}, follow_redirects=False)
    url = prepared_url(response)
    directory = routes.PREPARED_DIR / url.rsplit("/", 1)[1]
    manifest_path = directory / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    del data["pages"][1][missing_field]
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    old_files = {path.relative_to(directory): path.read_bytes() for path in directory.rglob("*") if path.is_file()}
    assert client.get(url).status_code == 409
    assert client.get(url + "/page/1/bbox-overlay").status_code == 409
    assert manifest_path.read_bytes() == old_files[Path("manifest.json")]
    prepare = routes.DocumentPreparationService.prepare
    calls = []

    def track(service, source, output_root):
        assert {path.relative_to(directory): path.read_bytes() for path in directory.rglob("*") if path.is_file()} == old_files
        calls.append(source)
        return prepare(service, source, output_root)

    monkeypatch.setattr(routes.DocumentPreparationService, "prepare", track)
    again = client.post("/review/upload", files={"document": ("rotated.pdf", content)}, follow_redirects=False)
    assert again.status_code == 303
    assert prepared_url(again) == url
    assert len(calls) == 1
    current = json.loads(manifest_path.read_text(encoding="utf-8"))
    with fitz.open(stream=content, filetype="pdf") as pdf:
        assert [page["source_to_visible_matrix"] for page in current["pages"]] == [list(page.rotation_matrix) for page in pdf]
    assert client.get(url).status_code == 200
    assert client.get(url + "/page/1/bbox-overlay").status_code == 200
    assert list(routes.PREPARED_DIR.glob(".staging-*")) == []
    assert list(routes.UPLOAD_DIR.iterdir()) == []


def test_failed_legacy_regeneration_keeps_prior_manifest_and_files(client, monkeypatch):
    content = pdf_bytes()
    response = client.post("/review/upload", files={"document": ("sample.pdf", content)}, follow_redirects=False)
    url = prepared_url(response)
    directory = routes.PREPARED_DIR / url.rsplit("/", 1)[1]
    manifest_path = directory / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    del data["pages"][0]["source_to_visible_matrix"]
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    before = {path.relative_to(directory): path.read_bytes() for path in directory.rglob("*") if path.is_file()}

    def fail(*args, **kwargs):
        raise RuntimeError("intentional preparation failure")

    monkeypatch.setattr(routes.DocumentPreparationService, "_prepare_pdf", fail)
    failed = client.post("/review/upload", files={"document": ("sample.pdf", content)})
    assert failed.status_code == 200
    assert "Preparados: 0 / 1. Fallidos: 1." in failed.text
    assert {path.relative_to(directory): path.read_bytes() for path in directory.rglob("*") if path.is_file()} == before
    assert list(routes.PREPARED_DIR.glob(".staging-*")) == []
    assert list(routes.UPLOAD_DIR.iterdir()) == []


def test_image_without_current_contract_version_is_regenerated_for_exif(client, monkeypatch):
    with Image.new("RGB", (40, 20), "red") as image:
        exif = Image.Exif()
        exif[274] = 6
        buffer = BytesIO()
        image.save(buffer, format="JPEG", exif=exif)
    content = buffer.getvalue()
    response = client.post("/review/upload", files={"document": ("photo.jpg", content)}, follow_redirects=False)
    url = prepared_url(response)
    directory = routes.PREPARED_DIR / url.rsplit("/", 1)[1]
    manifest_path = directory / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    del data["preparation_contract_version"]
    # Reproduce the old output, which already contained an identity matrix.
    with Image.open(BytesIO(content)) as image:
        image.convert("RGB").save(directory / data["pages"][0]["image_path"], format="WEBP")
    data["pages"][0].update(width_px=40, height_px=20, coordinate_width=40, coordinate_height=20)
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    assert client.get(url).status_code == 409
    prepare = routes.DocumentPreparationService.prepare
    calls = []

    def track(service, source, output_root):
        calls.append(source)
        return prepare(service, source, output_root)

    monkeypatch.setattr(routes.DocumentPreparationService, "prepare", track)
    again = client.post("/review/upload", files={"document": ("photo.jpg", content)}, follow_redirects=False)
    assert again.status_code == 303
    assert len(calls) == 1
    current = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert current["preparation_contract_version"] == 1
    page = current["pages"][0]
    assert (page["width_px"], page["height_px"]) == (20, 40)
    assert page["source_to_visible_matrix"] == [1, 0, 0, 1, 0, 0]
    assert (directory / current["source_path"]).read_bytes() == content

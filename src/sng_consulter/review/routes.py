import json
import re
import logging
import shutil
from html import escape
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from PIL import Image, ImageDraw

from sng_consulter.preparation import (
    DocumentPreparationService, deduplicate_text_spans, source_bbox_to_render_pixels,
)
from sng_consulter.preparation.models import PreparedDocument, PreparedPage, TextSpan
from sng_consulter.preparation.service import UnsupportedDocumentError

router = APIRouter(prefix="/review", tags=["review"])
UPLOAD_DIR = Path("runtime/review/uploads")
PREPARED_DIR = Path("runtime/review/prepared")
logger = logging.getLogger(__name__)


def _html(body: str) -> HTMLResponse:
    return HTMLResponse(
        '<!doctype html><html lang="es"><head><meta charset="utf-8">'
        '<title>SnG Consulter — Fase 1</title></head><body>'
        f'{body}</body></html>'
    )


@router.get("", response_class=HTMLResponse)
def review_home() -> HTMLResponse:
    return _html('''
        <h1>SnG Consulter</h1><h2>Fase 1 — Preparación documental</h2>
        <p>Selecciona uno o varios PDFs o imágenes JPG, JPEG, PNG, WEBP o BMP.</p>
        <form action="/review/upload" method="post" enctype="multipart/form-data">
            <input type="file" name="document" required multiple
                   accept=".pdf,.jpg,.jpeg,.png,.webp,.bmp">
            <button type="submit">Procesar documentos</button>
        </form>
    ''')


@router.post("/upload")
def upload_documents(document: list[UploadFile] = File(...)) -> Response:
    # A single file keeps the existing redirect and error behavior.
    try:
        if len(document) == 1:
            return upload_document(document[0])
        rows = []
        successful = 0
        for upload in document:
            name = escape(upload.filename or "document")
            try:
                result = upload_document(upload)
            except HTTPException as exc:
                if exc.status_code not in {400, 415}:
                    raise
                rows.append(f"<li>{name}: Error ({exc.status_code}) — {escape(str(exc.detail))}</li>")
            else:
                successful += 1
                url = escape(result.headers["location"], quote=True)
                rows.append(f'<li>{name}: Preparado — <a href="{url}" target="_blank" '
                            'rel="noopener">Revisar documento</a></li>')
        return _html(
            '<h1>Resultados de la carga</h1>'
            f'<p>Preparados: {successful} / {len(document)}. '
            f'Fallidos: {len(document) - successful}.</p>'
            '<p>Cada documento se revisa en una nueva pestaña.</p>'
            f'<ul>{"".join(rows)}</ul><p><a href="/review">Subir más documentos</a></p>'
        )
    finally:
        for upload in document:
            upload.file.close()


def upload_document(document: UploadFile) -> RedirectResponse:
    # The client name is metadata only; it never becomes a filesystem path.
    source_name = document.filename or "document"
    suffix = Path(source_name).suffix.lower()
    if suffix not in {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
        document.file.close()
        raise HTTPException(415, "Formato no soportado.")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with TemporaryDirectory(dir=UPLOAD_DIR) as temporary:
            source = Path(temporary) / ("document" + suffix)
            document.file.seek(0)
            with source.open("wb") as target:
                shutil.copyfileobj(document.file, target)
            if not source.is_file():
                raise HTTPException(500, "No se creó el archivo temporal del upload.")
            size = source.stat().st_size
            with source.open("rb") as uploaded:
                prefix = uploaded.read(8)
            if size == 0 or (suffix == ".pdf" and not prefix.startswith(b"%PDF-")):
                logger.warning("Upload rechazado: filename=%r size=%d prefix_hex=%s",
                               source_name, size, prefix.hex())
                detail = ("El archivo cargado está vacío." if size == 0 else
                          "El archivo cargado no comienza con la cabecera PDF %PDF-.")
                raise HTTPException(400, detail)
            logger.info("Upload validado: filename=%r size=%d prefix_hex=%s",
                        source_name, size, prefix.hex())
            try:
                prepared = DocumentPreparationService().prepare(source, PREPARED_DIR)
            except UnsupportedDocumentError as exc:
                raise HTTPException(415, str(exc)) from exc
            except (ValueError, RuntimeError) as exc:
                logger.exception("Falló preparación: filename=%r size=%d error_type=%s",
                                 source_name, size, type(exc).__name__)
                raise HTTPException(400, f"Falló la preparación ({type(exc).__name__}): {str(exc)[:300]}") from exc
            except OSError:
                logger.exception("Error de archivos durante preparación: filename=%r size=%d",
                                 source_name, size)
                raise
            prepared.source_name = source_name
            (prepared.output_dir / "manifest.json").write_text(
                prepared.model_dump_json(indent=2), encoding="utf-8"
            )
    finally:
        document.file.close()
    return RedirectResponse(f"/review/{prepared.document_id}", status_code=303)


def _safe_file(directory: Path, relative: Path) -> Path:
    root = PREPARED_DIR.resolve()
    directory = directory.resolve()
    candidate = (directory / relative).resolve()
    if (not directory.is_relative_to(root) or not candidate.is_relative_to(directory)
            or not candidate.is_file()):
        raise HTTPException(404, "Archivo no encontrado.")
    return candidate


def _load(document_id: str) -> tuple[Path, PreparedDocument]:
    if not re.fullmatch(r"[0-9a-f]{16}", document_id):
        raise HTTPException(404, "Documento no encontrado.")
    directory = PREPARED_DIR / document_id
    manifest = _safe_file(directory, Path("manifest.json"))
    data = json.loads(manifest.read_text(encoding="utf-8"))
    coordinate_fields = {"coordinate_width", "coordinate_height", "coordinate_unit"}
    if any(not coordinate_fields.issubset(page) for page in data["pages"]):
        raise HTTPException(409, "Este manifest no contiene metadatos de coordenadas. Vuelve a subir el documento desde /review.")
    return directory, PreparedDocument.model_validate(data)


def _page(prepared: PreparedDocument, number: int) -> PreparedPage:
    for page in prepared.pages:
        if page.page_number == number:
            return page
    raise HTTPException(404, "Página no encontrada.")


@router.get("/{document_id}", response_class=HTMLResponse)
def review_document(document_id: str, page: int = 1) -> HTMLResponse:
    directory, prepared = _load(document_id)
    current = _page(prepared, page)
    spans = json.loads(_safe_file(directory, current.text_path).read_text(encoding="utf-8"))
    unique_spans = deduplicate_text_spans([TextSpan.model_validate(span) for span in spans])
    text = "\n".join(span.text for span in unique_spans)
    base = f"/review/{document_id}"
    previous = f'<a href="{base}?page={page - 1}">Anterior</a>' if page > 1 else "Anterior"
    following = (f'<a href="{base}?page={page + 1}">Siguiente</a>'
                 if page < prepared.page_count else "Siguiente")
    overlay_link = (f'<p><a href="{base}/page/{page}/bbox-overlay">Ver bounding boxes</a></p>'
                    if current.text_span_count > 0 else "")
    return _html(f'''
        <h1>{escape(prepared.source_name)}</h1>
        <p>Tipo de documento: {escape(prepared.kind.value)}</p>
        <p>Página {page} / {prepared.page_count}</p>
        <p>Dimensiones del render: {current.width_px} × {current.height_px} px</p>
        <p>Dimensiones de coordenadas: {current.coordinate_width} × {current.coordinate_height}</p>
        <p>Unidad de coordenadas: {current.coordinate_unit.value}</p>
        <p>Texto nativo: {"Sí" if current.native_text_available else "No"}</p>
        <p>Text spans raw: {len(spans)}</p>
        <p>Text spans únicos: {len(unique_spans)}</p>
        <nav>{previous} | {following}</nav>
        {overlay_link}
        <p><a href="/review">Subir otro documento</a> |
           <a href="{base}/source">Descargar original preservado</a></p>
        <img src="{base}/pages/{page}/image" alt="Página preparada {page}"
             style="max-width:100%;height:auto">
        <h2>Texto nativo extraído — vista normalizada</h2><pre>{escape(text)}</pre>
        <p>Los spans RAW siguen preservados en disco. El overlay usa spans únicos.</p>
        <h2>Text spans RAW y bounding boxes originales</h2>
        <pre>{escape(json.dumps(spans, ensure_ascii=False, indent=2))}</pre>
    ''')


@router.get("/{document_id}/pages/{page_number}/image")
def page_image(document_id: str, page_number: int) -> FileResponse:
    directory, prepared = _load(document_id)
    page = _page(prepared, page_number)
    return FileResponse(_safe_file(directory, page.image_path), media_type="image/webp")


@router.get("/{document_id}/source")
def original_source(document_id: str) -> FileResponse:
    directory, prepared = _load(document_id)
    return FileResponse(_safe_file(directory, prepared.source_path),
                        media_type="application/octet-stream", filename=prepared.source_path.name)


@router.get("/{document_id}/page/{page_number}/bbox-overlay")
def bbox_overlay(document_id: str, page_number: int) -> Response:
    directory, prepared = _load(document_id)
    page = _page(prepared, page_number)
    spans = json.loads(_safe_file(directory, page.text_path).read_text(encoding="utf-8"))
    valid_spans = []
    for index, raw_span in enumerate(spans):
        try:
            valid_spans.append(TextSpan.model_validate(raw_span))
        except ValueError:
            logger.warning("Span inválido omitido del overlay: document_id=%s page=%d span=%d",
                           document_id, page_number, index)
    with Image.open(_safe_file(directory, page.image_path)) as original:
        image = original.convert("RGB")
    try:
        draw = ImageDraw.Draw(image)
        for index, span in enumerate(deduplicate_text_spans(valid_spans)):
            try:
                bbox = source_bbox_to_render_pixels(span.bbox, page)
            except ValueError:
                logger.warning("BBox omitida por coordenadas inválidas: document_id=%s page=%d span=%d",
                               document_id, page_number, index)
                continue
            if not (0 <= bbox.x0 <= bbox.x1 <= image.width
                    and 0 <= bbox.y0 <= bbox.y1 <= image.height):
                logger.warning("BBox omitida fuera del render: document_id=%s page=%d span=%d",
                               document_id, page_number, index)
                continue
            draw.rectangle((bbox.x0, bbox.y0, bbox.x1, bbox.y1), outline="red", width=1)
        with BytesIO() as buffer:
            image.save(buffer, format="PNG")
            return Response(buffer.getvalue(), media_type="image/png",
                            headers={"Cache-Control": "no-store"})
    finally:
        image.close()

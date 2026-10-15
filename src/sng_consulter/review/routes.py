import json
import hashlib
import re
import logging
import shutil
from html import escape
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from PIL import Image, ImageDraw, ImageFont
from pydantic import ValidationError

from sng_consulter.discovery.inputs import normalized_region_to_render_bbox
from sng_consulter.discovery.models import DiscoveryCandidate, DiscoveryResult
from sng_consulter.identity.inputs import build_identity_input
from sng_consulter.identity.models import IdentityResolutionProposal, IdentityResolutionResult
from sng_consulter.identity.visuals import candidate_crop

from sng_consulter.preparation import (
    DocumentPreparationService, deduplicate_text_spans, source_bbox_to_render_pixels,
)
from sng_consulter.preparation.models import PreparedDocument, PreparedPage, TextSpan
from sng_consulter.preparation.service import UnsupportedDocumentError
from sng_consulter.storage import (
    ContentIdentityConflictError, IncompatiblePreparedDocumentError,
    prepared_file, read_prepared_document, read_review_batch,
)

router = APIRouter(prefix="/review", tags=["review"])
UPLOAD_DIR = Path("runtime/review/uploads")
PREPARED_DIR = Path("runtime/review/prepared")
BATCH_DIR = Path("runtime/review/batches")
DISCOVERY_DIR = Path("runtime/discovery")
IDENTITY_DIR = Path("runtime/identity")
logger = logging.getLogger(__name__)


class IncompatiblePreparedManifestError(HTTPException):
    """An older persisted contract requires preparation, not inferred metadata."""

    def __init__(self) -> None:
        super().__init__(409, "Este manifest usa un contrato de preparación incompatible o incompleto. Vuelve a subir el documento desde /review.")


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
    try:
        batch_id = uuid4().hex
        entries = []
        for upload in document:
            entry = {"document_id": None, "original_name": upload.filename or "document", "status": "error"}
            try:
                prepared = _prepare_upload(upload)
            except HTTPException as exc:
                if exc.status_code not in {400, 415}:
                    raise
                entry.update(error=str(exc.detail), status_code=exc.status_code)
            else:
                entry.update(document_id=prepared.document_id, status="prepared")
            entries.append(entry)
        directory = BATCH_DIR / batch_id
        directory.mkdir(parents=True)
        (directory / "manifest.json").write_text(
            json.dumps({"batch_id": batch_id, "documents": entries}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return RedirectResponse(f"/review/batches/{batch_id}", status_code=303)
    finally:
        # The request handler is the sole owner of UploadFile closure.
        for upload in document:
            upload.file.close()


def _load_batch(batch_id: str) -> dict:
    try:
        return read_review_batch(BATCH_DIR, batch_id).model_dump(exclude_none=True)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Conjunto no encontrado.") from exc
    except ValueError as exc:
        raise HTTPException(409, "El manifest del conjunto es inválido.") from exc


@router.get("/batches/{batch_id}", response_class=HTMLResponse)
def review_batch(batch_id: str) -> HTMLResponse:
    batch = _load_batch(batch_id)
    rows = []
    successful = 0
    for index, entry in enumerate(batch["documents"]):
        name = escape(entry["original_name"])
        if entry["status"] == "prepared":
            successful += 1
            url = f'/review/{entry["document_id"]}?batch_id={batch_id}&occurrence={index}'
            rows.append(f'<li>{name}: Preparado — <a href="{url}">Revisar documento</a></li>')
        else:
            rows.append(f'<li>{name}: Error ({entry["status_code"]}) — {escape(entry["error"])}</li>')
    total = len(batch["documents"])
    discovery_section = "<p>No hay un resultado de Discovery guardado para este conjunto.</p>"
    if (DISCOVERY_DIR / batch_id / "discovery.json").exists():
        result, pages = _load_discovery(batch_id)
        links = [f'<li><a href="/review/batches/{batch_id}/discovery/{occurrence}/{number}">'
                 f'Revisar Discovery: ocurrencia {occurrence}, página {number}</a></li>'
                 for occurrence, number in pages]
        discovery_section = (f'<h2>Discovery — diagnóstico visual</h2><p>{len(result.candidates)} candidatos.</p>'
                             f'<ul>{"".join(links)}</ul>')
    identity_section = (f'<p><a href="/review/batches/{batch_id}/identity">Revisar Identity</a></p>'
                        if (IDENTITY_DIR / batch_id / "identity.json").exists() else "")
    return _html(f'<h1>Conjunto de revisión {batch_id}</h1>'
                 f'<p>Preparados: {successful} / {total}. Fallidos: {total - successful}.</p>'
                 f'<ul>{"".join(rows)}</ul>{discovery_section}{identity_section}'
                 '<p><a href="/review">Subir más documentos</a></p>')


def _load_identity(batch_id: str):
    _load_batch(batch_id)
    try:
        path = prepared_file(IDENTITY_DIR, IDENTITY_DIR / batch_id, Path("identity.json"))
        result = IdentityResolutionResult.model_validate_json(path.read_bytes())
        evidence = build_identity_input(batch_id, DISCOVERY_DIR, BATCH_DIR, PREPARED_DIR)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Identity o su evidencia no existe o su ruta no es válida.") from exc
    except ValueError as exc:
        raise HTTPException(409, "Identity o su evidencia contiene un contrato inválido.") from exc
    if result.batch_id != batch_id:
        raise HTTPException(409, "Identity pertenece a otro conjunto.")
    if result.source_covered_discovery_sha256 != evidence.source_covered_discovery_sha256:
        raise HTTPException(409, "Identity está desactualizado respecto a CoveredDiscovery. Vuelve a ejecutar Identity.")
    # Reuse the proposal contract to validate exclusivity without resolving again.
    try:
        proposal = IdentityResolutionProposal(
            groups=[group.model_dump(exclude={"identity_id"}) for group in result.identities],
            unresolved=[group.model_dump(exclude={"unresolved_id"}) for group in result.unresolved],
        )
    except ValidationError as exc:
        raise HTTPException(409, "Identity contiene candidatos con asignaciones duplicadas.") from exc
    candidates = {candidate.covered_candidate_id: candidate for candidate in evidence.candidates}
    assigned = {cid for group in [*proposal.groups, *proposal.unresolved] for cid in group.candidate_ids}
    resolved_count = sum(len(group.candidate_ids) for group in result.identities)
    unresolved_count = sum(len(group.candidate_ids) for group in result.unresolved)
    if (assigned != set(candidates) or result.covered_candidate_count != len(candidates)
            or result.resolved_candidate_count != resolved_count or result.unresolved_candidate_count != unresolved_count
            or len({g.identity_id for g in result.identities}) != len(result.identities)
            or len({g.unresolved_id for g in result.unresolved}) != len(result.unresolved)):
        raise HTTPException(409, "Identity debe representar cada candidato exactamente una vez y tener contadores e IDs coherentes.")
    return result, candidates


@router.get("/batches/{batch_id}/identity", response_class=HTMLResponse)
def review_identity(batch_id: str) -> HTMLResponse:
    result, candidates = _load_identity(batch_id)
    base = f"/review/batches/{batch_id}/identity"

    def members(candidate_ids):
        items = []
        for cid in candidate_ids:
            candidate = candidates[cid]
            items.append(f'<li><strong>{escape(cid)}</strong>'
                         f'<p>Documento: {candidate.document_id}. Ocurrencia: {candidate.occurrence}. '
                         f'Página: {candidate.page_number}.</p>'
                         f'<img src="{base}/candidates/{cid}/crop" alt="{escape(cid)}" '
                         'style="max-width:100%;max-height:60vh;width:auto;height:auto;object-fit:contain"></li>')
        return f'<ul>{"".join(items)}</ul>'

    identities = "".join(f'<section><h3>{group.identity_id}</h3>'
        f'<p>Referencia: {escape(group.reference_hint) if group.reference_hint is not None else "Sin referencia"}</p>'
        f'<p>{escape(group.description)}</p><p>Motivo: {escape(group.reason)}</p>'
        f'{members(group.candidate_ids)}</section>' for group in result.identities)
    unresolved = "".join(f'<section><h3>{group.unresolved_id}</h3><p>Motivo: {escape(group.reason)}</p>'
                         f'{members(group.candidate_ids)}</section>' for group in result.unresolved)
    return _html(f'<h1>Identity — revisión visual diagnóstica</h1>'
                 f'<p><a href="/review/batches/{batch_id}">Volver al conjunto</a></p>'
                 f'<h2>Identidades provisionales</h2>{identities or "<p>Sin identidades resueltas.</p>"}'
                 f'<h2>Unresolved</h2>{unresolved or "<p>Sin observaciones unresolved.</p>"}')


@router.get("/batches/{batch_id}/identity/candidates/{candidate_id}/crop")
def identity_candidate_crop(batch_id: str, candidate_id: str) -> Response:
    _, candidates = _load_identity(batch_id)
    if candidate_id not in candidates:
        raise HTTPException(404, "Candidato de Identity no encontrado.")
    candidate = candidates[candidate_id]
    with Image.open(candidate.image_path) as source:
        with source.convert("RGB") as image:
            try:
                crop = candidate_crop(image, candidate)
            except ValueError as exc:
                raise HTTPException(409, "El render no corresponde a las dimensiones preparadas.") from exc
            with crop, BytesIO() as buffer:
                crop.save(buffer, format="PNG")
                return Response(buffer.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})


def _load_discovery(batch_id: str) -> tuple[DiscoveryResult, dict[tuple[int, int], tuple[Path, PreparedPage]]]:
    batch = _load_batch(batch_id)
    try:
        path = prepared_file(DISCOVERY_DIR, DISCOVERY_DIR / batch_id, Path("discovery.json"))
    except FileNotFoundError as exc:
        raise HTTPException(404, "Discovery no existe para este conjunto o su ruta no es válida.") from exc
    try:
        result = DiscoveryResult.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValidationError, UnicodeDecodeError) as exc:
        raise HTTPException(409, "El resultado de Discovery es inválido.") from exc
    if result.batch_id != batch_id:
        raise HTTPException(409, "Discovery pertenece a otro conjunto.")
    if len({candidate.candidate_id for candidate in result.candidates}) != len(result.candidates):
        raise HTTPException(409, "Discovery contiene candidate_id duplicados.")
    documents = {}
    pages = {}
    for candidate in result.candidates:
        evidence = candidate.evidence
        if not 0 <= evidence.occurrence < len(batch["documents"]):
            raise HTTPException(409, "Discovery referencia una ocurrencia inexistente.")
        entry = batch["documents"][evidence.occurrence]
        if entry["status"] != "prepared" or entry["document_id"] != evidence.document_id:
            raise HTTPException(409, "Discovery no corresponde al documento de la ocurrencia.")
        if evidence.document_id not in documents:
            documents[evidence.document_id] = _load(evidence.document_id)
        directory, prepared = documents[evidence.document_id]
        try:
            page = _page(prepared, evidence.page_number)
        except HTTPException as exc:
            raise HTTPException(409, "Discovery referencia una página inexistente.") from exc
        _safe_file(directory, page.image_path)
        pages[(evidence.occurrence, evidence.page_number)] = (directory, page)
    return result, pages


def _discovery_page(batch_id: str, occurrence: int, page_number: int):
    result, pages = _load_discovery(batch_id)
    if (occurrence, page_number) not in pages:
        raise HTTPException(404, "No hay candidatos de Discovery para esta ocurrencia/página.")
    candidates = [candidate for candidate in result.candidates
                  if candidate.evidence.occurrence == occurrence and candidate.evidence.page_number == page_number]
    directory, page = pages[(occurrence, page_number)]
    return directory, page, candidates


def _candidate_label(candidate: DiscoveryCandidate) -> str:
    parts = [candidate.candidate_id]
    if candidate.reference_hint is not None:
        parts.append(candidate.reference_hint)
    parts.append(candidate.glass_relevance.value)
    return " · ".join(parts)


@router.get("/batches/{batch_id}/discovery/{occurrence}/{page_number}", response_class=HTMLResponse)
def review_discovery_page(batch_id: str, occurrence: int, page_number: int) -> HTMLResponse:
    _, page, candidates = _discovery_page(batch_id, occurrence, page_number)
    base = f"/review/batches/{batch_id}/discovery/{occurrence}/{page_number}"
    descriptions = "".join(f'<li><strong>{escape(_candidate_label(candidate))}</strong>'
                           f'<p>{escape(candidate.description)}</p></li>' for candidate in candidates)
    original_url = f"/review/{candidates[0].evidence.document_id}/pages/{page_number}/image"
    return _html(f'''
        <h1>Discovery — validación visual</h1>
        <p>Ocurrencia {occurrence}, página {page_number}. Render: {page.width_px} × {page.height_px} px.</p>
        <p><a href="/review/batches/{batch_id}">Volver al conjunto</a> |
           <a href="{original_url}" target="_blank" rel="noopener">Ver WEBP preparado</a> |
           <a href="{base}/overlay" target="_blank" rel="noopener">Ver overlay en tamaño completo</a></p>
        <p>LIKELY: verde. POSSIBLE: naranja. Esta vista solo muestra observaciones guardadas.</p>
        <img src="{base}/overlay" alt="Candidatos de Discovery"
             style="max-width:100%;max-height:80vh;width:auto;height:auto;object-fit:contain">
        <h2>Candidatos de esta ocurrencia/página</h2><ul>{descriptions}</ul>
    ''')


@router.get("/batches/{batch_id}/discovery/{occurrence}/{page_number}/overlay")
def discovery_overlay(batch_id: str, occurrence: int, page_number: int) -> Response:
    directory, page, candidates = _discovery_page(batch_id, occurrence, page_number)
    with Image.open(_safe_file(directory, page.image_path)) as original:
        image = original.convert("RGB")
    try:
        if image.size != (page.width_px, page.height_px):
            raise HTTPException(409, "El WEBP no corresponde a las dimensiones del manifest.")
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default(size=14)
        for candidate in candidates:
            bbox = normalized_region_to_render_bbox(candidate.evidence.region, page.width_px, page.height_px)
            color = "green" if candidate.glass_relevance.value == "LIKELY" else "darkorange"
            draw.rectangle((bbox.x0, bbox.y0, bbox.x1, bbox.y1), outline=color, width=2)
            label = _candidate_label(candidate)
            lines, line = [], ""
            for character in label:
                if character == "\n":
                    lines.append(line)
                    line = ""
                elif line and draw.textlength(line + character, font=font) > max(1, image.width - 4):
                    lines.append(line)
                    line = character
                else:
                    line += character
            lines.append(line)
            label = "\n".join(lines)
            bounds = draw.multiline_textbbox((0, 0), label, font=font)
            label_width, label_height = bounds[2] - bounds[0], bounds[3] - bounds[1]
            x = min(bbox.x0, max(0, image.width - label_width - 4))
            y = max(0, bbox.y0 - label_height - 6)
            draw.rectangle((x, y, x + label_width + 4, y + label_height + 4), fill="white")
            draw.multiline_text((x + 2, y + 2 - bounds[1]), label, fill=color, font=font)
        with BytesIO() as buffer:
            image.save(buffer, format="PNG")
            return Response(buffer.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})
    finally:
        image.close()


def _occurrence_name(document_id: str, batch_id: str | None, occurrence: int | None) -> str | None:
    if batch_id is None and occurrence is None:
        return None
    if batch_id is None or occurrence is None:
        raise HTTPException(404, "Ocurrencia no encontrada.")
    entries = _load_batch(batch_id)["documents"]
    if not 0 <= occurrence < len(entries):
        raise HTTPException(404, "Ocurrencia no encontrada.")
    entry = entries[occurrence]
    if entry["status"] != "prepared" or entry["document_id"] != document_id:
        raise HTTPException(404, "Ocurrencia no encontrada.")
    return entry["original_name"]


def _download_name(name: str) -> str:
    # Strip both Unix and Windows path components and header/control characters.
    basename = name.replace("\\", "/").rsplit("/", 1)[-1]
    basename = "".join(char for char in basename if ord(char) >= 32 and ord(char) != 127)
    basename = re.sub(r'[<>:"|?*]', "_", basename)
    basename = basename.strip().rstrip(". ")
    return basename if basename not in {"", ".", ".."} else "document"


def _prepare_upload(document: UploadFile) -> PreparedDocument:
    # The client name is metadata only; it never becomes a filesystem path.
    source_name = document.filename or "document"
    suffix = Path(source_name).suffix.lower()
    if suffix not in {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
        raise HTTPException(415, "Formato no soportado.")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
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
        with source.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        document_id = digest[:16]
        if (PREPARED_DIR / document_id / "manifest.json").is_file():
            try:
                _, existing = _load(document_id, expected_sha256=digest)
            except IncompatiblePreparedManifestError:
                # The atomic service retains the old version until publication.
                pass
            else:
                return existing
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
    return prepared


def _safe_file(directory: Path, relative: Path) -> Path:
    try:
        return prepared_file(PREPARED_DIR, directory, relative)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Archivo no encontrado.") from exc


def _load(document_id: str, expected_sha256: str | None = None) -> tuple[Path, PreparedDocument]:
    try:
        return read_prepared_document(PREPARED_DIR, document_id, expected_sha256)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Documento no encontrado.") from exc
    except ContentIdentityConflictError as exc:
        raise HTTPException(409, "Conflicto de identidad del contenido.") from exc
    except IncompatiblePreparedDocumentError as exc:
        raise IncompatiblePreparedManifestError() from exc


def _page(prepared: PreparedDocument, number: int) -> PreparedPage:
    for page in prepared.pages:
        if page.page_number == number:
            return page
    raise HTTPException(404, "Página no encontrada.")


@router.get("/{document_id}", response_class=HTMLResponse)
def review_document(document_id: str, page: int = 1, batch_id: str | None = None,
                    occurrence: int | None = None) -> HTMLResponse:
    directory, prepared = _load(document_id)
    original_name = _occurrence_name(document_id, batch_id, occurrence) or prepared.source_name
    context = f"&batch_id={batch_id}&occurrence={occurrence}" if batch_id else ""
    back_link = f'<p><a href="/review/batches/{batch_id}">Volver al conjunto</a></p>' if batch_id else ""
    current = _page(prepared, page)
    spans = json.loads(_safe_file(directory, current.text_path).read_text(encoding="utf-8"))
    unique_spans = deduplicate_text_spans([TextSpan.model_validate(span) for span in spans])
    text = "\n".join(span.text for span in unique_spans)
    base = f"/review/{document_id}"
    previous = f'<a href="{base}?page={page - 1}{context}">Anterior</a>' if page > 1 else "Anterior"
    following = (f'<a href="{base}?page={page + 1}{context}">Siguiente</a>'
                 if page < prepared.page_count else "Siguiente")
    overlay_link = (f'<p><a href="{base}/page/{page}/bbox-overlay">Ver bounding boxes</a></p>'
                    if current.text_span_count > 0 else "")
    text_sections = (f'''
        <h2>Texto nativo extraído — vista normalizada</h2><pre>{escape(text)}</pre>
        <p>Los spans RAW siguen preservados en disco. El overlay usa spans únicos.</p>
        <h2>Text spans RAW y bounding boxes originales</h2>
        <pre>{escape(json.dumps(spans, ensure_ascii=False, indent=2))}</pre>
    ''' if spans else "")
    return _html(f'''
        <h1>{escape(original_name)}</h1>
        <p>Tipo de documento: {escape(prepared.kind.value)}</p>
        <p>Página {page} / {prepared.page_count}</p>
        <p>Dimensiones del render: {current.width_px} × {current.height_px} px</p>
        <p>Dimensiones de coordenadas: {current.coordinate_width} × {current.coordinate_height}</p>
        <p>Unidad de coordenadas: {current.coordinate_unit.value}</p>
        <p>Texto nativo: {"Sí" if current.native_text_available else "No"}</p>
        <p>Text spans raw: {len(spans)}</p>
        <p>Text spans únicos: {len(unique_spans)}</p>
        <nav>{previous} | {following}</nav>
        {back_link}
        {overlay_link}
        <p><a href="/review">Subir otro documento</a> |
           <a href="{base}/source{("?" + context[1:]) if context else ""}">Descargar original preservado</a></p>
        <img src="{base}/pages/{page}/image" alt="Página preparada {page}"
             style="max-width:100%;max-height:80vh;width:auto;height:auto;object-fit:contain">
        <p><a href="{base}/pages/{page}/image" target="_blank" rel="noopener">Ver imagen preparada en tamaño completo</a></p>
        {text_sections}
    ''')


@router.get("/{document_id}/pages/{page_number}/image")
def page_image(document_id: str, page_number: int) -> FileResponse:
    directory, prepared = _load(document_id)
    page = _page(prepared, page_number)
    return FileResponse(_safe_file(directory, page.image_path), media_type="image/webp")


@router.get("/{document_id}/source")
def original_source(document_id: str, batch_id: str | None = None,
                    occurrence: int | None = None) -> FileResponse:
    directory, prepared = _load(document_id)
    name = _occurrence_name(document_id, batch_id, occurrence) or prepared.source_name
    return FileResponse(_safe_file(directory, prepared.source_path),
                        media_type="application/octet-stream", filename=_download_name(name))


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

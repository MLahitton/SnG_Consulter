# SnG Consulter

Servicio de preparación documental para Steel & Glass. Estado actual: **Fase 1**.

## Preparación disponible

- PDF multipágina y JPG/JPEG/PNG/WEBP/BMP. TIFF no está soportado.
- Original preservado byte por byte en `source/`.
- Todas las páginas renderizadas a WEBP; render PDF base a 180 DPI.
- Texto nativo de PDF y bounding boxes RAW en `text/`, sin OCR ni interpretación.
- `manifest.json` con identidad del contenido, páginas, rutas y dimensiones.
- Dimensiones de origen (`coordinate_width`, `coordinate_height`, `coordinate_unit`)
  separadas de las dimensiones del render (`width_px`, `height_px`).
  PDF usa `PDF_POINT`; imágenes usan `PIXEL`.
- `source_bbox_to_render_pixels()` escala una bbox sin modificar el original.
- `deduplicate_text_spans()` ofrece una vista no destructiva: texto exactamente
  igual y los cuatro extremos de bbox dentro de 0.01 unidades de origen.
  Conserva la primera ocurrencia y el orden. Los archivos RAW no cambian.

## Estructura local

```text
runtime/review/
  uploads/                        # temporales; se limpian al terminar o fallar
  prepared/<document_id>/          # identidad por hash del contenido
    manifest.json
    source/document.<ext>
    pages/0001.webp
    text/0001.json                 # spans RAW; una entrada por página
  batches/<batch_id>/
    manifest.json                 # referencias; sin copias de los documentos
```

`runtime/` está ignorado por Git. Las salidas de preparación también pueden
producirse bajo otra raíz mediante `DocumentPreparationService`.

## Review Harness

Abre `http://127.0.0.1:8000/review` para seleccionar uno o varios documentos o
fotos. `POST /review/upload` usa el mismo servicio de preparación.

Una carga individual conserva la redirección directa al documento. Si reutiliza
contenido ya preparado con otro nombre, crea un conjunto con esa ocurrencia
para conservar el nombre sin sobrescribir metadata. Una
multisubida crea un conjunto persistente en `/review/batches/<batch_id>`.
Cada ocurrencia conserva su nombre y resultado; archivos de contenido idéntico
referencian el mismo documento preparado, sin reprocesarlo ni reemplazar su
metadata. La pantalla del conjunto muestra también los errores de archivos
inválidos, sin perder los documentos válidos.

Cada entrada abre `/review/<document_id>?batch_id=<batch_id>&occurrence=<índice>`.
El índice corresponde al orden de selección, empezando en cero. La navegación
por páginas conserva ese contexto y ofrece volver al conjunto. Los documentos
siguen independientes; el batch no los fusiona.

La revisión muestra dimensiones, unidades, texto normalizado, contadores RAW y
únicos, y los spans RAW con sus bounding boxes. **Ver bounding boxes** genera
un PNG en memoria desde el WEBP preparado, usando los spans únicos. No altera
el manifest, el WEBP ni el texto. Rectángulos inválidos o fuera de límites se
omiten con registro. El enlace se oculta en páginas sin spans.

`/review/<document_id>/source` descarga los bytes preservados con un basename
seguro del nombre original. Con contexto de batch usa el nombre de esa
ocurrencia. El nombre recibido nunca se usa como ruta del upload.

El manifest de batch contiene `batch_id` y una lista ordenada `documents`.
Cada entrada tiene `document_id`, `original_name` y `status` (`prepared` o
`error`). Un error tiene `document_id: null`, `status_code` y `error` para explicar
el resultado. No hay base de datos ni archivos de texto normalizado adicionales.

## Desarrollo local

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m uvicorn sng_consulter.api:app --app-dir src --reload --host 127.0.0.1 --port 8000
```

Validación automatizada:

```powershell
python -m pytest tests/test_review.py
python -m pytest
```

## Validación de Fase 1

La aceptación incluye revisión manual documento por documento y página por
página con el Harness, además de tests automatizados. El benchmark se usa después
como regresión de los casos ya revisados; no es la única forma de validar.
Consulta [criterios de aceptación](docs/PHASE_1_ACCEPTANCE.md).

Esta fase no ejecuta IA, OCR, Discovery, extracción técnica ni interpretación
arquitectónica. Es una herramienta local de revisión, sin autenticación.

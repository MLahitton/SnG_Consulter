# SnG Consulter

Servicio de preparación documental para Steel & Glass. Estado actual: **Fase 1**.

## Preparación disponible

- Formatos implementados y probados: PDF, JPG, JPEG, PNG, WEBP y BMP.
  TIFF, DOCX, XLSX, DWG, HEIC y otros formatos no están soportados.
- Original preservado byte por byte en `source/`.
- La orientación EXIF de imágenes se aplica con `ImageOps.exif_transpose()` antes
  de guardar el WEBP. Dimensiones y coordenadas `PIXEL` describen la imagen visible
  ya orientada, con matriz identidad explícita; el original no se modifica.
- Todas las páginas renderizadas a WEBP; render PDF base a 180 DPI.
- Texto nativo de PDF y bounding boxes RAW en `text/`, sin OCR ni interpretación.
  Un PDF sin texto nativo sigue siendo válido: conserva todas sus páginas
  visuales con spans vacíos. Las imágenes no reciben texto inventado.
- `manifest.json` con identidad del contenido, páginas, rutas y dimensiones.
- Dimensiones de origen (`coordinate_width`, `coordinate_height`, `coordinate_unit`)
  separadas de las dimensiones del render (`width_px`, `height_px`).
  PDF usa `PDF_POINT`; imágenes usan `PIXEL`.
- `source_bbox_to_render_pixels()` transforma una bbox sin modificar el original.
  Cada página conserva `source_to_visible_matrix`: `page.rotation_matrix` en
  PDF e identidad en imágenes. Se transforman las cuatro esquinas y luego se
  escala al render. Las bbox RAW de PyMuPDF no están rotadas; `page.rect` y el
  render sí. PyMuPDF ya normaliza el origen visible del CropBox, también con
  MediaBox desplazado, por lo que no se aplica una segunda traslación del crop.
- El contrato persistido actual lleva `preparation_contract_version: 1` y exige
  matriz explícita en cada página PDF/IMAGE. `/review` rechaza con 409 los manifests
  anteriores sin versión, coordenadas o matriz; resubir el mismo contenido los
  regenera mediante staging, sin borrar primero la preparación anterior.
  No hay migración por inferencia ni un pipeline alternativo. La versión distingue
  también imágenes anteriores a la normalización EXIF que ya tenían matriz identidad.
- `deduplicate_text_spans()` ofrece una vista no destructiva: texto exactamente
  igual y los cuatro extremos de bbox dentro de 0.01 unidades de origen.
  Conserva la primera ocurrencia y el orden. Los archivos RAW no cambian.

## Regla de arquitectura

SnG_Consulter es document-agnostic: la preparación depende del formato,
contenido, dimensiones y texto nativo observables, no de clientes, nombres
concretos de archivos, páginas conocidas, posiciones fijas ni layouts de fixtures.
Los documentos usados durante construcción son exclusivamente casos de prueba.
Los nombres originales se conservan como metadata de ocurrencia y descarga;
no determinan cómo interpretar o preparar una página.

Existe un único flujo: `DocumentPreparationService.prepare()` dirige los
tratamientos de entrada PDF e imagen al mismo contrato `PreparedDocument` y
`PreparedPage`. La regla para añadir formatos es adaptar la entrada manteniendo
ese contrato y la semántica de consumo. Actualmente el despacho está implementado
por extensión y métodos PDF/imagen; no hay un registro o framework de adaptadores.
Esta auditoría no añade formatos ni implementa fases posteriores.

La relación conceptual es:

```text
ReviewBatch
  └── PreparedDocument[] (referencias; una por ocurrencia válida)
        ├── source/
        ├── pages/
        ├── text/ (RAW)
        └── manifest.json
```

El batch guarda referencias y resultados, no directorios de documentos propios.
Varias ocurrencias pueden referenciar el mismo contenido preparado; una entrada
fallida registra su error y no tiene `PreparedDocument`.

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

El servicio prepara source, páginas, texto y manifest dentro de `.staging-*`
en la misma raíz de salida. El manifest contiene las rutas finales. Solo tras
completar todo publica el directorio mediante rename. Si hay una versión previa,
se conserva hasta ese momento y se restaura si falla la publicación. El staging
se elimina ante éxito o excepción; no queda un `PreparedDocument` parcial.
Si también falla la restauración por un error del sistema de archivos, los bytes
anteriores permanecen en `.previous-*` para recuperación manual.

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
omiten con registro. En páginas sin texto ni spans se ocultan el enlace de
overlay y las secciones vacías de texto y spans; los contadores permanecen.
La imagen se presenta sin deformación, con ancho máximo de 100% y altura máxima
de 80vh. El enlace de tamaño completo abre el WEBP existente sin generar copias.

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

La validación de construcción se ha realizado manualmente documento por documento
y página por página con el Harness, además de tests automatizados. El benchmark
completo queda pendiente como regresión posterior; no es la única forma de validar
ni se declara ejecutado por esta auditoría.
Consulta [criterios de aceptación](docs/PHASE_1_ACCEPTANCE.md).

Esta fase no ejecuta IA, OCR, Discovery, extracción técnica ni interpretación
arquitectónica. Es una herramienta local de revisión, sin autenticación.

## Límites actuales de Fase 1

- Rotaciones PDF 0/90/180/270, CropBox distinto del MediaBox y MediaBox con origen
  desplazado están comprobados con tests sintéticos y overlay. Preparados antiguos
  incompatibles requieren resubida para su regeneración antes de revisar overlays.
- La preparación es síncrona, carga el PDF en memoria y no tiene escritura
  durable frente a corte de proceso/sistema ni bloqueo para uploads concurrentes
  del mismo contenido. El reemplazo usa dos renames, con una breve ventana sin
  directorio final; no es una transacción del sistema de archivos.
- Excepciones controladas limpian staging y uploads. Un cierre abrupto del proceso
  puede dejar staging o backups; no hay recuperación ni purga automática.
- No se migran metadatos antiguos por inferencia. La regeneración requiere disponer
  del archivo original para resubirlo; si falla, la preparación anterior se conserva.
- El cierre de un caso real requiere su revisión manual. La suite pasando no
  certifica por sí sola todos los documentos ni el benchmark completo.

# SnG Consulter

Servicio de preparación documental para Steel & Glass. Fase 1 cerrada;
estado actual: **3B.1 — Independent Identity Audit**.

## Discovery 2A.1 y 2A.2

`discovery/` define contratos y orquestación por página, con `OpenAIPageAnalyzer`
como adaptador del SDK oficial a Responses API. `build_discovery_input()`
lee un batch persistido y sus documentos mediante `storage.py`, el lector
validado común con Review Harness. No modifica los archivos de Fase 1.

Todas las páginas de ocurrencias válidas participan, incluidas páginas sin texto.
Entradas con error se omiten conservando los índices de las demás ocurrencias.
Los nombres originales no forman parte de `DiscoveryInput`. El texto nativo es
la vista deduplicada de Fase 1; sus bbox de entrada se expresan en píxeles visuales
del render, aplicando la conversión ya existente. RAW sigue intacto.

`NormalizedRegion` representa un rectángulo no vacío entre 0 y 1 en el espacio
visual preparado, con origen superior izquierdo. Los helpers permiten convertir
entre bbox de píxeles y región normalizada, sin recortar datos inválidos.

`DiscoveryService` recibe un `PageAnalyzer` intercambiable y asigna IDs
`candidate-0001`, `candidate-0002`, etc., en orden de página y propuesta.
Cada candidato mantiene descripción, referencia provisional, relevancia de
vidrio (`LIKELY`/`POSSIBLE`) y evidencia con documento, ocurrencia, página,
región y tipo (`VISUAL`/`NATIVE_TEXT`/`COMBINED`). No son estructuras definitivas;
no se fusionan referencias iguales ni candidatos entre páginas.

Los resultados se publican por archivo temporal y reemplazo en
`runtime/discovery/<batch_id>/discovery.json`. Un fallo no publica resultados
parciales ni modifica un resultado anterior. No se cambia el batch ni el preparado.
Los tests usan analizadores fake o el SDK oficial con transporte HTTP simulado;
no hacen llamadas reales a OpenAI. Hay una vista de lectura diagnóstica para
resultados existentes; no es una UI productiva ni un endpoint para ejecutar
Discovery. No hay reconciliación de identidad, OCR ni extracción técnica.

### Validación visual de Discovery

Desde `/review/batches/<batch_id>`, si existe
`runtime/discovery/<batch_id>/discovery.json`, aparecen enlaces por ocurrencia/página
con candidatos. `/review/batches/<batch_id>/discovery/<occurrence>/<page_number>`
muestra el overlay y la lista de candidate_id, referencia provisional si existe,
relevancia y descripción. La vista ofrece volver al conjunto, abrir el WEBP
preparado y abrir el overlay a tamaño completo.

El endpoint de imagen termina en `/overlay` y devuelve un PNG generado en memoria,
con las dimensiones originales del render. Reutiliza
`normalized_region_to_render_bbox()`; dibuja LIKELY en verde y POSSIBLE en naranja,
con etiquetas identificables. No modifica ni guarda copias de preparados, manifests
o resultados. Solo muestra candidatos de la ocurrencia y página seleccionadas.

Antes de mostrar resultados se valida el schema, la pertenencia al batch y todas
las referencias de ocurrencia/documento/página. Las rutas quedan contenidas en
las raíces de Discovery y Review. Un resultado ausente devuelve 404 con explicación;
un contrato o referencia inválida devuelve 409. La página del conjunto indica
si todavía no existe resultado. Esta herramienta no analiza ni llama a OpenAI.

### OpenAIPageAnalyzer y ejecución manual

Configuración: `OPENAI_API_KEY` es obligatoria y `SNG_DISCOVERY_MODEL` selecciona
el modelo; por defecto `gpt-6.1-sol`. La clave solo se entrega al cliente SDK:
no se incluye en prompts, resultados ni logs de la aplicación. Los errores
públicos omiten cuerpos originales del SDK y las cadenas de validación del modelo.

Cada página envía el WEBP preparado completo mediante una data URL base64, sin
leer el original ni redimensionar el archivo. `detail="high"` es el valor por
defecto para comprensión visual de alta fidelidad; el argumento interno
`image_detail="original"` permite otro detalle cuando el modelo lo soporte.
El servicio no cambia. El proveedor puede aplicar su propio preprocesamiento;
en planos densos la legibilidad y el recall requieren validación manual.

El prompt pide todas las observaciones razonables relacionadas con vidrio,
incluidas inciertas (`POSSIBLE`), sin asumir que cualquier rectángulo sea vidrio.
No pide cantidades, medidas, módulos, paneles, sistemas ni precios definitivos.
Los nombres y metadatos de identidad no se envían. El texto nativo complementa
la imagen en JSON compacto: `render_size` y pares `[text,[x0,y0,x1,y1]]` de píxeles
visuales, redondeados a tres decimales solo en el payload; se omiten índices internos.
Los RAW y los contratos de 2A.1 no cambian.

Se usa `client.responses.parse(text_format=...)` con un sobre Pydantic
`{"proposals": [...]}`. El SDK genera JSON Schema estricto y valida la salida;
el adaptador devuelve `list[CandidateProposal]`. Regiones inválidas, refusals,
respuestas incompletas o texto no estructurado fallan, sin reparar candidatos.
No hay fallback a una lista vacía ni reconciliación de referencias.

Los errores se distinguen como configuración, fallo de API y respuesta inválida.
El cliente tiene timeout de 180 segundos y cero retries automáticos; no hay
crops ni segundas pasadas. `store=False` evita almacenar la respuesta como recurso
de Responses; no sustituye las políticas de retención del proveedor.
`DiscoveryService` sigue publicando solo cuando todas las páginas terminan.

Para ejecutar manualmente un batch existente desde la raíz del repositorio:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
$discoveryCredential = Read-Host "OPENAI_API_KEY" -AsSecureString
$env:OPENAI_API_KEY = [System.Net.NetworkCredential]::new("", $discoveryCredential).Password
$env:SNG_DISCOVERY_MODEL = "gpt-6.1-sol"
python -m sng_consulter.discovery "BATCH_ID"
```

Reemplaza `BATCH_ID` por el identificador de `/review/batches/<batch_id>`.
Este comando hace llamadas reales y envía las páginas y texto nativo preparado
a OpenAI. Publica `runtime/discovery/<batch_id>/discovery.json`; una nueva ejecución
reemplaza el resultado previo solo al completar. No es un endpoint productivo.
El CLI devuelve 0 si completa y 1 ante los errores de configuración/API/contrato
gestionados. Los manifests preparados malformados o incompletos se rechazan con
`IncompatiblePreparedDocumentError`, sin accesos accidentales a campos ausentes.

Referencia: [salida estructurada de Responses](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses),
[imágenes y detalle](https://developers.openai.com/api/docs/guides/images-vision).
La integración está probada sin red; acceso real al modelo, costo, latencia,
recall y precisión espacial quedan pendientes de la prueba manual del batch.

## Coverage Audit 2B.1

`discovery/coverage/` realiza una segunda revisión multimodal exclusivamente de
posibles omisiones. Lee `discovery.json` persistido; nunca ejecuta Discovery
automáticamente. Valida batch, documentos, ocurrencias, páginas e IDs únicos antes
de auditar todas las páginas válidas, incluso aquellas sin candidatos.

Cada llamada recibe el WEBP completo, texto nativo visual y candidatos locales como
`[candidate_id, reference_hint, [x0,y0,x1,y1], glass_relevance]`. Comparte configuración,
cliente, base64, parser y errores seguros de Responses con Discovery; su prompt
original permanece intacto. El prompt de Coverage pide solo objetos separados
omitidos: una región imperfecta del mismo objeto cuenta como cubierta; tampoco
se repiten vistas ni se mejoran bbox. Ante duda razonable conserva `POSSIBLE`.
Esta decisión es multimodal, sin filtrado geométrico, reconciliación ni fusión.

`CoverageAuditProposal` contiene referencia nullable, descripción, región normalizada,
relevancia y motivo. `CoverageAuditPageResult` añade documento, ocurrencia y página.
`CoverageAuditResult` contiene batch, `audited_discovery_sha256`, `pages_audited` y omisiones con esos mismos
datos e IDs propios `coverage-0001`, distintos de `DiscoveryCandidate`.
No son estructuras definitivas.

Con el entorno OpenAI ya configurado:

```powershell
python -m sng_consulter.discovery coverage "BATCH_ID"
```

Hace llamadas reales a Responses con salida estructurada estricta y cero retries.
Publica `runtime/discovery/<batch_id>/coverage_audit.json` mediante temporal y
reemplazo atómico solo tras éxito completo. Un fallo conserva el audit anterior;
`discovery.json`, preparados y batch permanecen intactos. No hay crops ni UI nueva.
Los tests simulan la frontera HTTP; cobertura real, falsos positivos, duplicados
semánticos, costo y latencia requieren prueba manual antes de validar esta subfase.

## Consolidación 2C — Covered Discovery Result

```powershell
python -m sng_consulter.discovery consolidate "BATCH_ID"
```

Lee exclusivamente `runtime/discovery/<batch_id>/discovery.json` y
`coverage_audit.json`, ambos obligatorios, con validación de contrato, batch y
conteo de páginas. No lee preparados ni llama OpenAI; no necesita API key.
Concatena candidatos en orden persistido: Discovery primero, Coverage después.
No fusiona, deduplica ni resuelve identidad, aunque las referencias coincidan.

`CoveredCandidate` contiene `covered_candidate_id` (`covered-0001`, etc.),
`origin` (`DISCOVERY`/`COVERAGE_AUDIT`), `source_candidate_id`, referencia nullable,
descripción, relevancia y evidencia. Discovery conserva su evidencia completa;
Coverage conserva documento, ocurrencia, página, región y motivo en `evidence`,
sin inventar un tipo de evidencia que su contrato no contiene.
`CoveredDiscoveryResult` contiene batch, candidatos y contadores `discovery_count`,
`coverage_added_count` y `total_candidates`.

Publica `covered_discovery.json` mediante staging y reemplazo atómico; los mismos
inputs producen los mismos IDs y bytes. Fallos controlados limpian staging y
conservan un resultado previo. Los dos JSON fuente permanecen intactos.
La consolidación no certifica recall ni elimina posibles duplicados semánticos.
Coverage conserva el SHA-256 de los bytes exactos de `discovery.json` que leyó
para auditar. Consolidación exige que coincida con los bytes actuales, incluso si
solo cambió el formato del JSON. Si no coincide, rechaza el audit desactualizado
y exige ejecutarlo nuevamente, sin hacerlo automáticamente. Audits anteriores sin
hash son incompatibles; no se infieren hashes ni se migran resultados.

## Identity 3A.1 — Contratos, evidencia y orquestación

`CoveredDiscovery` contiene observaciones cubiertas. `IdentityResolution` agrupa
esas observaciones por identidad provisional; no es Technical Extraction ni
determina cantidades, medidas o especificaciones.

`identity/` ensambla todos los candidatos del `covered_discovery.json` canónico
con sus páginas preparadas, usando los lectores validados de storage. Conserva
documento, ocurrencia, página, región, imagen y dimensiones del render. Los nombres
originales no pertenecen al input analítico. SHA-256 corresponde a los bytes
exactos consumidos, sin reserializar el modelo.

`IdentityResolver` es un Protocol con el adapter `OpenAIIdentityResolver` de 3A.2.
`IdentityResolutionService` exige que la propuesta cubra cada candidato
exactamente una vez, resuelto o `unresolved`, sin IDs desconocidos ni asignaciones
dobles. No hay heurísticas, deduplicación o matching por referencias: una referencia
igual puede dar identidades distintas y referencias diferentes pueden agruparse.
Una asociación ambigua permanece `unresolved` con motivo explícito.

El servicio asigna IDs determinísticos por primera aparición de los candidatos en
el input canónico y publica `runtime/identity/<batch_id>/identity.json` mediante
staging y reemplazo atómico. Fallos controlados conservan el resultado previo.
No modifica artefactos de Fase 1/2. Véase el [contrato de Identity](docs/PHASE_3_IDENTITY.md).

### OpenAI Identity 3A.2

Configura `OPENAI_API_KEY` y, opcionalmente, `SNG_IDENTITY_MODEL` (por defecto
`gpt-6.1-sol`, independiente de `SNG_DISCOVERY_MODEL`). Con el venv activo:

```powershell
python -m sng_consulter.identity "BATCH_ID"
```

El comando consume el CoveredDiscovery ya persistido, sin ejecutar fases previas.
Envía una única solicitud Responses con metadata compacta de todos los candidatos
y contact sheets PNG creados en memoria: seis tiles por sheet, crops con margen
contextual del 5% por eje, limitados al render, sin deformación y con ID visible
fuera del dibujo. No persiste crops, boards, requests ni responses del proveedor.
Solo se publica el resultado canónico validado en `identity.json`.

La salida estructurada usa exactamente `IdentityResolutionProposal` de 3A.1.
Identity agrupa ítems/diseños provisionales; repeticiones visuales no determinan
cantidad. Observaciones compuestas, ambiguas o posibles falsos positivos pueden
permanecer unresolved, sin descartarse ni duplicarse.

Límites operativos: 80 candidatos y 120.000 bytes UTF-8 de metadata por solicitud;
excederlos produce `IdentityContextBudgetExceeded` antes de la llamada, sin truncar.
Timeout: 180 segundos; `store=False`; cero retries. No hay second resolver,
chunking, extracción técnica ni endpoint productivo. La fidelidad visual,
agrupación, costo y latencia requieren prueba manual real.

### Revisión visual diagnóstica de Identity

Cuando existe `runtime/identity/<batch_id>/identity.json`, la pantalla del batch
ofrece **Revisar Identity** en `/review/batches/<batch_id>/identity`.
Se muestran identidades provisionales y unresolved por separado, con referencia
cuando corresponde, descripción, motivo, IDs de candidatos y su evidencia visual.
Cada crop se sirve como PNG en memoria desde
`/review/batches/<batch_id>/identity/candidates/<covered_candidate_id>/crop`,
usando el mismo helper de crop contextual de Identity y preservando proporciones.

Antes de servir la vista o un crop se validan batch, hash de los bytes actuales
de CoveredDiscovery, cobertura exacta de candidatos, contadores y referencias de
ocurrencia/documento/página/región. Rutas quedan contenidas en las raíces previstas.
Un Identity desactualizado devuelve 409 con explicación; artefactos ausentes o
rutas no válidas devuelven 404. No ejecuta OpenAI ni Identity Audit, ni modifica
imágenes, manifests o resultados. Es una herramienta manual de lectura.

### Independent Identity Audit 3B.1

Identity Resolver propone una partición; **Identity Audit busca errores concretos
en esa partición y no la modifica**. Consume exclusivamente `identity.json`
persistido, valida su lineage contra CoveredDiscovery actual y reconstruye la
evidencia mediante `build_identity_input()`. No ejecuta fases previas ni el resolver.

```powershell
$env:SNG_IDENTITY_AUDIT_MODEL = "gpt-6.1-sol"
python -m sng_consulter.identity audit "BATCH_ID"
```

Requiere `OPENAI_API_KEY`. Su modelo es independiente de `SNG_IDENTITY_MODEL`.
Usa Responses con salida estructurada estricta, timeout de 180 segundos,
`store=False` y cero retries. Reutiliza los contact sheets en memoria de Identity;
el payload describe grupos actuales y todos sus candidatos, sin paths ni nombres.
El prompt independiente acepta `issues=[]` y exige evidencia concreta para
`MERGE_ERROR`, `SPLIT_ERROR`, `SHOULD_BE_UNRESOLVED` o
`UNRESOLVED_MAY_BE_RESOLVABLE`. No devuelve una partición corregida.

El servicio valida referencias y membership, asigna `identity-audit-0001`, etc.,
y publica `runtime/identity/<batch_id>/identity_audit.json` atómicamente solo tras
éxito completo. Conserva `audited_identity_sha256` de los bytes exactos auditados.
Un fallo conserva un audit previo; Identity y las fases previas permanecen intactos.
Un consumidor posterior deberá comparar ese hash con Identity actual antes de
usar el audit. Límite: 80 candidatos y 120.000 bytes de metadata UTF-8; excederlo
falla sin truncar ni llamar al proveedor. No hay corrección automática, chunking
ni `identity_final.json`. La calidad real del audit requiere revisión manual.

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

Toda subida crea un conjunto persistente y redirige a `/review/batches/<batch_id>`,
con una ocurrencia por archivo, tanto para uno como para varios archivos.
Un archivo único inválido también crea un conjunto con estado `error`; los errores
controlados 400/415 se muestran allí y los errores inesperados se propagan.
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

La preparación de Fase 1 no ejecuta IA, OCR, Discovery, extracción técnica ni interpretación
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

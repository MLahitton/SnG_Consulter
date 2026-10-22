# SnG Consulter

Servicio de preparación documental para Steel & Glass. Fase 1 cerrada;
estado actual: **5C — Deterministic Apply / Validation**.

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
Identity Gate compara ese hash con Identity actual antes de usar el audit.
Límite: 80 candidatos y 120.000 bytes de metadata UTF-8; excederlo
falla sin truncar ni llamar al proveedor. No hay corrección automática, chunking
ni `identity_final.json`. La calidad real del audit requiere revisión manual.

### Identity Gate 3C

Resolver propone partición → Audit busca errores → Gate decide si puede avanzar.

```powershell
python -m sng_consulter.identity gate "BATCH_ID"
```

El Gate es offline y lee únicamente `identity.json` e `identity_audit.json`.
No necesita API key ni ejecuta fases previas. Valida contratos, batch, exclusividad,
IDs únicos, cobertura/contadores internos y referencias de issues a sus grupos.
Exige que el hash auditado corresponda a los bytes exactos de Identity actual;
un audit desactualizado falla explícitamente, sin ejecutarlo automáticamente.

- `issues == []` → `READY_FOR_EXTRACTION`.
- Cualquier issue → `BLOCKED_BY_IDENTITY_ISSUES`, conservando todos los issue IDs
  en orden persistido, sin severidades ni excepciones por tipo.

Unresolved legítimo no bloquea por sí solo. Se publica atómicamente
`runtime/identity/<batch_id>/identity_gate.json`, con SHA-256 de los dos archivos
consumidos, estado, conteo e IDs bloqueantes. Mismos bytes producen el mismo
resultado, sin campos volátiles; los fallos preservan un gate previo.
No inicia Technical Extraction ni corrige identidades. El Gate no reconstruye
imágenes ni verifica de nuevo el artefacto CoveredDiscovery: valida el formato
del hash fuente de Identity; la comprobación contra ese artefacto pertenece al Audit.

## Technical Extraction Foundation — 4A.1 y 4A.2

`technical/` define contratos tipados, evidencia canónica y orquestación con un
`TechnicalExtractor` abstracto y su adapter `OpenAITechnicalExtractor` de 4B.
Solo acepta Gate `READY_FOR_EXTRACTION` vigente.

El builder valida los hashes exactos Gate → Identity/Audit, Audit → Identity e
Identity → CoveredDiscovery actual. Reutiliza evidencia de Identity y páginas
completas/texto nativo de Discovery, sin OCR, crops nuevos ni cambios de fases previas.
Unresolved se preserva con su motivo, sin convertirlo en una Structure.

Structure, Module, GlassPane físico, DecorativeElement y quantity son conceptos
distintos. Vistas y repeticiones no determinan cantidad. Los campos conservan
valor tipado, resolución EXPLICIT/INFERRED/DEFAULTED/UNKNOWN/CONFLICT y referencias
internas de evidencia. Se permite representar quantity DEFAULTED=1 con regla
explícita, pero no se aplica automáticamente. No se seleccionan sistemas comerciales.

El servicio exige una propuesta por Identity, valida evidencia de su propia
Identity, asigna IDs determinísticos y calcula RESOLVED/PARTIAL/AMBIGUOUS y paths
de campos UNKNOWN recursivamente. Publica atómicamente
`runtime/technical/<batch_id>/technical_extraction.json`, preservando salida previa
ante fallo y sin modificar fuentes. Es un resultado interno no verificado;
covered_candidate_id no es el contrato público final de Backend.
Consulta los [contratos y límites de Fase 4](docs/PHASE_4_TECHNICAL_EXTRACTION.md).

4A.2 añade `module_count: ResolvedNonNegativeInt` y `scope: ResolvedTechnicalScope`
a cada estructura. Scope distingue GLASS_RELATED de NOT_GLASS_RELATED; una Identity
resuelta no implica alcance de vidrio. La incertidumbre usa UNKNOWN/CONFLICT,
sin valor UNCERTAIN. Las estructuras fuera de alcance permanecen en el resultado.

Un `module_count` conocido debe coincidir con `len(modules)` y un
`glass_pane_count` conocido con `len(glass_panes)`. UNKNOWN/CONFLICT permiten
listas parciales sin inventar el total. Ambos campos nuevos participan en estado,
missing_fields y validación de evidencia de su propia Identity. No se aplican
defaults automáticos, ni se cuentan vistas o decoraciones como módulos/paños.

### OpenAI Technical Extraction — 4B

Con el venv activo, `OPENAI_API_KEY` configurada y los artefactos previos persistidos:

```powershell
$env:SNG_TECHNICAL_MODEL = "gpt-6.1-sol"
python -m sng_consulter.technical "BATCH_ID"
```

El modelo técnico es independiente de Discovery/Identity/Audit, sin fallback.
Cada Identity resuelta produce una solicitud Responses aislada, con salida
estructurada exactamente `TechnicalStructureProposal`. Se envían metadata de esa
Identity y sus candidatos, crops contextuales etiquetados en memoria, páginas
WEBP completas una vez por documento/occurrence/página y todos sus spans nativos
con bbox visual sin redondeo. No se envían paths ni nombres de archivos/clientes.

El prompt distingue scope, quantity, módulos funcionales, paños físicos y decoración.
Permite normalizar unidades documentales inequívocas m/cm/mm a mm conservando
EXPLICIT y nota; prohíbe estimaciones desde píxeles/proporciones. No aplica defaults:
cualquier DEFAULTED devuelto, incluso anidado, falla explícitamente. NOT_GLASS_RELATED
se conserva; no implica conteos cero ni composición de vidrio inventada.

Presupuesto por Identity: 24 candidatos, 8 páginas y 160.000 bytes UTF-8 de metadata
textual, incluidas etiquetas de imágenes. No se truncan datos ni hay chunking/retries.
`store=False`, timeout 180 segundos y `max_retries=0`. Un fallo en cualquier Identity
impide publicar todo el batch y conserva el resultado previo. El adapter no escribe
artefactos: el servicio existente sigue publicando tras validar todas las propuestas.
El CLI cierra el cliente ante éxito/error y nunca ejecuta fases anteriores.

Extraction no aplica Technical Resolution ni ejecuta Verification o Final Resolution.
El empaquetado es determinístico; la calidad del proveedor requiere prueba manual real.

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


### Evidencia dimensional de Technical Extraction (4B.1)

Además de crops individuales y páginas completas, cada Identity recibe un contexto
PNG por página (unión de candidatos + margen del 6% de página), generado en memoria,
y spans nativos locales por intersección, sin alterar RAW ni perder spans completos.
Unidades explícitas producen EXPLICIT; una convención documental coherente inferida
produce INFERRED con evidencia y nota. Ambigüedad queda UNKNOWN. No estima por píxeles.
Structure, Module y GlassPane requieren evidencia dimensional propia; no se propagan
medidas. `location` es ubicación del elemento dentro del proyecto, no la dirección
del title block. Ver [contrato y límites](docs/PHASE_4_TECHNICAL_EXTRACTION.md).

### Evidencia de schedules asociados (4B.2)

Cada Identity/página recibe además un Associated Annotation Context PNG en memoria:
misma unión de candidatos, márgenes de página del 8% horizontal, 6% superior y 12%
inferior, con spans locales exactos y sin interpretar texto localmente. Conserva toda
la evidencia anterior y una request por Identity. El prompt exige revisar schedules
y verificar su asociación; la presencia de una tabla vecina dentro del crop no basta.
Una especificación explícita puede aplicarse a varios panes solo si el schedule
abarca inequívocamente al conjunto, citando evidencia y explicándolo en nota. No
inventa espesor. Quantity puede venir de una columna asociada; cuerpos de vidrio
no equivalen a módulos. Una columna SISTEMA no implica selección comercial SnG.
Las reglas dimensionales y de location de 4B.1 permanecen sin cambios. Falta validar
manualmente la mejora de schedules; los tests no llaman al proveedor real.

### Atribución de atributos documentales (4B.3)

El prompt exige que cada atributo de schedules/descripciones/captions/notas conserve
su componente propietario. Una dimensión o material de frame, sill, profile u otro
componente no se convierte en especificación/espesor de vidrio por compartir fila.
Primero se establece qué modifica el número; después puede normalizarse su unidad.
La mera existencia de vidrio no es una especificación; ante atribución ambigua usa
UNKNOWN. Tipo y espesor se conservan separados, sin inventar el que falte. Cada
glass_specification EXPLICIT debe explicar su soporte documental y alcance en note;
solo puede propagarse si el texto la aplica inequívocamente a todos los panes.
No cambia contratos, evidencia visual ni reglas dimensionales. Falta comprobar la
atribución con el proveedor real; los tests sintéticos validan prompt y adapter.

### Independent Technical Audit — 4C.1

Technical Extraction propone; Technical Audit critica independientemente. Consume
`runtime/technical/<batch_id>/technical_extraction.json` y reconstruye evidencia
canónica vigente sin ejecutar fases anteriores. Valida contrato, lineage, IDs,
targets, conteos, unresolved, evidencia y completitud antes de auditar.

```powershell
python -m sng_consulter.technical audit "BATCH_ID"
```

Usa `OPENAI_API_KEY`, modelo independiente `SNG_TECHNICAL_AUDIT_MODEL` (default
`gpt-6.1-sol`), una request por estructura y prompt propio. Reutiliza los crops,
contextos, páginas y spans existentes; no persiste imágenes ni respuestas del proveedor.
Reporta problemas concretos mediante paths y candidatos de la Identity; UNKNOWN
puede ser correcto y `issues=[]` es válido. No corrige ni vuelve a extraer.
Publica atómicamente `technical_audit.json`, con SHA-256 de los bytes exactos del
resultado auditado. Un fallo conserva el audit anterior. Límites por estructura:
24 candidatos, 8 páginas, 220.000 bytes de metadata, sin truncar ni retries.
Según la validación manual reportada, 4C.1 fue probado sobre múltiples documentos
reales: audit limpio, omisiones soportadas, propagación excesiva de especificación
de vidrio y respeto de UNKNOWN legítimos. Esa revisión complementa los tests sintéticos.

### Technical Gate — 4C.2

Technical Extraction propone, Technical Audit critica y Technical Gate decide
determinísticamente si los issues pendientes bloquean el avance. Es completamente
offline: no interpreta evidencia, descarta issues, corrige valores ni ejecuta fases.

```powershell
python -m sng_consulter.technical gate "BATCH_ID"
```

Lee los bytes exactos de extracción y audit una sola vez, valida lineage y evidencia
canónica actual, y revalida IDs, orden, referencias, candidatos y paths de cada issue.
Audit limpio produce `READY_FOR_VERIFICATION`; cualquier issue válido produce
`BLOCKED_BY_TECHNICAL_ISSUES`. Ambos son éxitos del comando (exit code 0); artefactos
missing/invalid/stale/inconsistentes fallan con exit code 1.
Publica atómicamente `runtime/technical/<batch_id>/technical_gate.json`, con hashes
SHA-256 exactos de ambos inputs y todos los blocking_issue_ids en orden. Ante fallo
conserva el Gate previo y los inputs. No requiere API key. Se conservan los comandos
de extracción y audit; Gate no ejecuta Technical Resolution ni reconciliación automática.

Según la validación operativa reportada, 4C.2 fue probado sobre múltiples artefactos
reales, tanto READY_FOR_VERIFICATION como BLOCKED_BY_TECHNICAL_ISSUES.

### Technical Resolution & Verification — 5A

La foundation 5A de `technical/resolution/` define contratos y
`build_technical_resolution_input(batch_id, ...)`, sin CLI nuevo, IA, apply ni
publicación de resolución. Consume snapshots exactos de Extraction/Audit/Gate,
revalida lineage/evidencia actual y agrupa solo estructuras con issues. READY da
`resolution_required=false, targets=[]`; BLOCKED da un target por estructura afectada,
con estructura actual, issues, evidencia canónica de su Identity y allowed_change_paths.
Unresolved Identity permanece intacto y nunca se convierte en estructura.

Los permisos derivan solo de los paths del audit: deduplicados, sin descendientes
redundantes y en orden de primera aparición de los roots conservados. La pertenencia
se compara por componentes, sin confundir índices. Las propuestas de 5B usan
TechnicalStructure completa, no JSON patch genérico; esto no autoriza cambios fuera
de los roots. No se comparan ni aplican propuestas en 5A.

5A fue validada operativamente según el reporte recibido: READY produjo cero targets,
BLOCKED solo estructuras afectadas, paths acordes a issues y unresolved preservados.

Actual: 5C Deterministic Apply/Validation, sobre 5A y las propuestas de 5B.
5D Independent Verification y 5E Final Technical Gate siguen futuros.
Ver [decisiones y contratos de Fase 5](docs/PHASE_5_TECHNICAL_RESOLUTION.md).

### Issue-targeted Resolver — 5B (solo propuestas)

```powershell
python -m sng_consulter.technical resolve "BATCH_ID"
```

Primero valida snapshots mediante 5A. READY retorna éxito, cero targets, sin API
key, cliente ni archivo nuevo. BLOCKED usa una request por target con issues y
allowed_change_paths; nunca vuelve a extraer estructuras limpias. Configuración:
OPENAI_API_KEY, SNG_TECHNICAL_RESOLUTION_MODEL (default gpt-6.1-sol), store=False,
timeout 180 y retries 0. Reutiliza crops/contextos/páginas/spans, sin imágenes nuevas.

Publica solo tras éxito completo `runtime/technical/<batch_id>/technical_resolution_proposals.json`,
con tres hashes fuente y propuestas en orden. El modelo devuelve estructura completa
y un outcome por issue: CORRECTED, DISPUTED o UNRESOLVED. Se validan IDs, cobertura
de issues, evidencia, contrato, bookkeeping preservado y ausencia de nuevos DEFAULTED.
Límites: 24 candidatos, 8 páginas, 240.000 bytes de metadata por target, sin truncar.

**Las propuestas NO están aplicadas.** Technical Extraction, Audit y Gate no cambian.
5B fue validada operativamente según el reporte recibido: BLOCKED con correcciones
UNKNOWN → INFERRED y retiro EXPLICIT → UNKNOWN, READY sin targets ni API key, y cambios
observados limitados a allowed_change_paths. El prompt no certifica permisos; 5C lo hace.

### Deterministic Apply / Validation — 5C

```powershell
python -m sng_consulter.technical apply-resolution "BATCH_ID"
```

Comando offline, sin API key ni imports del proveedor. Consume snapshots persistidos
validados por 5A; no ejecuta Extraction, Audit, Gate ni 5B. READY ignora propuestas
históricas y publica el baseline completo con cero issues/cambios. BLOCKED exige
propuestas con hashes y targets exactos y revalida las garantías de 5B.

El diff recorre el contrato en orden; listas primitivas cuentan como un path. Cada
cambio debe pertenecer a un root autorizado y a un issue CORRECTED con cambio real.
DISPUTED/UNRESOLVED no autorizan cambios, salvo solapamiento con CORRECTED. Todos los
IDs y el tamaño/orden de modules, glass_panes y decorative_elements permanecen exactos;
no se aplican cambios de composición. El resultado se construye desde baseline + paths
validados, recalcula missing_fields/resolution_status con las reglas canónicas y pasa
validación técnica completa. No introduce DEFAULTED ni interpreta evidencia nueva.

Publica `runtime/technical/<batch_id>/technical_resolution.json` por staging + replace
atómico. Incluye todas las estructuras en orden, lineage Identity y cuatro hashes
fuente, outcomes con changed_paths y contadores. Los paths se cuentan una vez por
estructura aunque varios issues CORRECTED los compartan. Cualquier fallo conserva
el resultado previo y todos los inputs. No hay locks ni garantías frente a cierre
abrupto; los hashes identifican los snapshots consumidos.

**technical_resolution.json todavía NO es el contrato final de Backend**: faltan 5D y
5E. 5C tiene validación sintética; la prueba operativa sobre propuestas reales queda
pendiente y no se ejecuta automáticamente.

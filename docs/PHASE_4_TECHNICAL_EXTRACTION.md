# Technical Extraction — Foundation 4A, Extractor 4B, Audit/Gate 4C

Esta subfase define contratos internos, evidencia canónica, lineage y orquestación.
4B implementa el adapter OpenAI del Protocol existente, sin cambiar contratos de
4A. 4C.1 agrega auditoría independiente y 4C.2 su Gate determinístico. No hay OCR,
Technical Resolution, Verification, Final Resolution, pricing, selección
de sistemas comerciales ni integración con Backend.

## Distinciones de dominio

Una Structure corresponde al ítem/diseño provisional de una Identity resuelta.
Un Module es un subsistema funcional, un GlassPane es un paño físico de vidrio y
un DecorativeElement describe retículas/divisiones decorativas. No son equivalentes:
un módulo fijo puede contener tres paños; varias celdas decorativas pueden pertenecer
a un solo paño. Rectángulos/líneas no prueban módulos ni paños.

Quantity representa unidades físicas solicitadas. Ni Identity, referencias, vistas,
repeticiones visuales, módulos ni paños determinan quantity por conteo. `system`
queda reservado a sistemas comerciales, que estos contratos no seleccionan.

## Campos resueltos

`ResolvedText`, `ResolvedPositiveInt`, `ResolvedNonNegativeInt`,
`ResolvedPositiveNumber`, `ResolvedModuleFunction` y `ResolvedTechnicalScope` conservan su tipo de valor.
Todos contienen `value` obligatorio (nullable), `resolution`,
`evidence_candidate_ids[]` y `note` nullable. Campos extra están prohibidos;
números deben ser finitos y cumplir signo/tipo, sin coerción de strings o bool.

| Resolution | Value | Evidence | Note |
| --- | --- | --- | --- |
| EXPLICIT | No null | Al menos un ID | Opcional |
| INFERRED | No null | Al menos un ID | Inferencia explicada |
| DEFAULTED | No null | Vacía | Regla aplicada |
| UNKNOWN | Null | Vacía | Opcional |
| CONFLICT | Null | Al menos un ID | Conflicto explicado |

No se permiten IDs de evidencia duplicados ni confidence numérico. Omitir `value`
no equivale a declarar UNKNOWN. Se puede representar quantity DEFAULTED=1 con
nota de regla y sin evidencia, pero 4A.1 nunca aplica ese default automáticamente.

## Propuestas y resultados

- `TechnicalDimensions`: `width_mm`, `height_mm` como ResolvedPositiveNumber.
  Milímetros son la unidad canónica; no hay parser ni conversor local de unidades.
  4B instruye al modelo para normalizar unidades documentales explícitas con nota.
- `ModuleFunction`: FIXED, SLIDING, PROJECTING, HINGED, FOLDING, PIVOT, LOUVER, OTHER.
  Función desconocida usa value null y resolution UNKNOWN, no enum UNKNOWN.
- `GlassPaneProposal`: `position_hint`, `dimensions`, `glass_specification`.
- `DecorativeElementProposal`: `kind`, `description`, `position_hint`, todos ResolvedText.
- `ModuleProposal`: `position_hint`, `dimensions`, `function`, `opening_direction`
  (ResolvedText), `glass_pane_count` (ResolvedNonNegativeInt), `glass_panes[]`,
  `decorative_elements[]`.
- `TechnicalStructureProposal`: `identity_id`, `scope` (ResolvedTechnicalScope), `reference`, `name`, `category`,
  `location` (ResolvedText), `quantity` (ResolvedPositiveInt), `dimensions`,
  `module_count` (ResolvedNonNegativeInt), `modules[]`, `notes[]`.
- `TechnicalExtractionProposal`: `structures[]`.

Propuestas no asignan IDs técnicos, estados ni missing_fields. Las listas no se
usan para inferir conteos automáticamente. Cuando `module_count.value` no es null
(EXPLICIT/INFERRED/DEFAULTED), debe coincidir exactamente con `len(modules)`.
Análogamente, `glass_pane_count.value` conocido debe coincidir con `len(glass_panes)`.
UNKNOWN/CONFLICT conservan value null y permiten listas vacías o parcialmente
conocidas sin exigir igualdad. Estas reglas se aplican también a los modelos
persistibles mediante los mismos validadores. No se completan listas ni se
corrigen conteos silenciosamente.

`module_count` representa módulos funcionales, separado de quantity, vistas,
candidatos, paños y celdas decorativas. No se aplica default automático.

`TechnicalScope` contiene solo GLASS_RELATED y NOT_GLASS_RELATED.
**Identity resolution does not imply glass scope.** Una Identity puede estar
bien resuelta y representar un elemento ajeno al alcance de vidrio. Scope se
resuelve con las mismas reglas de valor/evidencia/nota: UNKNOWN y CONFLICT usan
null, nunca un enum UNCERTAIN. DEFAULTED es representable mediante el contrato
genérico, sin aplicarse automáticamente. NOT_GLASS_RELATED no elimina ni filtra
la estructura y no tiene excepciones de cálculo de completitud. La futura Final
Resolution decidirá qué resultados llegan al contrato público.

Los modelos persistibles conservan esos datos y añaden:

- `TechnicalStructure`: `technical_structure_id`, `resolution_status`, `missing_fields`.
- `Module`: `module_id`.
- `GlassPane`: `glass_pane_id`.
- `DecorativeElement`: `decorative_element_id`.

## Input canónico y lineage

`build_technical_extraction_input(batch_id, identity_root, discovery_root,
batch_root, prepared_root)` exige Identity, Audit y Gate existentes. Lee bytes
exactos, valida contratos y batch, requiere Gate READY y cero issues coherentes.
Comprueba Gate → SHA-256 de Identity actual, Gate → SHA-256 de Audit actual y
Audit → SHA-256 de Identity actual. Ningún hash se deriva de reserializar modelos.

Reconstruye con `build_identity_input()` y exige Identity → CoveredDiscovery actual,
además de partición exclusiva y completa. Reutiliza `build_discovery_input()` para
páginas/texto: spans nativos deduplicados, con bbox en píxeles visuales mediante
la conversión existente. No reinterpreta texto ni altera RAW. Storage valida las
rutas, documentos, occurrences y páginas. Un lineage desactualizado falla, sin
ejecutar fases anteriores ni regenerar el Gate.

`TechnicalExtractionInput` contiene `batch_id`, `source_identity_sha256`,
`source_identity_gate_sha256`, `identities[]`, `unresolved_groups[]`.

Cada `TechnicalIdentityInput` contiene `identity_id`, `reference_hint`,
`description`, `candidate_ids[]`, `candidates[]`, `pages[]`. Candidatos reutilizan
IdentityCandidateInput: origen, ID fuente, referencia, descripción, relevancia,
documento, occurrence, página, NormalizedRegion, dimensiones y path del render.
`TechnicalPageEvidence` reutiliza PageDiscoveryInput: documento, occurrence,
página, width_px, height_px, image_path, native_text_spans. Cada página relevante
aparece una sola vez por Identity, identificada por documento/occurrence/página.

La evidencia permite usar localización/crop y full-page/texto en 4B. El builder no
genera imágenes ni requests. Unresolved conserva ID, miembros y motivo,
pero no crea targets ni estructuras. Su existencia no bloquea un Gate READY.

## Servicio y persistencia

`TechnicalExtractor.extract(TechnicalExtractionInput) -> TechnicalExtractionProposal`
es el Protocol implementado en 4B por OpenAITechnicalExtractor. El servicio pasa copia profunda al extractor
y exige exactamente una propuesta por Identity resuelta, sin omisiones, duplicados
ni targets desconocidos. Cada ID de evidencia, incluso anidado, debe pertenecer
a esa Identity. covered_candidate_id es evidencia interna, no contrato público final
de Backend; una futura resolución final podrá transformarlo.

`TechnicalExtractionService.extract()` emite structures en orden de Identity;
modules, paños y decoraciones siguen el orden propuesto. Asigna secuencias globales
independientes dentro del resultado: `technical-structure-0001`, `module-0001`,
`glass-pane-0001`, `decorative-0001`. No hay timestamps ni UUIDs en el artefacto.

El servicio recorre recursivamente los campos resueltos, incluidas dimensiones,
módulos, paños y decoraciones:

- Cualquier CONFLICT → AMBIGUOUS.
- Sin CONFLICT, con algún UNKNOWN → PARTIAL.
- Sin ambos → RESOLVED. DEFAULTED no se trata como UNKNOWN.

`missing_fields` contiene solo paths de UNKNOWN, en orden estable de contrato/listas,
por ejemplo `modules[0].glass_panes[1].dimensions.height_mm`. No incluye CONFLICT
ni inventa required fields adicionales.

Scope y module_count participan en ese mismo recorrido recursivo: CONFLICT implica
AMBIGUOUS; UNKNOWN implica PARTIAL en ausencia de conflictos y añade `scope` o
`module_count` a missing_fields. Su evidencia solo puede citar candidatos de la
Identity correspondiente. No existe otro mecanismo de evidencia.

`TechnicalExtractionResult`: batch, hashes de Identity/Gate, `structures[]`,
`unresolved_groups[]`, `resolved_identity_count` (targets técnicos, no unidades
físicas ni número de structures con status RESOLVED), `unresolved_group_count`.
No afirma que el resultado esté verificado ni resuelto finalmente.

Publica `runtime/technical/<batch_id>/technical_extraction.json` mediante staging
y reemplazo atómico solo tras éxito completo. Ante excepción limpia staging y
conserva resultado previo. No modifica artefactos de fases anteriores.

## Compatibilidad

Scope y module_count son campos obligatorios, sin defaults. Propuestas o resultados
técnicos anteriores que no los incluyan son incompatibles con el contrato actual;
no hay migración ni inferencia silenciosa. TechnicalExtractionInput, lineage e
Identity/Discovery permanecen sin cambios.

## OpenAI Multimodal Technical Extractor — 4B

`OpenAITechnicalExtractor.extract(TechnicalExtractionInput)` procesa identities en
orden y realiza una sola solicitud Responses por Identity. Cada salida usa
exactamente TechnicalStructureProposal, no un modelo paralelo, y debe devolver el
identity_id esperado. Agrega las propuestas en TechnicalExtractionProposal.
No asigna IDs técnicos ni calcula status/missing_fields; eso sigue en el servicio.

Config: `OPENAI_API_KEY` obligatorio, `SNG_TECHNICAL_MODEL` independiente, default
`gpt-6.1-sol`, sin fallback. SDK oficial `responses.parse(text_format=...)`,
`store=False`, timeout 180 segundos y cero retries automáticos. Se usa el patrón
de [salida estructurada de OpenAI Docs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses).

### Request por Identity

JSON compacto y sin truncamiento:

- `identity`: identity_id, reference_hint, description, candidate_ids.
- `candidates`: campos canónicos de IdentityCandidateInput excepto image_path;
  incluye origen, ID fuente, referencia, descripción, relevancia, documento,
  occurrence, página, región y dimensiones del render.

Después de los crops individuales, por cada página se envían metadata de contexto
(documento, occurrence, página, render_size, context_bbox y local_native_spans),
PNG contextual, metadata annotation_context con sus spans y PNG de anotaciones,
WEBP completo y metadata full_native_spans. Los spans usan
`[text,[x0,y0,x1,y1]]`, sin redondear bbox ni perder spans de la vista canónica.

No se envían batch/hash, otras identities, unresolved, filenames, original_name,
paths locales ni metadata del proveedor. El texto documental sigue siendo evidencia
no confiable; no se depura ni convierte en instrucciones.

`visuals.py` reutiliza candidate_crop() de Identity (margen contextual canónico 5%).
Cada crop conserva píxeles/aspect ratio, sin resize, dentro de un PNG con franja
blanca superior de 32 px para el ID. No se dibuja encima de la evidencia. Cada
imagen va precedida de metadata con kind, documento/occurrence/página y, para
crops, covered_candidate_id. Los crops se envían en orden de candidatos.
Los dos contextos agregados preceden a los bytes WEBP exactos de cada página relevante, una vez por
documento/occurrence/página, con `detail=high` en todas las imágenes. Todo se
genera/codifica en memoria, sin archivos intermedios.

### Prompt y semántica

El prompt en `technical/openai_extractor.py` pide una estructura para la Identity
actual usando conjuntamente crops, full-page y spans. Mantiene los invariantes:

- Scope no se deriva de haber resuelto Identity; admite NOT_GLASS_RELATED y lo
  conserva. Si no hay composición de vidrio soportada, usa conteos UNKNOWN y
  listas vacías, sin convertir fuera de alcance en cantidad/conteo cero.
- EXPLICIT requiere evidencia documental inequívoca. INFERRED exige evidencia y
  nota; UNKNOWN usa null/sin evidencia; CONFLICT conserva null y explica evidencia
  incompatible. No se elige arbitrariamente ni se aplica default.
- Una unidad inequívoca m/cm/mm puede normalizarse a mm como EXPLICIT con nota,
  p.ej. 3.00 m → 3000 mm. Es una instrucción al proveedor, no un parser/conversor
  local ni una verificación de la aritmética del modelo. No estima escala,
  píxeles→mm ni proporciones y no propaga dimensiones entre Structure/Module/Pane.
- Quantity no se obtiene contando vistas/crops/páginas/módulos/paños. INFERRED
  requiere instancias físicas inequívocas, excluyendo vistas alternativas y
  repeticiones. Ante duda usa UNKNOWN; no default quantity=1.
- Módulos funcionales, paños físicos y celdas decorativas permanecen separados.
  Conteos conocidos deben corresponder a listas completas; detalles pueden ser
  UNKNOWN. Conteos inciertos permiten elementos parciales soportados.
- reference_hint no se transforma automáticamente en EXPLICIT. Función y apertura
  requieren evidencia; especificaciones faltantes no se completan. Notes no sirve
  para esconder campos técnicos ni eludir evidencia.

El adapter recorre todos los ResolvedField anidados y rechaza DEFAULTED con
UnexpectedTechnicalDefaultError, sin reemplazarlo por UNKNOWN. El contrato genérico
lo conserva para futuras reglas determinísticas. El servicio comprueba que la
evidencia pertenezca a la Identity, incluidos scope, conteos y campos anidados.

### Presupuesto, errores y CLI

Por Identity: máximo 24 candidatos, 8 páginas y 160.000 bytes de texto metadata
UTF-8 (JSON principal, metadata de imágenes y spans locales/completos). Prompt estático e
imágenes base64 no forman parte de ese límite textual. TechnicalContextBudgetExceeded
falla antes de llamar al proveedor para esa Identity; nunca trunca ni divide.
Una Identity previa puede haber completado su solicitud, pero no se publica nada
si falla cualquier Identity posterior. No hay retries ni fallback.

Errores separados: TechnicalConfigurationError, TechnicalAPIError,
InvalidTechnicalResponseError, TechnicalContextBudgetExceeded y
UnexpectedTechnicalDefaultError. Refusal, incompleta, malformed, schema/conteos
inválidos o identity_id distinto fallan explícitamente. Errores públicos no incluyen
claves, cuerpos completos ni base64; no hay reparaciones o respuesta vacía de fallback.

```powershell
.\.venv\Scripts\Activate.ps1
$env:SNG_TECHNICAL_MODEL = "gpt-6.1-sol"
python -m sng_consulter.technical "BATCH_ID"
```

Configurar antes OPENAI_API_KEY por el mecanismo local habitual. El comando llama
al builder, instancia el adapter, ejecuta el servicio y cierra el cliente en éxito
o error. No ejecuta Preparation, Discovery, Coverage, Identity, Audit ni Gate.
El servicio sigue siendo el único publicador de technical_extraction.json tras
validar todas las identities. No se persisten prompts, requests, responses, crops,
contact sheets, base64 ni metadata del proveedor. Se preservan artefactos anteriores.

## Pendiente de validación real del hardening de schedules

Evaluar legibilidad de crops/full-page, asociaciones de cotas, unidad normalizada,
quantity, composición, scope, calidad de evidencia, costos y latencia con documentos
reales. Los tests simulan exclusivamente HTTP, no demuestran precisión semántica.
Las consistencias de conteos descritas son validaciones de contrato, no Verification.
No hay verificación técnica adicional, correcciones automáticas,
selección comercial ni contrato público Backend. Persistencia sin locks ni
recuperación automática ante cierre abrupto; los hashes identifican snapshots
consumidos y futuros consumidores deberán comprobarlos.
No se introducen chunking, retries adaptativos o verificación global.


## 4B.1 — Evidencia dimensional

Por Identity/página se agrega un PNG contextual en memoria: unión de las regiones
convertidas con `normalized_region_to_render_bbox()`, expandida por 6% del ancho y
alto totales a cada lado, floor/ceil y clipping a página. Solo participan candidatos
de esa Identity y del mismo documento/occurrence/página. No cambia `candidate_crop()`.
Los `local_native_spans` se seleccionan por intersección de área positiva con ese
contexto; conservan texto exacto y bbox visual sin redondeo ni interpretación numérica.
No se agrega un clasificador de spans numéricos.

Orden: instrucciones, Identity/candidates, crops individuales; luego por página
metadata/spans locales, contexto PNG high, annotation_context/PNG high de 4B.2,
página WEBP original high y spans completos.
No se envían paths ni se guardan crops. Se mantienen presupuestos de 24 candidatos,
8 páginas y 160.000 bytes textuales, sin truncar evidencia; el contexto agrega una
imagen por página y puede aumentar el costo de entrada visual.

Unidades explícitas se normalizan a mm como EXPLICIT con nota. Una convención
implícita puede inferirse de múltiples cotas coherentes y su contexto arquitectónico:
el campo dimensional completo queda INFERRED con evidencia y nota de inferencia y
normalización. Un número aislado no establece metros; ambigüedad real queda UNKNOWN.
La convención se aplica consistentemente dentro del mismo contexto, no entre detalles
sin relación. Nunca se estima por píxeles, proporciones, escala gráfica ni tamaño de página.

Structure prioriza cotas totales asociadas y vistas coherentes; Module necesita cotas
individuales, sin copiar/dividir totales. GlassPane necesita tamaño físico del vidrio
terminado: vano/frame/módulo no lo demuestra. Cada eje es independiente. Un diámetro
explícito puede representar ambos ejes con nota. Contexto vecino no autoriza tomar sus cotas.
`location` es ubicación del elemento dentro del proyecto, no ciudad, dirección, lote
ni ubicación general del title block; si falta ubicación del elemento, UNKNOWN.

La validación SDK simulada comprueba contratos y evidencia enviada, no la capacidad
del modelo de asociar cotas o inferir unidades. La prueba manual posterior a 4B.1
reportada por el usuario validó satisfactoriamente dimensiones. Falta validar la
mejora de schedules de 4B.2 y evaluar legibilidad, precisión, costo y latencia. No se
introducen Technical Audit, nuevas llamadas, retries ni conversión dimensional local.

## 4B.2 — Associated Schedule Evidence Hardening

`visuals.py` calcula una sola unión de bbox mediante el helper canónico por
Identity/documento/occurrence/página. De esa unión salen ambos contextos: el de
4B.1 conserva 6% por lado; Associated Annotation Context usa 8% del ancho de página
a izquierda/derecha, 6% de la altura arriba y 12% abajo, para incluir captions y
tablas cercanas frecuentemente situadas debajo. Ambos usan floor/ceil y clipping;
no se buscan referencias, palabras ni posiciones de documentos conocidos.

Cada nuevo PNG se genera en memoria, sin resize ni persistencia. Su metadata incluye
`annotation_context: {context_bbox, local_native_spans}`. Los spans intersectan el
crop con área positiva y conservan texto/bbox visual exactos: sin OCR, regex semántica
ni interpretación numérica. Se mantienen crops individuales, Identity Page Context,
full WEBP y spans completos, todos los contextos/imágenes con `detail=high`.

Antes de devolver la propuesta, el prompt exige inspeccionar schedules/tablas/captions
asociados para referencia, quantity, descripción de operación, cuerpos móviles/fijos,
especificación de vidrio/material y nombre/categoría explícitos. El crop puede incluir
vecinos: exige relación suficiente mediante proximidad, referencia coincidente,
layout, caption, agrupación de vistas o asociación visual; aparecer en el crop no basta.
Ilegibilidad o asociación ambigua permiten UNKNOWN, sin completar por conocimiento externo.

Un schedule legible e inequívocamente asociado es evidencia EXPLICIT. Una especificación
aplicable a toda la estructura/todos sus cuerpos de vidrio puede repetirse en los
GlassPane correspondientes como EXPLICIT, con evidencia de esa Identity y nota que
explique la aplicación al conjunto. No se propaga si distingue panes/cuerpos; specs
distintas se representan por pane, y conflictos no resueltos como CONFLICT. No se
inventa espesor, color ni tratamiento ausente.

Se revisa la columna de cantidad antes de UNKNOWN, manteniendo las reglas existentes.
SISTEMA o equivalente es descripción documental, sin selección de sistemas comerciales:
solo aporta campos soportados. La geometría conserva su función para composición;
cuerpos de vidrio no equivalen a módulos, ni el texto sustituye conceptos geométricos
distintos. Contratos, dimensiones, location, scope y rechazo de DEFAULTED no cambian.

Una request por Identity, mismos modelos/configuración y presupuestos: 24 candidatos,
8 páginas, 160.000 bytes de metadata incluyendo annotation_context/spans. Ahora son
hasta 24 crops individuales + 24 imágenes de página/contextos (48 en total) por Identity.
La imagen extra aumenta costo visual y su metadata cuenta dentro del límite textual;
no se trunca, no hay segunda llamada, chunking ni retries. La suite prueba los límites,
empaquetado y respuestas HTTP simuladas, no la interpretación real de schedules.

## 4B.3 — Schedule Attribute Attribution Hardening

Se agregan instrucciones al prompt existente, sin cambiar contratos, estrategia
visual, imágenes, budgets, requests ni validación/postprocesamiento del adapter.
Cada atributo de schedule, descripción, caption o nota debe vincularse al componente
descrito: vidrio, frame/profile, sill, threshold, hardware, wall, seal/gasket,
decoración u otro. Compartir frase/fila no basta para transferir sus propiedades.

Antes de usar cifra/unidad debe determinarse qué modifica; solo entonces es válida
su normalización. Dimensiones/especificaciones de otros componentes no pueden
convertirse en espesor/material de vidrio. Las reglas dimensionales 4B.1 se conservan.
glass_specification requiere evidencia que describa inequívocamente al vidrio;
existencia de glazing no es una especificación. Tipo y espesor son distintos: un
tipo documental no autoriza inventar espesor, ni tamaño/operación/conteo/práctica
constructiva lo establecen. Ante atribución ambigua o plausible propietario distinto,
el prompt pide UNKNOWN.

La revisión interna pide comprobar la declaración exacta que soporta cada
glass_specification no nula, su asociación al vidrio y posibles propietarios
alternativos. No agrega campos de checklist al contrato. Toda specification EXPLICIT
debe incluir note con el soporte documental real y su alcance, sin atribuir al
documento frases ausentes. Se conserva propagación de 4B.2 únicamente si la declaración
de vidrio aplica inequívocamente al conjunto; nunca se propagan atributos de frame/sill.

Tests HTTP simulados cubren tipo sin espesor, espesor explícito, propiedades de otros
componentes, coexistencia en una fila, múltiples panes y atribución ambigua. Comprueban
prompt, evidencia enviada, conservación de respuestas y ausencia de escrituras;
no demuestran razonamiento del modelo. Sigue pendiente repetir validación manual
real/benchmark para evaluar atribución y regresión dimensional. No se ha ejecutado
benchmark ni llamadas reales a OpenAI en esta subfase.

## 4C.1 — Independent Technical Audit

Technical Extraction proposes. Technical Audit independently challenges. El auditor
trata la estructura como hipótesis: solo reporta errores/omisiones significativos
demostrables, no preferencias de redacción ni interpretaciones igualmente válidas.
UNKNOWN no es un error por sí mismo; no exige cortes de vidrio calculables ni
datos ausentes. Una salida `issues=[]` es válida y esperada. No corrige valores,
repite extracción ni aplica defaults. El Gate separado se describe en 4C.2.

### Contratos

- `TechnicalAuditIssueProposal`: issue_type, field_paths (al menos uno),
  candidate_ids (sin duplicados), reason (no vacío/blanco).
- `TechnicalStructureAuditProposal`: identity_id, issues.
- `TechnicalAuditIssue`: todos los campos de IssueProposal más issue_id,
  technical_structure_id e identity_id.
- `TechnicalAuditResult`: batch_id, audited_technical_extraction_sha256,
  structures_audited, issue_count, issues.

Tipos exactos: UNSUPPORTED_VALUE, MISSING_SUPPORTED_VALUE, RESOLUTION_ERROR,
SCOPE_ERROR, QUANTITY_ERROR, DIMENSION_ERROR, MODULE_COMPOSITION_ERROR,
MODULE_FUNCTION_ERROR, PANE_COMPOSITION_ERROR, GLASS_SPECIFICATION_ERROR,
ATTRIBUTE_OWNERSHIP_ERROR. Se pide el más específico: omisión explícita de vidrio
usa GLASS_SPECIFICATION_ERROR; propiedad de sill atribuida al vidrio usa
ATTRIBUTE_OWNERSHIP_ERROR, no el genérico. No hay corrected_value, confidence
ni estructura de reemplazo. IDs `technical-audit-0001`, etc., en orden de estructura
y después orden de issues del auditor.

### Validación y lineage

El servicio lee una sola vez los bytes exactos de technical_extraction.json,
valida TechnicalExtractionResult y calcula SHA-256 sin reserializar. Reconstruye
`build_technical_extraction_input(batch_id)`: comprueba de nuevo Identity/Audit/Gate,
CoveredDiscovery y evidencia preparada, sin ejecutar servicios de esas fases.
Exige batch y hashes de Identity/Gate actuales, targets exactos en orden, counters
y unresolved preservados. `technical/validation.py` comprueba IDs únicos globales
por clase, pertenencia de evidencia, missing_fields y resolution_status conforme a
UNKNOWN/CONFLICT. El contrato existente valida conteos conocidos de módulos/panes.
No cambia contratos ni servicio de extracción.

Cada field_path se recorre por campos BaseModel e índices de listas existentes;
se rechazan campos/índices inexistentes, paths vacíos y sintaxis insegura, sin eval.
Una colección como `modules` o `modules[0].glass_panes` también es un target válido.
Issues solo pueden citar candidatos de su Identity. Como los errores contractuales
determinísticos se rechazan antes, los issues restantes requieren evidencia
documental (candidate_ids no vacío); no se aceptan afirmaciones contractuales sin
soporte del modelo sobre una entrada ya validada.

### Solicitudes y configuración

Una solicitud Responses estricta por Structure/Identity, en orden; ninguna request
reejecuta extracción. El prompt `TECHNICAL_AUDIT_INSTRUCTIONS` es independiente del
extractor y examina scope, quantity, dimensiones, composición, funciones, vidrio,
propiedad de atributos y resolución, sin usar ground truth/identidad de fixtures.
JSON inicial: current_structure completa (incluye notes/resolutions/evidencia),
identity y candidates sin image_path. Después reutiliza `build_technical_images()`:
candidate crops, Identity Page Context, Associated Annotation Context, full page,
full native spans, con metadata/spans locales y detail=high. No envía paths,
filenames, claves, respuestas previas del proveedor ni prompt del extractor.

OPENAI_API_KEY obligatorio; SNG_TECHNICAL_AUDIT_MODEL independiente, default
gpt-6.1-sol. store=False, timeout=180, max_retries=0, sin fallback/checkpoints.
Presupuesto por estructura: 24 candidatos, 8 páginas, 220.000 bytes de metadata
UTF-8 incluidos estructura, headers y spans. Imágenes base64 y prompt estático no
cuentan en ese límite textual. Exceso falla antes de API, sin truncar ni chunking.

Errores: TechnicalAuditConfigurationError, TechnicalAuditAPIError,
InvalidTechnicalAuditResponseError, TechnicalAuditContextBudgetExceeded,
InvalidTechnicalAuditInputError. Refusal/incomplete/malformed y errores API no se
convierten en audit limpio. Mensajes API no incluyen claves, base64 ni cuerpos.

### Persistencia y ejecución

Solo tras éxito completo se publica `runtime/technical/<batch_id>/technical_audit.json`
mediante staging y replace. Excepciones limpian staging y conservan audit anterior;
technical_extraction.json y artefactos/evidencia previos permanecen intactos.

```powershell
python -m sng_consulter.technical audit "BATCH_ID"
```

El CLI conserva `python -m sng_consulter.technical "BATCH_ID"` para extracción.
Audit no instancia el extractor ni ejecuta fases anteriores, y cierra el cliente
en éxito/error. Solo persiste el contrato de audit; no guarda imágenes, prompts
ni respuestas completas del proveedor.

Tests sintéticos/SDK HTTP simulado comprueban validación, snapshot exacto, errores,
evidencia, budget, CLI y atomicidad; no certifican por sí solos precisión semántica.
La validación manual de 4C.1 reportada por el usuario cubrió múltiples documentos
reales: audit limpio, omisiones soportadas, propagación excesiva de glass specification
y respeto de UNKNOWN legítimos. No se usan nombres ni resultados de esos casos como
reglas productivas. Costos, latencia y regresión posterior requieren seguimiento.
No hay locks,
checkpoints o recuperación automática ante cierre abrupto; el hash identifica el
snapshot auditado y no congela los artefactos durante solicitudes concurrentes.

## 4C.2 — Technical Gate

Gate offline y determinístico: Technical Extraction proposes, Technical Audit
challenges, Technical Gate decides whether unresolved audit issues block progression.
No interpreta documentos, valora severidad, descarta issues, corrige ni reejecuta
ninguna fase. No utiliza OpenAI. Technical Resolution y reconciliación no están
implementadas; READY no ejecuta Verification ni afirma que esa fase exista.

`TechnicalGateStatus` tiene exactamente READY_FOR_VERIFICATION y
BLOCKED_BY_TECHNICAL_ISSUES. `TechnicalGateResult` contiene batch_id,
source_technical_extraction_sha256, source_technical_audit_sha256, status,
issue_count y blocking_issue_ids; sin confidence ni reason duplicada.

El servicio lee una única vez los bytes exactos de technical_extraction.json y
technical_audit.json bajo runtime/technical/<batch_id>, calcula ambos SHA-256 y
valida sus contratos, sin hashear reserializaciones. Reconstruye únicamente
build_technical_extraction_input y reutiliza validate_technical_result: lineage
Identity/Gate/CoveredDiscovery/evidencia preparada vigente, targets, orden, IDs,
conteos, unresolved, evidencia y completitud se comprueban sin ejecutar fases.

Audit debe pertenecer al batch, auditar el hash exacto actual de extracción y
declarar structures_audited/issue_count correctos. Cada issue se revalida:

- issue_id sigue exactamente technical-audit-0001, technical-audit-0002, etc.;
- Identity y estructura existen y corresponden entre sí;
- candidate_ids no vacío, sin duplicados y exclusivo de esa Identity;
- field_paths no vacío y cada campo/índice existe según resolve_field_path;
- reason no vacío/blanco y issue_type pertenece al enum actual;
- el orden no retrocede a una estructura anterior; varios issues de una estructura
  son válidos, conservando su orden persistido.

Tras validar todo, cero issues da READY_FOR_VERIFICATION, contador cero y lista
vacía. Uno o más da BLOCKED_BY_TECHNICAL_ISSUES y todos sus IDs en orden. No hay
excepciones por tipo ni severidad. BLOCKED es un resultado exitoso, no un error de
ejecución. Missing/invalid/stale/inconsistente/tampered produce
InvalidTechnicalGateInputError; nunca se convierte en READY o BLOCKED.

Publica technical_gate.json mediante staging + replace atómico, solo tras éxito
completo. Un fallo limpia staging, conserva Gate anterior y no modifica extracción,
audit ni upstream. No hay locks ni congelación concurrente de inputs; los hashes
identifican los snapshots leídos, y un cierre abrupto puede dejar staging.

```powershell
python -m sng_consulter.technical gate "BATCH_ID"
```

CLI: READY y BLOCKED retornan 0; input inválido o fallo de publicación muestra error
limpio y retorna 1. No requiere API key ni instancia extractor/auditor. Se conservan
los comandos existentes de extracción y audit. Tests sintéticos verifican ambos
estados, hashes exactos, alteraciones, referencias/orden, atomicidad e independencia
offline del CLI. Esta implementación no ejecuta llamadas reales ni benchmark;
queda pendiente su validación operativa con artefactos reales.

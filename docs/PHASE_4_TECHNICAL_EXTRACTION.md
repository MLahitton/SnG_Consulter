# Technical Extraction Foundation — 4A.1 y Contract hardening — 4A.2

Esta subfase define contratos internos, evidencia canónica, lineage y orquestación.
No hay extractor real, OpenAI, prompts, OCR, Verification, Final Resolution, pricing,
selección de sistemas comerciales ni integración con Backend. No hay CLI de extracción.

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
  Milímetros son la unidad canónica; todavía no hay parser ni conversiones.
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

La evidencia permite usar localización/crop y full-page/texto en 4B, sin crear
ahora crops, thumbnails ni requests. Unresolved conserva ID, miembros y motivo,
pero no crea targets ni estructuras. Su existencia no bloquea un Gate READY.

## Servicio y persistencia

`TechnicalExtractor.extract(TechnicalExtractionInput) -> TechnicalExtractionProposal`
es un Protocol sin implementación real. El servicio pasa copia profunda al extractor
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

## Compatibilidad y pendiente para 4B

Scope y module_count son campos obligatorios, sin defaults. Propuestas o resultados
técnicos anteriores que no los incluyan son incompatibles con el contrato actual;
no hay migración ni inferencia silenciosa. TechnicalExtractionInput, lineage e
Identity/Discovery permanecen sin cambios.

Implementar el extractor multimodal y definir cómo combinar contexto full-page,
crops y texto para proponer campos soportados por evidencia. Todavía no se
interpreta ningún documento real ni se aplica quantity=1 automáticamente.
Las consistencias de conteos descritas son validaciones de contrato, no Verification.
No hay verificación técnica adicional, correcciones automáticas,
selección comercial ni contrato público Backend. Persistencia sin locks ni
recuperación automática ante cierre abrupto; los hashes identifican snapshots
consumidos y futuros consumidores deberán comprobarlos.
En 4B también deberá prohibirse que el proveedor emita DEFAULTED; ese control no
se adelanta aquí. No se ha demostrado otra carencia contractual bloqueante para
implementar el adapter, sin sustituir la futura validación con evidencia real.

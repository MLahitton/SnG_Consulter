# Fase 5 — Technical Resolution & Verification

## Estado y límites

5C — Deterministic Apply / Validation está implementada y validada operativamente,
sobre 5A cerrada y 5B proposal-only. Fases 1–4 cerradas. No se cambia su semántica ni
se reejecutan servicios anteriores. 5C no importa proveedores, no usa API ni interpreta
evidencia nueva. Publica un snapshot aplicado, sin modificar los artefactos de origen.
La subfase actual es [5D Independent Verification](PHASE_5_TECHNICAL_VERIFICATION.md).
Final Gate 5E todavía no existe.

| Subfase | Estado |
| --- | --- |
| 5A Resolution Foundation | Implementada |
| 5B Issue-targeted Resolver | Implementada, solo propuestas |
| 5C Deterministic Apply/Validation | Implementada y validada operativamente |
| 5D Independent Verification | Implementada, validación sintética |
| 5E Final Technical Gate | Futura; no implementada |

## Builder y snapshots

`build_technical_resolution_input(batch_id, technical_root, identity_root,
discovery_root, batch_root, prepared_root)` usa los mismos roots por defecto que
las fases anteriores, bajo runtime/. Lee una sola vez los bytes de:

- runtime/technical/<batch_id>/technical_extraction.json
- runtime/technical/<batch_id>/technical_audit.json
- runtime/technical/<batch_id>/technical_gate.json

Calcula SHA-256 exactos, sin reserializar. Valida sus contratos y reconstruye
build_technical_extraction_input para comprobar Identity, Identity Audit/Gate,
CoveredDiscovery y Preparation actuales, sin ejecutar sus servicios. Reutiliza
validate_technical_result para extracción y validate_technical_audit para lineage,
contadores y referencias del audit. Esa última función conserva las comprobaciones
extraídas del Gate: secuencia de IDs, targets reales, candidatos exclusivos no
vacíos, paths existentes, reason/tipo válidos y orden de estructuras sin retrocesos.

Exige batch y hashes exactos del Gate contra Extraction/Audit, el mismo issue_count
y blocking_issue_ids en el mismo orden, además de status compatible con la presencia
de issues. READY solo es válido si lo declara un Gate íntegro sin issues. Missing,
invalid, stale, tampered o inconsistencia falla con InvalidTechnicalResolutionInputError,
nunca se oculta como ausencia de trabajo.

Whitespace en Gate cambia su hash fuente y puede seguir siendo válido; whitespace
en Audit invalida el Gate; whitespace en Extraction invalida Audit y Gate hasta que
se regeneren explícitamente por sus comandos existentes. El builder no los regenera.

## Contratos del input

`TechnicalResolutionIssue`: issue_id, issue_type, field_paths, candidate_ids, reason.
Conserva los issues del audit; no añade corrected_value ni confidence.

`TechnicalResolutionTarget`: identity_id, technical_structure_id, current_structure
(TechnicalStructure), issues, allowed_change_paths e identity (TechnicalIdentityInput).
La evidencia es la misma adquisición canónica de 4A; no se genera otra representación
visual. Los candidatos/paths pertenecen a la Identity/estructura del target.

`TechnicalResolutionInput`: batch_id, source_technical_extraction_sha256,
source_technical_audit_sha256, source_technical_gate_sha256, resolution_required,
targets, resolved_identity_count y unresolved_groups. Los modelos prohíben extras.

READY produce resolution_required=false y targets=[] con hashes, contador total
de identities resueltas y unresolved_groups preservados. BLOCKED produce true y
solo un target por estructura con issues, en orden de Extraction. Cada target
conserva el orden de sus issues del Audit. Estructuras limpias quedan fuera del
trabajo de Resolution; varios issues de una estructura no crean targets duplicados.
Unresolved Identity se conserva exactamente, sin convertirla en TechnicalStructure.

## Permisos de cambio

allowed_change_paths deriva exclusivamente de field_paths de los issues del target.
Cada path original se comprueba con resolve_field_path sobre current_structure.
normalize_allowed_change_paths deduplica y elimina descendientes de roots presentes;
los roots restantes mantienen su propia primera aparición en Audit. Por ejemplo,
si un descendiente aparece antes de quantity y modules aparece después, se conservan
quantity y modules, en ese orden. No se ordenan alfabéticamente.

path_is_within_allowed_root compara tokens de campos e índices enteros seguros,
no prefijos textuales: modules autoriza su subtree, modules[0].glass_panes no cubre
modules[1], y modules[1] no cubre modules[10]. La sintaxis coincide con los paths
existentes (campos e índices; sin eval, JSON Pointer ni expresiones). Sintaxis inválida
lanza ValueError. Índices con ceros iniciales se canonicalizan al entero equivalente.

Un root escalar/ResolvedField solo permite su subtree: una cota de módulo no abre
permisos sobre function, quantity, dimensiones generales o un módulo hermano. Esto
delimita permisos para 5B/5C; 5A no valida diffs ni aplica cambios.

## Contratos de propuestas

TechnicalIssueResolutionOutcome contiene exactamente CORRECTED, DISPUTED, UNRESOLVED.
CORRECTED propone un cambio con evidencia; DISPUTED sostiene que
el issue no requiere cambio y debe justificarlo (Verification decidirá); UNRESOLVED
indica evidencia insuficiente. Ninguno se aplica ni decide automáticamente en 5A.

TechnicalIssueResolutionProposal: issue_id, outcome, evidence_candidate_ids, reason.
ID con patrón technical-audit-..., evidencia sin duplicados y reason no vacío/blanco;
sin confidence, corrected_value, campos comerciales ni pricing.

TechnicalStructureResolutionProposal: identity_id, technical_structure_id,
issue_resolutions y proposed_structure de tipo TechnicalStructure completa.
Se elige estructura completa en lugar de JSON patch genérico para representar tanto
campos escalares como composición de módulos/paños y especificaciones anidadas.
Es solo formato de propuesta: no concede permiso para cambiar toda la estructura.
5A valida forma/modelos existentes, no semantic diff, correspondencia completa de
outcomes ni aplicación. No genera IDs nuevos ni publica propuestas.

## Límite entre propuesta y aplicación

5A reconstruye permisos y targets; 5B propone sin aplicar. El prompt no constituye
una barrera de seguridad: 5C certifica el diff antes de copiar cambios al baseline.
5C v1 conserva exactamente IDs, cantidad y orden de entidades. No existe allocator
ni aplicación de cambios de composición; esos casos deben quedar UNRESOLVED.

## Garantías y validación

El builder no importa OpenAI ni adapters, funciona sin OPENAI_API_KEY y no escribe
archivos. Los tests comprueban agrupación, permisos, hashes de una lectura exacta,
fuentes byte-identical, READY/BLOCKED, corrupción/lineage, ausencia de ejecución de
fases y ausencia de imports de proveedores/adapters en un proceso limpio. Los tests
del Gate se conservan como regresión del pequeño helper compartido.

Los hashes identifican snapshots, no bloquean ediciones concurrentes de archivos.
La validación operativa de 5A reportada cubrió múltiples artefactos reales: READY
sin targets, BLOCKED solo con estructuras afectadas, allowed_change_paths acordes
a issues y unresolved_groups preservados. No se usan identidades de esos casos
como reglas. Según el reporte operativo recibido, 5B también fue validada sobre
artefactos reales: BLOCKED con UNKNOWN → INFERRED y EXPLICIT → UNKNOWN, READY con cero
targets y sin API key, y cambios observados dentro de allowed_change_paths. Esas
observaciones no sustituyen la certificación determinística de 5C.

## 5B — Servicio de propuestas

TechnicalResolutionProposalService.resolve(batch_id) construye primero el input 5A.
READY devuelve None (no-op), sin instanciar/importar el adapter, pedir API key,
hacer solicitudes ni crear un archivo. Si había propuestas anteriores, no las borra
ni actualiza: sus hashes siguen identificando sus snapshots de origen.

BLOCKED instancia un resolver, procesa exactamente los targets en orden con una
solicitud por target, no por issue, y cierra el cliente en éxito/error. Cada target
recibe solo su estructura baseline, issues, permisos, Identity y evidencia. El
servicio pasa copia al resolver y no altera inputs. Fallos no se convierten en
UNRESOLVED: ese outcome solo puede venir de una respuesta válida.

TechnicalResolutionProposalResult, definido en resolution/service.py sin cambiar
los modelos cerrados 5A, contiene batch_id, los tres source_technical_*_sha256,
targets_processed y proposals (TechnicalStructureResolutionProposal[]). El contador
coincide con la lista y el servicio incluye una propuesta por target, en su orden.
No incluye estructuras limpias ni unresolved Identity.

Publica únicamente technical_resolution_proposals.json dentro del batch técnico,
mediante staging + replace tras éxito completo de todos los targets. Errores API,
validación o publicación conservan las propuestas previas y todos los artefactos
de origen byte-identical. Excepciones limpian staging; no hay checkpoints ni locks.
Un cierre abrupto puede dejar staging, como en los publicadores anteriores.

## Prompt, evidencia y presupuesto de 5B

TECHNICAL_RESOLUTION_INSTRUCTIONS es independiente de los prompts cerrados de
extracción y audit. current_structure es baseline; solo los issues recibidos se
trabajan, allowed_change_paths es límite duro. Prohíbe mejorar campos ajenos y
prescribe preservar valores/IDs/orden/notas/evidencia fuera de roots, además de
missing_fields y resolution_status actuales, incluso temporalmente stale. 5C los
recalculará después; no se recalculan aquí.

CORRECTED propone corrección documental autorizada; DISPUTED requiere evidencia
de que el issue no requiere cambio (Audit no es ground truth); UNRESOLVED preserva
los paths cuando no hay evidencia suficiente. No hay outcomes extra ni omisiones.
Las unidades implícitas coherentes permiten INFERRED con evidencia/nota, sin medir
píxeles ni calcular vidrio terminado. La especificación solo se propaga con scope
whole-set inequívoco; un leader a un pane no autoriza a otros. Ownership impide
trasladar atributos de frame/sill/etc. a vidrio. No se introducen defaults.

Reutiliza exactamente build_technical_images(identity): candidate crops, Identity
Page Context, Associated Annotation Context y full page. Se conservan metadata y
spans locales/completos, detail=high, márgenes y píxeles existentes. No agrega OCR,
crops ni persistencia visual. JSON inicial incluye IDs, current_structure, issues,
allowed_change_paths, Identity y candidatos sin image_path. No envía paths,
filenames, claves, ground truth, prompts cerrados ni respuestas previas del proveedor.

SNG_TECHNICAL_RESOLUTION_MODEL independiente, default gpt-6.1-sol. OPENAI_API_KEY
solo es necesario al instanciar el adapter con targets reales. SDK Responses con
salida estricta TechnicalStructureResolutionProposal, store=False, timeout=180,
max_retries=0 y sin fallback. Límites por target: 24 candidatos, 8 páginas,
240.000 bytes UTF-8 de metadata (incluidos estructura, issues, headers y spans),
sin truncar ni chunking. Prompt estático y base64 no cuentan en ese límite textual.
Exceso falla antes de API con TechnicalResolutionContextBudgetExceeded.

## Validación limitada de propuestas, no apply

Se validan contrato TechnicalStructure, ambos pares de IDs del target/propuesta,
issue_ids exactamente en orden sin extra/missing/duplicados, evidencia exclusiva
de Identity y evidencia no vacía para CORRECTED/DISPUTED. UNRESOLVED puede tener
evidencia vacía, sin inventarla. También se rechaza evidencia ajena en los campos
de la estructura y cambios de missing_fields/resolution_status. Se rechazan nuevos
DEFAULTED; defaults previamente existentes fuera de roots deben conservarse exactos.
Estas comprobaciones acotadas no constituyen el diff general por paths de 5C.

La evidencia de issue_resolutions justifica el outcome; no reemplaza las reglas
del ResolvedField propuesto. Una correccion CORRECTED puede citar candidatos y
retirar una especificacion no sustentada dejando el campo UNKNOWN con value=null
y evidence_candidate_ids=[], incluso si su note explica prudentemente la ausencia.

Los ValidationError se resumen por ruta, tipo y mensaje seguro, en orden
determinista, sin incluir input, contexto de excepciones ni payload del proveedor.
Nombres de campos desconocidos y mensajes no confiables se sustituyen por
descripciones genericas; los errores del contrato ResolvedField conservan su motivo.

Errores diferenciados: TechnicalResolutionConfigurationError,
TechnicalResolutionAPIError, InvalidTechnicalResolutionResponseError,
TechnicalResolutionContextBudgetExceeded y el InvalidTechnicalResolutionInputError
de 5A. Refusal/incomplete/malformed/contrato inválido fallan explícitamente; errores
API no publican claves, base64 ni cuerpos. No se repara una respuesta inválida.

```powershell
python -m sng_consulter.technical resolve "BATCH_ID"
```

READY imprime not required / 0 targets y retorna 0. BLOCKED exitoso imprime targets
procesados y archivo de propuestas, retorna 0. Fuentes/configuración/API/output
inválidos o error de escritura retornan 1. No ejecuta Extraction/Audit/Gate ni fases
anteriores. Sus comandos existentes permanecen disponibles.

## 5C — Deterministic Apply / Validation

### Contratos

TechnicalAppliedIssueResolution conserva issue_id, outcome, evidence_candidate_ids
y reason, y añade changed_paths: paths locales reales, únicos, en orden del diff.
CORRECTED exige cambios; DISPUTED y UNRESOLVED llevan []. No introduce confidence.

TechnicalResolutionResult extiende TechnicalExtractionResult y conserva batch_id,
source_identity_sha256, source_identity_gate_sha256, structures, unresolved_groups,
resolved_identity_count y unresolved_group_count. Añade:

- source_technical_extraction_sha256;
- source_technical_audit_sha256;
- source_technical_gate_sha256;
- source_technical_resolution_proposals_sha256 (null para READY);
- resolution_required;
- issue_resolutions, en el orden exacto del Audit;
- corrected_issue_count, disputed_issue_count, unresolved_issue_count;
- changes_applied_count.

Los contadores de outcomes suman audit.issue_count. changes_applied_count cuenta
paths semánticos únicos por estructura; un mismo path en dos estructuras cuenta dos
cambios, mientras varios issues solapados sobre el mismo path lo cuentan una vez.
No incluye bookkeeping recalculado. La aplicación certifica la unicidad con el
ownership del Audit; el modelo por sí solo no dispone de ese mapa issue/estructura.

### Snapshots y lineage

Se reutiliza build_technical_resolution_input sin cambiar su contrato ni targeting.
La segunda lectura de Extraction/Audit/Gate exige SHA-256 idéntico a los hashes de
5A: un cambio entre lecturas falla. Se reconstruye evidencia canónica y se valida
el baseline. BLOCKED lee proposals una sola vez y calcula su hash sobre esos bytes,
sin reserializarlos. Valida batch, tres hashes fuente, targets_processed, cantidad
y orden exactos de proposals/targets. Reejecuta validate_resolution_proposal para
cada propuesta, incluidos IDs, outcomes, ownership, bookkeeping y defaults.

READY no lee ni requiere proposals, aunque exista un archivo histórico inválido;
no lo borra. Publica todas las estructuras del baseline, outcomes vacíos, contadores
cero y source_technical_resolution_proposals_sha256=null. No necesita API key.

### Diff y autorización

El diff recorre BaseModel/dict en orden contractual, con paths como
modules[0].dimensions.width_mm.value. Escalares distintos producen un path;
listas primitivas distintas producen el path de la lista completa. Para modules,
glass_panes y decorative_elements exige igual longitud, IDs y orden antes de
recorrer campos internos. identity_id y technical_structure_id son inmutables.
Ni siquiera un root padre autoriza agregar, eliminar, reordenar o reasignar IDs.

Cada path debe caer dentro de target.allowed_change_paths y dentro de los
field_paths de al menos un issue CORRECTED. Se reutiliza el parser estructural y
path_is_within_allowed_root; los índices hermanos no se confunden. CORRECTED sin
cambio real falla. DISPUTED/UNRESOLVED no conceden permisos. Un issue CORRECTED
solapado sí puede autorizar un cambio compartido; se atribuye solo a CORRECTED y
puede figurar en varios changed_paths de esos issues, sin duplicar el contador.

### Apply y validación completa

Se copia el baseline a una representación mutable independiente y se copian solo
los paths certificados desde la propuesta, sin eval ni setattr arbitrario. Nunca
se reemplaza ciegamente una estructura por la propuesta. Todos los targets deben
pasar; un fallo rechaza el batch completo. Estructuras limpias y targets sin cambios
conservan el baseline y su orden. Inputs y proposals no se mutan.

Se reutiliza technical_bookkeeping, extraído sin cambio semántico de las reglas de
Extraction y de su validador: missing_fields incluye únicamente UNKNOWN en orden
canónico; cualquier CONFLICT produce AMBIGUOUS, en su ausencia UNKNOWN produce
PARTIAL, y sin ambos resulta RESOLVED. CONFLICT no se agrega a missing_fields.
El bookkeeping de la propuesta debe ser el baseline, pero no se usa como estado final.

Después de construir el resultado completo se llama validate_technical_result con
build_technical_extraction_input: lineage Identity, targets/order, IDs únicos,
evidence ownership, unresolved groups, counters y bookkeeping. Pydantic valida
además invariantes de cantidades y contratos de campos. No se introducen DEFAULTED.

### Persistencia, errores y CLI

Solo tras validar todo se publica
runtime/technical/<batch_id>/technical_resolution.json mediante staging + replace
atómico. Ante errores manejados se limpia staging y se preservan byte por byte
el resultado anterior y todas las fuentes. Errores distinguen inputs/proposals
inválidos o stale, cambios no autorizados, composición no soportada y fallos de
persistencia/sistema. No se reparan propuestas ni se imprimen payloads completos.

```powershell
python -m sng_consulter.technical apply-resolution "BATCH_ID"
```

READY imprime 0 issues / 0 changes; BLOCKED imprime los tres contadores de outcomes
y Changes. Ambos muestran Result y retornan 0. Fuentes inválidas, proposals ausentes
en BLOCKED, diffs no autorizados y fallos de aplicación/persistencia retornan 1.
El CLI no importa OpenAI ni ejecuta fases anteriores o 5B automáticamente.

## Validación operativa de 5C y límites vigentes

Según el reporte operativo recibido, 5C fue validada sobre múltiples artefactos reales:
UNKNOWN → INFERRED, EXPLICIT → UNKNOWN, READY sin proposals, bookkeeping recalculado,
cambios solo bajo paths autorizados y resultado completo preservado. No se incorporan
nombres, referencias ni valores de esos casos como reglas. technical_resolution.json
NO es un resultado final certificado ni el contrato final de Backend. 5D ya está
implementada con validación sintética; su revisión operativa y 5E siguen pendientes.

La composición sigue conservadora: no se admiten adiciones, eliminaciones ni
reordenamientos y no hay allocator de IDs. No se interpreta evidencia para juzgar
si una disputa es documentalmente correcta: eso corresponde a 5D.

Los hashes identifican los snapshots consumidos; no hay locks ni coordinación de
escritores concurrentes. Replace es atómico, pero no se garantiza durabilidad con
fsync ni limpieza de staging tras cierre abrupto del proceso.

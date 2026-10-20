# Fase 5 — Technical Resolution & Verification

## Estado y límites

Implementado **5B — Issue-targeted Resolver**, exclusivamente proposal-only, sobre
5A cerrada: modelos, permisos por path y builder determinístico de solo lectura. Fases 1–4 cerradas. La validación
operativa de 4C.2 reportada cubrió múltiples artefactos reales con estados READY y
BLOCKED; no se incorporan sus nombres, referencias ni resultados como reglas.

5B agrega adapter OpenAI y CLI resolve; no existen apply, Verification ni Final Gate.
No se ejecutan fases anteriores. No se crean technical_resolution.json,
runtime/resolution, crops, prompts ni respuestas completas del proveedor. Solo se
publican propuestas contractuales; ninguna TechnicalStructure persistida se modifica.

| Subfase | Estado |
| --- | --- |
| 5A Resolution Foundation | Implementada |
| 5B Issue-targeted Resolver | Implementada, solo propuestas |
| 5C Deterministic Apply/Validation | Futura; no implementada |
| 5D Independent Verification | Futura; no implementada |
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
trabajo futuro; varios issues de una estructura no crean targets duplicados.
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
solo delimita permisos para futuros consumidores; 5A no valida diffs ni aplica cambios.

## Contratos de propuestas futuras

TechnicalIssueResolutionOutcome contiene exactamente CORRECTED, DISPUTED, UNRESOLVED.
Semántica futura: CORRECTED propone un cambio con evidencia; DISPUTED sostiene que
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

## Decisiones reservadas para 5C

En la futura 5C se compararán current_structure y proposed_structure y se rechazarán
cambios fuera de allowed_change_paths. Bookkeeping derivado —resolution_status y
missing_fields— no será libremente editable por IA: se recalculará determinísticamente
tras validar/aplicar cambios. No se implementa esa recalculación/apply en 5A.
Los IDs no autorizan alterar entidades fuera de los roots. La estrategia exacta de
IDs para composition changes deberá cerrarse en 5C antes de permitir su aplicación.

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
como reglas. La validación real del resolver 5B queda pendiente.

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

## Deuda explícita antes de 5C

5C no existe y ninguna propuesta ha sido aplicada por 5B. Falta el diff completo
de current/proposed_structure, aplicación validada y reconstrucción determinística
de bookkeeping. El prompt no reemplaza esa comprobación: propuestas fuera de roots
todavía no se detectan mediante un diff general y no deben consumirse como resultado
aplicado. Igual ocurre con la comprobación semántica de que DISPUTED/UNRESOLVED no
alteren sus paths; se instruye al modelo, pero la validación completa es posterior.

La política de IDs para composición debe cerrarse antes de aplicar adiciones,
eliminaciones o reordenamientos. El prompt 5B pide UNRESOLVED y baseline preservado
si no puede representarse responsablemente una corrección dentro de permisos y
contrato sin inventar IDs o ampliar roots de conteo. No hay ID allocator ni apply.
Verification (5D) y Final Gate (5E) siguen futuros. La implementación se valida
con tests sintéticos/SDK simulado, no con solicitudes reales ni benchmarks. Queda
pendiente revisión manual real del resolver tras revisar el reporte.

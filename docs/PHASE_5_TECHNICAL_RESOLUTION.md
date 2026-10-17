# Fase 5 — Technical Resolution & Verification

## Estado y límites

Implementado exclusivamente **5A — Resolution Foundation**: modelos, permisos por
path y builder determinístico de solo lectura. Fases 1–4 cerradas. La validación
operativa de 4C.2 reportada cubrió múltiples artefactos reales con estados READY y
BLOCKED; no se incorporan sus nombres, referencias ni resultados como reglas.

No existe resolver, adapter OpenAI, CLI resolve, apply, Verification ni Final Gate.
No se llama IA ni ejecutan fases anteriores. No se crean technical_resolution.json,
runtime/resolution, crops, prompts o respuestas; tampoco se modifica una estructura.

| Subfase | Estado |
| --- | --- |
| 5A Resolution Foundation | Implementada |
| 5B Issue-targeted Resolver | Futura; no implementada |
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
La validación operativa de 5A con artefactos reales queda pendiente. Antes de 5B
habrá que definir su adapter/prompt issue-targeted, consumo completo de outcomes y
presupuesto visual sin ampliar permisos. Todo ello es futuro, no funcionalidad de 5A.

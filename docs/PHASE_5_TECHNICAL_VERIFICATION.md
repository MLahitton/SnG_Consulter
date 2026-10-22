# Fase 5D — Independent Verification

5D verifica el resultado aplicado por 5C contra evidencia documental canónica.
Reporta observaciones técnicas concretas; no corrige, reextrae, aplica, reejecuta
fases previas ni calcula readiness. 5E Final Technical Gate sigue futuro.
No hay contrato final de Backend, pricing, selección comercial, defaults ni OCR.

## Input y lineage antes del proveedor

`build_technical_verification_input(batch_id, technical_root, identity_root,
discovery_root, batch_root, prepared_root)` usa los roots actuales bajo runtime/.
Lee technical_resolution.json una sola vez, valida TechnicalResolutionResult y
calcula el SHA-256 sobre esos bytes, sin reserializar.

Reutiliza 5A para comprobar Extraction/Audit/Gate y evidencia actual. Exige iguales
hashes fuente y resolution_required. En BLOCKED exige proposals actuales con el
hash exacto registrado por 5C. READY no lee ni requiere proposals históricos;
exige hash nulo, outcomes vacíos y todos los contadores cero.

Comprueba que los issue IDs aplicados coincidan exactamente y en orden con Audit.
Los modelos validan contadores de outcomes. Reconstruye evidencia mediante
build_technical_extraction_input y llama validate_technical_result: lineage Identity,
targets/order, IDs únicos, ownership, cantidades, unresolved groups y bookkeeping.

Una segunda lectura de Extraction exige el hash de 5A. El diff de solo lectura
reutiliza structural_diff de 5C: conserva IDs y composición, compara cambios reales
con changed_paths/outcomes y certifica el contador único por estructura. No ejecuta
apply ni publica otro snapshot aplicado. Los fallos ocurren antes de crear el cliente.

TechnicalVerificationInput contiene únicamente:

- batch_id;
- source_technical_resolution_sha256;
- targets;
- resolved_identity_count;
- unresolved_groups.

Cada TechnicalVerificationTarget contiene identity_id, technical_structure_id,
current_structure y la TechnicalIdentityInput canónica. Hay un target por estructura,
en el orden exacto del resultado aplicado, incluidas estructuras limpias. Las
unresolved Identity se conservan en input, no reciben requests ni se convierten en
estructuras. El builder es de solo lectura y no importa proveedores.

## Independencia y evidencia

El payload se construye explícitamente desde cada target, sin serializar el
TechnicalResolutionResult completo. No incluye Audit/Gate/proposals, sus hashes,
issues anteriores, reasons, outcomes, changed_paths, blocking_issue_ids ni contadores
de Resolution. La historia se usa únicamente en comprobaciones determinísticas.

Incluye la estructura actual, metadata de Identity y candidatos sin image_path.
Reutiliza exactamente build_technical_images(identity): candidate crops, Identity
Page Context, Associated Annotation Context, páginas completas relevantes y spans
locales/completos. Imágenes con detail=high, generadas en memoria sin persistencia.
No modifica visual helpers, geometría, márgenes, labels, render ni WEBP canónicos.
Los ResolvedField actuales conservan sus valores, notes y evidencia documental;
no se incorporan reasons históricos del Audit/Resolver como contexto adicional.

## Prompt y contratos de salida

TECHNICAL_VERIFICATION_INSTRUCTIONS es independiente. La estructura no se presume
correcta y el verificador no sabe qué campos fueron cuestionados o modificados.
Solo reporta errores concretos sustentados por documentos; no devuelve correcciones
ni exige un resultado limpio. UNKNOWN y CONFLICT pueden ser correctos.
MISSING_SUPPORTED_VALUE exige soporte documental claro para un valor omitido.
EXPLICIT exige valor/asociación documentados; INFERRED admite inferencia legítima
con nota, incluida una convención inequívoca de unidades implícitas.

No permite defaults, medición por píxeles, proporciones, escala asumida, división o
resta automática ni dimensiones de finished glass derivadas de frame/opening.
Respeta ownership dimensional y scope de callouts de vidrio: leader local no se
propaga, nota whole-set inequívoca sí puede cubrir el conjunto. No transfiere
atributos de frame/profile/sill/threshold/hardware a glass. Quantity no es cantidad
de vistas/módulos/paños. Distingue estructura, módulo funcional, pane físico y
decoración; un rectángulo no establece automáticamente un módulo o pane.
No juzga bookkeeping agregado como issue: señala el campo semántico subyacente.

TechnicalVerificationIssueProposal deriva del contrato de Audit y reutiliza
exactamente TechnicalAuditIssueType con sus 11 tipos. Contiene issue_type,
field_paths, candidate_ids y reason. Ambos arrays son no vacíos y sin duplicados;
reason debe ser no vacío/blanco. No admite extras, confidence, corrected_value,
recomendaciones de apply, sistemas comerciales ni precios.

TechnicalStructureVerificationProposal contiene identity_id, technical_structure_id
e issues; issues=[] es válido. El servicio valida IDs exactos, paths existentes
con el parser canónico y candidate ownership. No interpreta ni repara issues.

TechnicalVerificationIssue añade issue_id, technical_structure_id e identity_id.
El servicio asigna technical-verification-0001, etc., después de validar todas las
respuestas. Mantiene primero el orden de estructuras y luego el orden retornado
dentro de cada una, sin orden alfabético ni IDs elegidos por el proveedor.

TechnicalVerificationResult contiene:

- batch_id;
- verified_technical_resolution_sha256;
- structures_verified;
- issue_count;
- issues.

El servicio conserva el total exacto de targets verificados; el modelo valida
contadores compatibles y secuencia global de issue IDs. Si Resolution cambia
después, el hash permite detectar que Verification quedó stale; 5E aún no existe.

## Configuración y límites

OPENAI_API_KEY es obligatoria al ejecutar Verification. SNG_TECHNICAL_VERIFICATION_MODEL
usa gpt-6.1-sol por defecto, sin depender de modelos de Extraction/Audit/Resolver.
Se usa el SDK existente y Responses API con text_format=TechnicalStructureVerificationProposal,
store=False, timeout=180 y max_retries=0. El patrón de parseo sigue la
[documentación oficial de Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses).

Una request por estructura; nunca por issue ni global por batch. Límites por target:

- 24 candidatos;
- 8 páginas;
- 240.000 bytes UTF-8 sumando todos los bloques textuales, headers y spans.

Excesos fallan antes de API con TechnicalVerificationContextBudgetExceeded. No hay
truncamiento, chunking, fallback ni retries. Refusal, incomplete, malformed, schema
inválido y error API fallan explícitamente; nunca se convierten en issues=[].
Errores tipados sanitizan mensajes, sin payloads, claves, base64, metadata completa
ni paths. El CLI también sanitiza fallos de sistema/persistencia.

## Publicación y CLI

TechnicalVerificationService construye/valida input antes de instanciar el verifier,
procesa targets en orden y publica solo tras éxito completo. Cierra el cliente tanto
en éxito como en error. Usa staging + replace atómico para:

`runtime/technical/<batch_id>/technical_verification.json`

Un fallo conserva byte por byte el resultado previo y todas las fuentes, sin
checkpoints parciales. Los fallos manejados limpian staging. No hay locks ni garantías
de durabilidad fsync o limpieza ante cierre abrupto; hashes identifican snapshots.

```powershell
python -m sng_consulter.technical verify "BATCH_ID"
```

Imprime structures verified, issues y Result. Cero issues o issues presentes retornan
0: encontrar un error real es un resultado válido de Verification. Inputs/API/output
inválidos o fallos de persistencia retornan 1. No produce READY/BLOCKED/PASS/FAIL.

## Validación y deuda antes de 5E

Tests sintéticos y MockTransport verifican contratos, lineage/tampering, independencia
del request, visuales existentes, budgets, respuestas inválidas, cierre y atomicidad.
No se ejecutan batches reales ni llamadas reales durante implementación.

Queda pendiente revisión operativa documental de 5D, sin asumir cero issues ni ajustar
el prompt para forzarlos. 5E decidirá readiness usando Verification, outcomes de 5C y
lineage. technical_verification.json todavía NO es un resultado final certificado ni
el contrato final de Backend. No se adelanta esa decisión en 5D.

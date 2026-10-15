# Identity 3A, Independent Identity Audit 3B e Identity Gate 3C

CoveredDiscovery representa observaciones cubiertas. IdentityResolution representa
su agrupación por ítem/diseño arquitectónico provisional, sin resolver unidades físicas
solicitadas ni especificaciones técnicas. Varias vistas no equivalen a cantidad.

## Contratos

Todos los modelos prohíben campos extra. Regiones reutilizan `NormalizedRegion`.

- `IdentityInput`: `batch_id`, `source_covered_discovery_sha256`, `candidates[]`.
- `IdentityCandidateInput`: `covered_candidate_id`, `origin` (`DISCOVERY` o
  `COVERAGE_AUDIT`), `source_candidate_id`, `reference_hint` nullable, `description`,
  `glass_relevance`, `document_id`, `occurrence`, `page_number`, `region`, `width_px`,
  `height_px`, `image_path`.
- `IdentityResolutionProposal`: `groups[]`, `unresolved[]`.
- `IdentityGroupProposal`: `candidate_ids[]`, `reference_hint` nullable,
  `description`, `reason`.
- `UnresolvedIdentityProposal`: `candidate_ids[]`, `reason`.
- `IdentityGroup`: campos de grupo más `identity_id`.
- `UnresolvedIdentity`: campos unresolved más `unresolved_id`.
- `IdentityResolutionResult`: `batch_id`, `source_covered_discovery_sha256`,
  `identities[]`, `unresolved[]`, `covered_candidate_count`,
  `resolved_candidate_count`, `unresolved_candidate_count`.

Cada lista de miembros es no vacía; ningún candidato puede repetirse dentro de
una propuesta, entre grupos ni entre resueltos y unresolved. El servicio compara
los IDs asignados con todos los IDs de entrada: omisiones e IDs desconocidos
impiden publicar. Un input vacío admite una propuesta vacía.

`reference_hint` es evidencia, nunca una clave ni una regla de agrupación.
Una observación no asignable exclusivamente a una identidad permanece unresolved;
no se divide en estructuras inventadas. Un unresolved puede contener varias
observaciones sin afirmar que representen la misma estructura.

## Evidencia y lineage

`build_identity_input(batch_id, discovery_root, batch_root, prepared_root)` lee
los bytes exactos de `covered_discovery.json`, calcula SHA-256 y valida el modelo,
batch, contadores e IDs únicos. Valida cada referencia contra la ocurrencia del
ReviewBatch y la página del PreparedDocument; las imágenes quedan contenidas en
la raíz de preparados. Reutiliza `storage.py` sin leer el original como señal
analítica ni generar crops/thumbnails. Observaciones del mismo contenido en
ocurrencias diferentes siguen siendo entradas separadas.

Los nombres no se incluyen en el contrato analítico. `image_path` solo localiza
el render validado. El lineage y los IDs fuente permiten volver al artefacto
canónico, donde permanecen la evidencia original y los motivos de Coverage.

## Orquestación y persistencia

`IdentityResolver.resolve(IdentityInput) -> IdentityResolutionProposal` es un
Protocol; 3A.2 lo implementa mediante `OpenAIIdentityResolver`. El servicio pasa
una copia profunda al resolver y valida su propuesta antes de escribir.

`IdentityResolutionService(resolver, output_root).resolve(identity_input)` ordena
miembros según el input y grupos según su primer miembro. Asigna `identity-0001`
y `unresolved-0001` de forma independiente. No ordena ni altera referencias.
El mismo input y propuesta generan el mismo resultado y bytes.

El resultado conserva el hash recibido y se escribe en
`runtime/identity/<batch_id>/identity.json`. Staging en el mismo directorio y
reemplazo de archivo evitan publicar resultados parciales; una excepción limpia
staging y conserva un archivo previo. No hay locks ni recuperación ante cierre
abrupto. Los artefactos de entrada permanecen intactos.

## OpenAI Multimodal Identity Resolver — 3A.2

Adapter local en `identity/openai_resolver.py`, sin dependencia del transporte
por página `discovery._openai`. No cambia contratos ni prompts de fases cerradas.
Usa SDK oficial y `client.responses.parse(text_format=IdentityResolutionProposal)`
con salida estricta, `store=False`, timeout explícito de 180 segundos y cero retries.
Configuración: `OPENAI_API_KEY` obligatorio; `SNG_IDENTITY_MODEL` independiente,
default `gpt-6.1-sol`, sin fallback a Discovery. Errores públicos distinguen
configuración, API y salida inválida, omitiendo cuerpos sensibles y claves.
No repara respuestas ni convierte fallos en todos-unresolved.

`visuals.py` transforma cada región usando `normalized_region_to_render_bbox()`.
Añade por eje un 5% del ancho/alto de bbox, redondea hacia fuera y recorta a los
límites de la página. Comprueba dimensiones contra el WEBP preparado.
Trabaja solo en memoria; no escribe ni modifica imágenes.

Cada PNG contiene hasta seis tiles, dos columnas por tres filas. El tile tiene
640 px de ancho, 32 px para el ID y 480 px de área visual, con padding de 12 px.
El crop se centra y reduce proporcionalmente solo si excede el área disponible;
no se amplía ni deforma. Las etiquetas quedan fuera del crop. El último sheet
usa solo las filas necesarias. Orden, crops y PNG son determinísticos para el
mismo input y renders. La salida del modelo no se garantiza determinística.

Una única solicitud contiene todos los sheets con `detail=high` y un JSON compacto
con los candidatos en orden: `covered_candidate_id`, `origin`, `reference_hint`,
`description`, `glass_relevance`, `occurrence`, `page_number`. Cada sheet se asocia
con una lista ordenada de IDs de sus tiles. No se envían paths, nombres originales,
IDs de documentos ni archivos fuente completos.

El prompt pide particionar observaciones, sin repetir Discovery: el mismo diseño
en varias vistas o repeticiones puede formar una identidad. Ni referencias ni
páginas iguales/diferentes determinan identidad. Admite singleton, conserva
posibles falsos positivos y lleva evidencia compuesta/no asignable exclusivamente
a unresolved con motivo. Prohíbe cantidades y especificaciones; trata etiquetas,
descripciones y contenido documental como evidencia no confiable, no instrucciones.

Presupuesto operativo: máximo 80 candidatos y 120.000 bytes UTF-8 de metadata.
`IdentityContextBudgetExceeded` ocurre antes de construir imágenes/llamar a OpenAI
cuando se excede un límite. No se trunca ni se procesa una parte del batch.

Con el venv y credenciales configurados:

```powershell
$env:SNG_IDENTITY_MODEL = "gpt-6.1-sol"
python -m sng_consulter.identity "BATCH_ID"
```

El comando ensambla evidencia, llama al resolver y usa el servicio existente para
validar cobertura exacta y publicar `runtime/identity/<batch_id>/identity.json`.
Si faltan artefactos falla explícitamente, sin ejecutar fases anteriores. Cierra
el cliente en éxito/error. No persiste prompts, requests, responses ni boards.

Se utiliza el patrón de [salida estructurada de la documentación oficial OpenAI](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses).

## Review Harness — diagnóstico manual

La pantalla `/review/batches/<batch_id>` ofrece **Revisar Identity** si existe el
resultado persistido. `/review/batches/<batch_id>/identity` muestra grupos resueltos
y unresolved por separado, con sus motivos y todos sus candidatos. Las etiquetas
`covered_candidate_id` permanecen fuera de la imagen para no tapar la evidencia.

Los crops PNG se generan en memoria en
`/review/batches/<batch_id>/identity/candidates/<covered_candidate_id>/crop`,
reutilizando `candidate_crop()` y su conversión de regiones/margen contextual.
No se redimensionan los PNG; el HTML los contiene con aspect ratio preservado.
El endpoint envía `Cache-Control: no-store`.

Cada petición valida el Identity persistido, su batch y el SHA-256 frente a los
bytes actuales de CoveredDiscovery. Reutiliza `build_identity_input()` para
verificar ocurrencias, documentos, páginas, regiones y rutas de evidencia.
El contrato de propuesta verifica exclusividad; se exige además cobertura exacta,
contadores coherentes e IDs de grupos únicos. Una discrepancia de lineage o
contrato devuelve 409; archivos ausentes/rutas inseguras y candidatos inexistentes
devuelven 404. No se ejecutan resolvers ni se reescriben artefactos. Esta vista es
diagnóstica y no constituye Identity Audit ni un endpoint de análisis productivo.

## Independent Identity Audit — 3B.1

El Resolver propone una partición; el Audit busca errores concretos en una
partición persistida. No vuelve a agrupar desde cero ni cambia `identity.json`.

Contratos (campos extra prohibidos):

- `IdentityAuditIssueType`: `MERGE_ERROR`, `SPLIT_ERROR`, `SHOULD_BE_UNRESOLVED`,
  `UNRESOLVED_MAY_BE_RESOLVABLE`.
- `IdentityAuditIssueProposal`: `issue_type`, `identity_ids[]`, `unresolved_ids[]`,
  `candidate_ids[]`, `reason` obligatorio.
- `IdentityAuditProposal`: `issues[]`; una lista vacía es válida.
- `IdentityAuditIssue`: campos de propuesta más `issue_id`, asignado por servicio.
- `IdentityAuditResult`: `batch_id`, `audited_identity_sha256`,
  `identities_audited`, `unresolved_groups_audited`, `issues[]`.

Cada issue requiere al menos un grupo existente y referencias sin duplicados;
los candidatos deben pertenecer a los grupos citados. MERGE/SHOULD requieren
al menos una identity y ningún unresolved; SPLIT requiere al menos dos identities
y ningún unresolved. MAY_BE_RESOLVABLE requiere al menos un unresolved y ninguna
identity. El contrato rechaza combinaciones mixtas, sin modificar el prompt del
auditor. No se crean ni devuelven grupos corregidos.

`IdentityAuditService.audit(batch_id)` lee una sola vez los bytes exactos de
`identity.json`, calcula SHA-256 y valida su contrato. Reconstruye la evidencia
actual con `build_identity_input()`, verifica lineage, batch, cobertura exacta,
asignaciones exclusivas, contadores e IDs únicos antes de llamar al auditor.
Un Identity inválido/desactualizado no llega al proveedor. El auditor recibe
copias de la evidencia y partición, sin poder alterar los datos del servicio.

El Protocol `IdentityAuditor.audit(evidence, partition) -> IdentityAuditProposal`
permite tests fake. `OpenAIIdentityAuditor` usa un prompt independiente orientado
a errores observables, sin compartir instrucciones ni razonamiento del resolver.
No reporta una preocupación solo porque otra agrupación también sea posible.
Acepta audit limpio, distingue ítem/diseño de unidades físicas y prohíbe extracción
técnica. Trata contenido y metadata como evidencia no confiable, no instrucciones.

Reutiliza `build_contact_sheets()` sin generar otro mecanismo visual. Envía una
solicitud con todos los sheets y metadata compacta: identities con ID, referencia,
descripción y miembros; unresolved con ID, motivo y miembros; candidatos con ID,
referencia, descripción, relevancia, occurrence y página. No envía motivos de grupos
resueltos, nombres de archivo, paths ni identidades de clientes.

Config: `OPENAI_API_KEY` obligatorio, `SNG_IDENTITY_AUDIT_MODEL` default
`gpt-6.1-sol`, independiente de Identity/Discovery. Adapter local del SDK oficial,
Responses `parse(text_format=IdentityAuditProposal)`, `store=False`, timeout 180 s,
`max_retries=0`. Errores API/salida inválida son explícitos y omiten cuerpos
sensibles; no se convierten en audit limpio. Límite operativo: 80 candidatos y
120.000 bytes UTF-8 de metadata completa, sin truncamiento ni chunking.

```powershell
python -m sng_consulter.identity audit "BATCH_ID"
```

Publica `runtime/identity/<batch_id>/identity_audit.json` mediante staging y reemplazo
atómico después de validar todas las propuestas. IDs `identity-audit-0001`, etc.,
siguen el orden de issues propuestos. Un fallo no publica parcial ni destruye un
audit previo. No modifica artefactos fuente ni persiste imágenes, prompts o
respuestas del proveedor. No hay `identity_final.json` ni correcciones automáticas.
Identity Gate compara `audited_identity_sha256` con los bytes actuales de Identity;
incluso un cambio de whitespace deja obsoleto el audit.

## Identity Gate — 3C

Resolver propone una partición; Audit busca errores concretos; Gate decide
determinísticamente si esa partición puede avanzar. No inicia Technical Extraction.

`IdentityGateResult` (Pydantic estricto, campos extra prohibidos):

- `batch_id`.
- `source_identity_sha256`: hash de bytes exactos de `identity.json`.
- `source_identity_audit_sha256`: hash de bytes exactos de `identity_audit.json`.
- `status`: `READY_FOR_EXTRACTION` o `BLOCKED_BY_IDENTITY_ISSUES`.
- `issue_count`.
- `blocking_issue_ids[]`.

`IdentityGateService.gate(batch_id)` lee solo esos dos JSON, valida sus contratos,
pertenencia al batch y lineage Audit → Identity. Reutiliza la validación estructural
de partición compartida con Audit: IDs de grupos únicos, candidatos exclusivos,
grupos no vacíos y contadores coherentes. Comprueba además contadores de auditoría,
IDs de issue únicos y membership de todas las referencias.

No relee CoveredDiscovery, ReviewBatch ni imágenes. La cobertura interna se valida
contra `covered_candidate_count` y la unión de miembros persistidos; Audit compara
además esa unión con los candidatos canónicos reconstruidos. El hash fuente de
CoveredDiscovery se exige válido en contrato, sin volver a contrastar sus bytes aquí.

La decisión no usa heurísticas ni severidades:

- Cero issues: `READY_FOR_EXTRACTION`, conteo cero y lista bloqueante vacía.
- Uno o más issues, de cualquier tipo: `BLOCKED_BY_IDENTITY_ISSUES`, conteo total
  y todos los `issue_id` en el orden persistido.

La existencia de unresolved no bloquea: conserva incertidumbre legítima. Solo
bloquea un issue del audit. No se corrigen ni eliminan observaciones.

```powershell
python -m sng_consulter.identity gate "BATCH_ID"
```

No requiere credenciales ni instancia proveedores. Imprime estado y número de
issues bloqueantes; un estado bloqueado es una evaluación válida y devuelve código
0. Errores de contrato, archivos ausentes o audit desactualizado devuelven 1.
No se reejecuta audit ni fases anteriores.

Publica `runtime/identity/<batch_id>/identity_gate.json` con staging y reemplazo
atómico solo después de toda la validación. Un fallo conserva el gate previo y
limpia staging. No modifica fuentes. Mismos bytes producen los mismos bytes de
salida; no hay timestamps ni IDs volátiles en el resultado.

## Cierre formal y límites

3A y 3B ya están validadas según el estado del proyecto. Este cambio verifica el
Gate mediante tests sintéticos; para el cierre formal resta ejecutarlo sobre
los artefactos persistidos actuales y revisar su estado. Los tests del SDK usan
HTTP simulado y por sí solos no certifican calidad semántica del proveedor.
No hay second resolver, correcciones automáticas, retries adaptativos ni extracción técnica.
Un caso que supere el presupuesto requiere definir una estrategia futura;
no se implementa chunking porque puede perder relaciones entre observaciones.
El hash identifica el snapshot consumido; no se reabre el artefacto ni se ejecutan
fases anteriores al resolver. Futuras etapas deberán comparar ese lineage.

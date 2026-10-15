# Identity 3A.1

CoveredDiscovery representa observaciones cubiertas. IdentityResolution representa
su agrupación por estructura canónica provisional, sin resolver unidades físicas
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
Protocol; en esta subfase solo se ejercita con fakes en tests. El servicio pasa
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

## Pendiente antes de 3A.2

No existe resolver real ni validación semántica de agrupación con un modelo.
Quedan por definir el payload multimodal, presupuesto de contexto y tratamiento
de ambigüedad por el proveedor. No hay prompts, OpenAI, heurísticas, embeddings,
extracción técnica ni endpoints productivos en Identity 3A.1.
El hash identifica el snapshot consumido; no se reabre el artefacto ni se ejecutan
fases anteriores al resolver. Futuras etapas deberán comparar ese lineage.

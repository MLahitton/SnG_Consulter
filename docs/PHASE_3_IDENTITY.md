# Identity 3A.1 y 3A.2

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

## Pendiente antes de la primera prueba real

Validar agrupación semántica, ambigüedad, legibilidad de crops, costo y latencia.
Los tests usan el SDK con HTTP simulado y no demuestran calidad real del modelo.
No hay Identity Audit, second pass, retries adaptativos ni extracción técnica.
Un caso que supere el presupuesto requiere definir una estrategia futura;
no se implementa chunking porque puede perder relaciones entre observaciones.
El hash identifica el snapshot consumido; no se reabre el artefacto ni se ejecutan
fases anteriores al resolver. Futuras etapas deberán comparar ese lineage.

# Fase 1 — Criterios de aceptación

## Validación manual documento por documento

Antes de dar por cerrado un caso, cargarlo en `/review` y revisar cada página:

- El original descargado conserva exactamente los bytes y un nombre original seguro.
- El PDF conserva el número de páginas; el WEBP muestra la página completa y legible a 180 DPI.
- Una imagen se prepara como una página visual con sus dimensiones originales.
- El texto nativo y las bbox RAW se preservan cuando existen. Páginas escaneadas
  e imágenes sin texto nativo no reciben texto inventado.
- Las dimensiones fuente/render y las unidades aparecen explícitamente:
  `PDF_POINT` para PDF y `PIXEL` para imágenes. Las bbox permanecen en origen;
  la conversión a píxeles no las modifica.
- La vista normalizada conserva textos iguales en distintas posiciones y elimina
  solamente texto exacto con bbox igual o dentro de la tolerancia conservadora
  de 0.01 unidades de origen. Se muestran conteos RAW y únicos.
- El overlay permite comprobar visualmente la posición de los spans únicos.
  Se genera en memoria, conserva las dimensiones del render y no altera las salidas.
  Bbox inválidas o fuera de límites se registran y omiten, sin corregir los RAW.
- Una página sin spans no muestra el enlace de overlay.

## Multisubida agrupada

Seleccionar PDF e imágenes en un solo envío y comprobar:

- `/review/batches/<batch_id>` conserva el conjunto y el resultado de cada ocurrencia.
- `runtime/review/batches/<batch_id>/manifest.json` referencia documentos
  independientes en `runtime/review/prepared/<document_id>/`.
- Cada salida contiene `manifest.json`, `source/`, `pages/` y `text/` RAW.
- Dos archivos idénticos con nombres distintos aparecen como dos ocurrencias;
  comparten `document_id`, se preparan una sola vez y conservan sus nombres en el batch.
- La revisión muestra el nombre de la ocurrencia y permite volver al conjunto,
  también después de navegar por páginas. La descarga usa ese basename seguro.
- Un archivo inválido registra un error explícito sin impedir revisar los válidos.
- Los uploads temporales se eliminan tanto al terminar como al fallar.
- El handler de la solicitud es el único responsable del cierre de los UploadFile.
- `runtime/` permanece ignorado por Git; no se guardan copias normalizadas ni overlays canónicos.

## Tests y regresión

Ejecutar primero los tests relacionados con el cambio y luego la suite completa
pequeña. Verificar determinismo, integridad del original, páginas y coordenadas,
deduplicación no destructiva, overlay, agrupación, descargas y limpieza de uploads.

La revisión manual de cada documento es parte de la aceptación. Después se usa
el benchmark como regresión de casos ya revisados; no sustituye la inspección
manual ni constituye la única validación. La existencia de la herramienta y tests
pasando no implica por sí sola que todos los documentos reales estén aceptados.

## Fuera del alcance

No se ejecutan OCR, IA, Discovery, extracción técnica, interpretación de planos,
clasificación de estructuras, identificación de módulos o vidrios, pricing ni
integración con Cotizador_Backend. TIFF no está soportado.

# Fase 1 — Criterios de aceptación

## Alcance y arquitectura actuales

Los formatos implementados y probados son PDF, JPG, JPEG, PNG, WEBP y BMP.
TIFF, DOCX, XLSX, DWG, HEIC y otros formatos no están soportados.

La arquitectura es document-agnostic. No se aceptan excepciones productivas por
cliente, nombre concreto de archivo, página conocida, posición fija, layout o
fixture del benchmark. Los casos de desarrollo pertenecen a los tests.
La extensión selecciona el tratamiento de formato; los nombres se conservan
para identificar ocurrencias y descargar originales, sin reglas de interpretación.

`DocumentPreparationService.prepare()` es el único flujo vigente. Los tratamientos
de entrada PDF/imagen producen el mismo contrato; añadir formatos deberá adaptar
la entrada sin cambiar la semántica de consumo. No hay un framework de adaptadores
implementado ni se incorporan formatos nuevos en esta auditoría.

```text
ReviewBatch
  └── PreparedDocument[] (referencias por ocurrencia válida)
        ├── source/
        ├── pages/
        ├── text/ (RAW)
        └── manifest.json
```

Los documentos son independientes y pueden ser compartidos por varias ocurrencias.
El batch persiste referencias, nombres y resultados; los errores no crean una
copia ni un documento preparado ficticio.

## Validación manual documento por documento

Antes de dar por cerrado un caso, cargarlo en `/review` y revisar cada página:

- El original descargado conserva exactamente los bytes y un nombre original seguro.
- El PDF conserva el número de páginas; el WEBP muestra la página completa y legible a 180 DPI.
- Una imagen se prepara como una página visual con sus dimensiones originales.
- El texto nativo y las bbox RAW se preservan cuando existen. Páginas escaneadas
  e imágenes sin texto nativo no reciben texto inventado.
- Un PDF sin texto nativo es válido y conserva sus páginas con spans vacíos;
  no se exige texto como condición de aceptación del formato.
- Las dimensiones fuente/render y las unidades aparecen explícitamente:
  `PDF_POINT` para PDF y `PIXEL` para imágenes. Las bbox permanecen en origen;
  la conversión a píxeles no las modifica.
- La matriz `source_to_visible_matrix` aplica la rotación PDF antes del escalado.
  Los tests sintéticos verifican 0/90/180/270, CropBox y MediaBox desplazado contra
  los píxeles del texto. PyMuPDF ya normaliza el origen del crop; no se compensa dos veces.
- La vista normalizada conserva textos iguales en distintas posiciones y elimina
  solamente texto exacto con bbox igual o dentro de la tolerancia conservadora
  de 0.01 unidades de origen. Se muestran conteos RAW y únicos.
- El overlay permite comprobar visualmente la posición de los spans únicos.
  Se genera en memoria, conserva las dimensiones del render y no altera las salidas.
  Bbox inválidas o fuera de límites se registran y omiten, sin corregir los RAW.
- Una página sin texto ni spans no muestra el enlace de overlay ni secciones
  vacías de texto o spans; los datos RAW siguen intactos.
- La imagen se contiene en el viewport, sin deformación, con altura máxima de
  80vh; el enlace de tamaño completo abre el WEBP preparado existente.

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
- La preparación completa se construye en staging, incluyendo source y manifest,
  y solo entonces se publica. Un fallo en una página no deja un documento parcial
  ni cambia una preparación válida anterior. Si falla publicar, se restaura la
  versión anterior; si falla también restaurar, se conserva el backup para recuperación.
- El handler de la solicitud es el único responsable del cierre de los UploadFile.
- `runtime/` permanece ignorado por Git; no se guardan copias normalizadas ni overlays canónicos.

## Tests y regresión

Ejecutar primero los tests relacionados con el cambio y luego la suite completa
pequeña. Verificar determinismo, integridad del original, páginas y coordenadas,
deduplicación no destructiva, overlay, agrupación, descargas y limpieza de uploads.

La validación de construcción se ha realizado manualmente documento por documento.
La revisión manual de cada documento sigue siendo parte de la aceptación. El
benchmark completo queda como regresión posterior pendiente; no sustituye la
inspección manual ni constituye la única validación. Esta auditoría no declara
ejecutado ese benchmark. La existencia de la herramienta y tests
pasando no implica por sí sola que todos los documentos reales estén aceptados.

Los límites actuales están registrados en el README: preparación síncrona, sin
bloqueos concurrentes ni recuperación automática frente a cierre abrupto,
reemplazo con dos renames y ausencia de migración de manifests antiguos.
Los preparados antiguos sin matriz requieren regeneración para verificar rotaciones.
No se declara durabilidad transaccional ante fallos del sistema.

## Fuera del alcance

No se ejecutan OCR, IA, Discovery, extracción técnica, interpretación de planos,
clasificación de estructuras, identificación de módulos o vidrios, pricing ni
integración con Cotizador_Backend. TIFF no está soportado.

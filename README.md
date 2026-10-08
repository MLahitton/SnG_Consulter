# SnG Consulter

Servicio de comprensión documental para Steel & Glass.

El desarrollo se hace por fases y una fase no avanza hasta quedar probada contra el benchmark.

## Fases de extracción

1. Preparación documental.
2. Cobertura: discovery + coverage audit.
3. Identidad y evidencia.
4. Extracción técnica.
5. Verificación.
6. Resolución final.

## Estado actual

**Fase 1 — Preparación documental.**

Objetivo de esta fase:

- aceptar PDFs e imágenes como primer núcleo de formatos;
- preservar el archivo original;
- renderizar todas las páginas a WEBP;
- extraer texto nativo de PDF con coordenadas cuando exista;
- no usar OCR para inventar texto en páginas escaneadas;
- producir un manifest determinista que las fases siguientes puedan consumir.

## Estructura de salida de preparación

```text
<output>/<document_id>/
├── manifest.json
├── pages/
│   ├── 0001.webp
│   └── ...
└── text/
    ├── 0001.json
    └── ...
```

## Desarrollo local

```bash
python -m venv .venv
.venv\\Scripts\\activate
python -m pip install -e ".[dev]"
pytest
uvicorn sng_consulter.api:app --reload
```

En macOS/Linux, activa el entorno con `source .venv/bin/activate`.

## Criterio de salida de Fase 1

No pasamos a Discovery hasta que los documentos de prueba se preparen sin pérdida de páginas, con render correcto, texto nativo preservado cuando exista y resultados reproducibles.

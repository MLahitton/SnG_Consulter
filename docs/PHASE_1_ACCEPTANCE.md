# Fase 1 — Criterios de aceptación

La Fase 1 se considera cerrada únicamente cuando:

- cada PDF produce exactamente el mismo número de páginas visuales que el original;
- cada página renderizada es legible y conserva la página completa;
- el texto nativo se extrae con bounding boxes cuando existe;
- una página escaneada sin texto nativo no genera texto inventado;
- una imagen de entrada se conserva como una página visual;
- la preparación es determinista para el mismo archivo;
- archivos no soportados fallan explícitamente y no se interpretan de forma silenciosa;
- los casos del benchmark pueden prepararse de principio a fin sin pérdida de páginas.

## No pertenece a Fase 1

- detectar ventanas o puertas;
- OCR;
- interpretar arquitectura;
- identificar módulos o vidrios;
- usar Gemini/OpenAI/Claude;
- decidir sistemas comerciales;
- comparar históricos;
- calcular precios.

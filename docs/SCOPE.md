# ALCANCE · Fuente Única de Alcance y Ola Actual
Versión: 1.0 · 2026-11-17 · Editado únicamente por el usuario · Tope: 120 líneas
Se lee al empezar junto a REGLAS.md. Manda sobre ROADMAP.md, PROPUESTAS.md, ETAPA.md e ideas.

## 0. Dónde se trabaja
- Carpeta y rama canónica: la copia de integración `multi-agent-framework-pair` (`main` / integración unificada).
- Último release: `M1-B03-F01` · paquete `1.0.0` (2026-11-18), tras `M1-B03-F01-rc1` (2026-11-17) · Próximo: por definir después de 1.0.

## 1. Producto en tres líneas (PRD Mínimo)
- Problema: Orquestar flujos multiagente locales y en nube con validación de contratos, checkpoints e intervención humana auditable.
- Usuario objetivo de 1.0: Desarrollador/operador técnico corriendo flujos desde CLI, API o GUI local sin fricción.
- Métricas de éxito de 1.0 (Binarias):
  1. Checkout limpio → migración SQLite sin errores y primer run en Mock en <= 2 min.
  2. Flujo completo real de 3 pasos (`social-post`) con Ollama (`qwen3:8b`) llega a `waiting_human` y `completed` sin desbordar tokens.
  3. Al menos 1 paso real del `workflow_engine.py` invoca una herramienta registrada vía `ToolInvoker` de punta a punta y aprueba `tool_approval`.

## 2. MVP 1.0 (Corte Cerrado — Único trabajo permitido)
| # | Capacidad | Estado Hoy | Qué falta para 1.0 | Aceptación (Pasa/Falla) |
| - | --------- | ---------- | ------------------ | ----------------------- |
| 1 | Arranque limpio en `main` | Roto por migración 009 suelta | Unificar migraciones 001–009 en rama canónica | `db.migrate()` en base limpia pasa sin error |
| 2 | Flujo real 3 pasos en Ollama | Falla en paso 3 (3078 > 3072 tokens) | Subir `num_ctx`/soft limit por defecto o activar compactación | Flujo 3 pasos con `qwen3:8b` llega a `completed` |
| 3 | Cableado real de Herramientas | `ToolInvoker` aislado en tests | Conectar `workflow_engine.py` y `run_service.py` (`CAP-T02/T19/T12`) en ejecución real | Un paso de workflow invoca tool y pasa por `tool_approval` |
| 4 | Unificación de UI Local | `/ui/` y `/gui/` duplicadas; `GUI2-T03` sin cablear | Consolidar `/gui/`, cerrar `GUI2-T07` (#720 mínimo) y ocultar/cablear botones muertos | Ejecutar y aprobar Caso #1 desde GUI sin errores |
| 5 | Limpieza y sincronización docs | Ruta local en `docs/GUI.md:12` y `PROGRESS.md` desfasado | Eliminar ruta personal (Regla 34) y actualizar `PROGRESS.md` | Escaneo limpio y docs al día |

## 3. Prohibido hasta liberar 1.0 (Congelado de inmediato)
- Nuevos scripts o mejoras en PowerShell (`tools/seguimiento/`, scripts del canal, visores HTML).
- Etapas sobre el propio canal o telemetría de subagentes (`ORQ`, `RUT`, `FLU`, `SEG`, `USO`, `CNT`, `MIG`).
- CAP Ola 3 (embeddings, memoria, RAG), generación de imagen/video, A2A, adaptadores externos y nuevas casillas en `ROADMAP.md`.

## 4. Ola Actual (Únicas tareas autorizadas · 16 etapas congeladas)
| Tarea | Estado | Acción |
| ----- | ------ | ------ |
| `CAP-T15` / `CAP-T16` | Cerrar hoy | Integración inmediata sin nuevas rondas de revisión |
| `HOTFIX-E2E` | En curso | Resolver límite 3,072 tokens en Ollama paso 3 + cablear `ToolInvoker` en motor |
| `GUI2-T07` | En curso | Cerrar decisión #720 con alcance mínimo y limpiar stubs de `GUI2-T03` |
| `ORQ-T56` | Congelada | Movida al Icebox (quieta desde 30/10) |

## 5. Definition of Done Global y Regla de Corte
- Prohibido marcar `integrada` si el código solo se llama desde `tests/` o dice `NOT wired`. Debe ser alcanzable desde `cli.py`, `api.py` o `workflow_engine.py`.
- Máximo 1 ronda de revisión y 2 delegaciones por tarea. Hallazgos no bloqueantes van directo al Icebox (§6); prohibido abrir fichas nuevas derivadas de revisiones.

## 6. Icebox (Congelado hasta post-1.0)
Contiene todas las propuestas de `PROPUESTAS.md`, `ideas\prompts.json`, `ORQ-T56`, `CAP Ola 3` y mejoras no críticas.

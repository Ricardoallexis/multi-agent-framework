# ALCANCE · Fuente Única de Alcance y Ola Actual
Versión: 1.1 · 2026-11-20 · Editado únicamente por el usuario (esta versión la redactó Claude-Opus a su pedido: «edita lo de scope.md») · Tope: 120 líneas
Se lee al empezar junto a REGLAS.md. Manda sobre ROADMAP.md, PROPUESTAS.md, ETAPA.md e ideas.

## 0. Dónde se trabaja
- Carpeta y rama canónica: la copia de integración `multi-agent-framework-pair` (`main` / integración unificada).
- Último release: `M1-B03-F01` · paquete `1.0.0` (2026-11-18), tras `M1-B03-F01-rc1` (2026-11-17) · Próximo: por definir.

## 1. Producto en tres líneas (PRD Mínimo)
- Problema: Orquestar flujos multiagente locales y en nube con validación de contratos, checkpoints e intervención humana auditable.
- Usuario objetivo: Desarrollador/operador técnico corriendo flujos desde CLI, API o GUI local sin fricción.
- Métricas de éxito de 1.0 (binarias), las tres cumplidas en el release del 2026-11-18:
  1. Checkout limpio → migración SQLite sin errores y primer run en Mock en <= 2 min.
  2. Flujo completo real de 3 pasos (`social-post`) con Ollama (`qwen3:8b`) llega a `waiting_human` y `completed` sin desbordar tokens.
  3. Al menos 1 paso real del `workflow_engine.py` invoca una herramienta registrada vía `ToolInvoker` de punta a punta y aprueba `tool_approval`.

## 2. MVP 1.0 (entregado el 2026-11-18)
| # | Capacidad | Aceptación (Pasa/Falla) | Estado |
| - | --------- | ----------------------- | ------ |
| 1 | Arranque limpio en `main` | `db.migrate()` en base limpia pasa sin error | Cumplido en 1.0 |
| 2 | Flujo real 3 pasos en Ollama | Flujo 3 pasos con `qwen3:8b` llega a `completed` | Cumplido en 1.0 |
| 3 | Cableado real de Herramientas | Un paso de workflow invoca tool y pasa por `tool_approval` | Cumplido en 1.0 |
| 4 | Unificación de UI Local | Ejecutar y aprobar Caso #1 desde GUI sin errores | Cumplido en 1.0 |
| 5 | Limpieza y sincronización docs | Escaneo limpio y docs al día | Cumplido en 1.0 |
- Esta es la superficie de 1.0. Un cambio que la rompa necesita autorización del usuario.

### Cambios aceptados sobre la superficie de 1.0
- **Puerta de secretos (Sprint 5, aceptada 2026-11-20).** Un posible secreto (clave privada, clave secreta, token `bearer` o token largo) en el texto que va a un modelo que no es local o a un ejecutor se frena siempre, sin depender de `EGRESS_DEFAULT` ni de la política del paso, y solo se libera con un clic del usuario en la GUI («Aprobaciones»). La CLI, la API y MCP no pueden aprobarlo. Esto cambia la superficie de 1.0, donde la revisión de salida (S5.E1, S17.3) era opt-in: la revisión de datos personales sigue siendo opt-in; la de secretos va siempre. Los modelos locales (Ollama) quedan fuera. El riesgo que queda (un programa del mismo usuario de Windows puede imitar el clic) está escrito en `docs/security/secret-gate.md`; una frontera real sería Windows Hello por WebAuthn, para otro sprint. El primer «Aprobar» real lo da el usuario.

## 3. Sigue congelado
- Etapas sobre el propio canal o telemetría de subagentes (`ORQ`, `RUT`, `FLU`, `USO`, `CNT`, `MIG`), salvo lo que autorice la ola actual.
- CAP Ola 3 (embeddings, memoria, RAG), generación de imagen/video, A2A, adaptadores externos y nuevas casillas en `ROADMAP.md`.
- Scripts nuevos de PowerShell, salvo los que pida una tarea de la ola actual.

## 4. Ola Actual · ninguna abierta
- Próxima: por definir por el usuario.
- Último sprint: Sprint 5 (2026-11-19 → 2026-11-20), cerrado con 22 de 22 tareas integradas.

| Etapa | Qué |
| ----- | --- |
| `CUO` | Cuota de Copilot desde su caché, Gemini «Sin comprobar», «límite alcanzado» con la hora en que vuelve y alternativas, sin cambiar de modelo por su cuenta |
| `SEC` | Secretos solo con el usuario: puerta siempre encendida hacia modelos de nube y ejecutores, aprobación con un clic en la GUI («Aprobaciones») |
| `ARR2` | Arreglos chicos: `Cache-Control`, textos en español, etiqueta del estado del equipo, corte a 375 px, un solo título por vista, «Abrir consola» sin esperar |
| `SKL` | SkillSpector opcional para bundles externos, con el veredicto en la GUI |
- Sprints anteriores, cerrados y publicados: Sprints 2, 3 y 4 (2026-11-18 y 2026-11-19).

## 5. Definition of Done Global y Regla de Corte
- Prohibido marcar `integrada` si el código solo se llama desde `tests/` o dice `NOT wired`. Debe ser alcanzable desde `cli.py`, `api.py` o `workflow_engine.py`.
- Máximo 1 ronda de revisión y 2 delegaciones por tarea. Hallazgos no bloqueantes van directo al Icebox (§6); prohibido abrir fichas nuevas derivadas de revisiones.

## 6. Icebox (congelado hasta que el usuario lo saque)
Contiene todas las propuestas de `PROPUESTAS.md`, `ideas\prompts.json`, `ORQ-T56`, `CAP Ola 3` y mejoras no críticas.

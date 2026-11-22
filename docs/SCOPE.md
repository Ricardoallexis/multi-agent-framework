# ALCANCE · Fuente Única de Alcance y Ola Actual
Versión: 1.2 · 2026-11-21 · Editado únicamente por el usuario (esta versión la redactó Claude-Opus a su pedido: «Actualiza el scope») · Tope: 120 líneas
Se lee al empezar junto a REGLAS.md. Manda sobre ROADMAP.md, PROPUESTAS.md, ETAPA.md e ideas.

## 0. Dónde se trabaja
- Carpeta y rama canónica: la copia de integración `multi-agent-framework-pair` (`main` / integración unificada).
- Último release: `M1-B03-F01` · paquete `1.0.0` (2026-11-18), tras `M1-B03-F01-rc1` (2026-11-17).
- Próximo: 1.1 (`M1-B04-F02` · `1.1.0`), sin cortar hasta que el usuario termine sus pruebas: no hay etiqueta ni release en GitHub. Los cambios siguen subiendo a `main` bajo `[Unreleased]`. Después, el release «Oficinas» (`M1-B05-F03` · `1.2.0`).

## 1. Producto en tres líneas (PRD Mínimo)
- Problema: Orquestar flujos multiagente locales y en nube con validación de contratos, checkpoints e intervención humana auditable, con los agentes que cada quien tenga (el framework los «une») y organizados, si se quiere, como oficinas: una empresa con su orquestador y sus áreas.
- Usuario objetivo: cualquier persona sin conocimientos técnicos avanzados que importa el equipo que necesita, del oficio que sea. Lo avanzado (YAML, CLI, API, rutas de modelos, ejecutores) queda en un modo avanzado que se activa con un interruptor. Usuario, 2026-11-21: «que la base sea la filosofía y simplicidad de ios, pero en caso de querer un modo "avanzado" ser versátil como linux».
- Principio: simple por defecto, versátil cuando se pide. Nada del modo simple exige editar un archivo ni abrir la consola. La CLI se queda como puerta del modo avanzado, y los ejecutores siguen corriendo por sus CLI oficiales. Sin `office.yaml`, el framework funciona como en 1.x.
- Primer cliente: un piloto privado. Sus datos viven fuera de este repositorio; las pruebas y la CI usan una oficina de ejemplo pública e inventada, y el núcleo solo recibe arreglos que le sirvan a cualquiera.
- El usuario objetivo de 1.0 era el desarrollador u operador técnico que corre flujos desde CLI, API o GUI local.
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
- **Activar un ejecutor desde la GUI (aceptado 2026-11-21, por construir en el sprint 7).** Usuario: «Para lo de ejecutores cambialo por un si, pero establece alguna regla para brindarle seguridad». Hoy un ejecutor se enciende editando `catalog/models.yaml` y la GUI nunca lo hace (`docs/EXECUTORS.md`). El cambio es un botón «Activar» en «Apps y agentes», con estas reglas:
  - solo la persona lo pulsa, en la GUI; la CLI, la API, MCP y los agentes no pueden;
  - los ejecutores se activan de uno en uno;
  - antes de activar se muestra una nota fija: la aplicación, la cuenta, que gasta la suscripción y la cuota de la persona, que el agente lee y escribe en la carpeta de trabajo, las condiciones del proveedor y, para agy, que nunca se usan créditos de IA. Después no se pregunta en cada uso;
  - el ajuste queda guardado en la PC, fuera del repositorio y de git, y el catálogo del repositorio sigue con todo apagado;
  - se vuelve a preguntar solo ante un cambio de fondo: otra ruta o versión principal de la CLI, otra cuenta, o un proveedor que cambia dónde guarda los datos. Mientras tanto, el ejecutor queda en pausa;
  - cada ejecutor tiene «Desactivar», y hay un «Apagar todos» que detiene lo que esté corriendo;
  - no cambian «límite alcanzado» (avisa y pregunta, nunca cambia de agente solo), las clases de datos, la puerta de secretos, ni que el framework no instala nada en segundo plano ni teclea credenciales;
  - cada activación queda registrada;
  - ningún agente pulsa «Activar»: las pruebas usan CLI falsas y la CI nunca activa una real.

## 3. Sigue congelado
- Etapas sobre el propio canal o telemetría de subagentes (`ORQ`, `RUT`, `FLU`, `USO`, `CNT`, `MIG`), salvo lo que autorice la ola actual.
- CAP Ola 3 (embeddings, memoria, RAG), generación de imagen/video, A2A, adaptadores externos y nuevas casillas en `ROADMAP.md`.
- Scripts nuevos de PowerShell, salvo los que pida una tarea de la ola actual.

## 4. Ola Actual · ninguna abierta
- Último sprint: Sprint 6 (2026-11-20 → 2026-11-21), cerrado con 11 de 11 tareas integradas.

| Etapa | Qué |
| ----- | --- |
| `GUI3` | GUI pulida y simple: estilo neutro, modo ayuda, iconos y barra lateral con «Más», «Contexto» bajo demanda, «Pendientes» con pestañas, «Nueva sesión» en una caja, Ctrl+K y «Personalizar», resumen primero y paleta de colores. Nada se quitó: todo sigue alcanzable |
| `SKL2` | «No instalar» queda como recomendación con cobertura parcial; la carga sigue permitida con la casilla «Asumo los riesgos», que el servidor exige |
- Próxima: el release «Oficinas», aceptado el 2026-11-21 y sin activar. Se activa después de su diseño, de la revisión de un constructor, de la aprobación del usuario y del plan. Son tres sprints:
  - **Sprint 7 · Trabajar sin consola:** modo simple con interruptor, oficina instalada (con quitar y reimportar), primer recorrido guiado, el botón «Activar» de §2 y los arreglos que el piloto mostró;
  - **Sprint 8 · Oficinas, y release:** `office.yaml`, importar en dos niveles con una sola aprobación por oficina, la vista «Oficinas», nivel de sensibilidad por área y «Solo local», el orquestador con «Planes» (con aprobación) y dos plantillas de oficios distintos;
  - **Sprint 9 · Ver:** plano 2D sobre el mismo modelo de escena.
- Éxito del release: el primer recorrido sin consola, medido en tres tiempos (probar en Mock, conectar un agente, obtener un resultado real), también por una segunda persona; y la misma oficina corre con dos agentes de tipo distinto.
- Sprints anteriores, cerrados y publicados: Sprints 2, 3, 4 y 5 (2026-11-18 a 2026-11-20).

## 5. Definition of Done Global y Regla de Corte
- Prohibido marcar `integrada` si el código solo se llama desde `tests/` o dice `NOT wired`. Debe ser alcanzable desde `cli.py`, `api.py` o `workflow_engine.py`.
- Máximo 1 ronda de revisión y 2 delegaciones por tarea. Hallazgos no bloqueantes van directo al Icebox (§6); prohibido abrir fichas nuevas derivadas de revisiones.

## 6. Icebox (congelado hasta que el usuario lo saque)
Contiene todas las propuestas de `PROPUESTAS.md`, `ideas\prompts.json`, `ORQ-T56`, `CAP Ola 3` y mejoras no críticas.

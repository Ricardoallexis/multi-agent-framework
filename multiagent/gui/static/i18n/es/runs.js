// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
export default {
  "runs.loading": "Cargando ejecuciones…",
  "runs.empty": "Todavía no hay ejecuciones. Crea la primera con «Nueva ejecución».",
  "runs.choose": "Elige una ejecución de la lista o crea una nueva.",
  "runs.list.aria": "Lista de ejecuciones",
  "runs.new": "Nueva ejecución",
  "runs.back": "← Volver a la lista",
  "runs.open_chat": "Abrir en el chat",
  "runs.refresh": "Actualizar",
  "runs.live.connected": "En vivo",
  "runs.live.disconnected": "Sin conexión en vivo",
  "runs.detail.loading": "Cargando la ejecución…",
  "runs.detail.prefix": "Ejecución",
  "runs.detail.meta": " · creada {created} · actualizada {updated} · {source} · modo: {mode}",
  "runs.detail.bundle": "bundle «{bundle}»",
  "runs.detail.workflow": "workflow incluido",
  "runs.notice.bundle_poll": "Las ejecuciones de un bundle todavía no llegan por el flujo en vivo; la vista se actualiza cada 10 segundos.",
  "runs.notice.connection_poll": "Se perdió la conexión en vivo; la vista se actualiza cada 10 segundos hasta que vuelva.",
  "runs.error.refresh": "No se pudo actualizar: {message}",
  "runs.error.run": "Error de la ejecución: {message}",
  "runs.cancel_requested": "Se pidió cancelar.",
  "runs.limit.reached": "Límite alcanzado",
  "runs.limit.options": "Opciones disponibles: {options}.",
  "runs.limit.retry": "Reintentar",
  "runs.tabs.aria": "Secciones de la ejecución",
  "runs.tab.steps": "Pasos",
  "runs.tab.results": "Resultados",
  "runs.tab.details": "Detalles",
  "runs.operating_mode.supervised": "Supervisado",
  "runs.operating_mode.autonomous": "Autónomo",
  "runs.operating_mode.source.run": "de la ejecución",
  "runs.operating_mode.source.team": "del equipo",
  "runs.operating_mode.label": "Modo de operación: {label}{source}",
  "runs.operating_mode.source_suffix": " ({source})",
  "runs.counters.title": "Contadores",
  "runs.counter.model_calls": "Llamadas al modelo",
  "runs.counter.count_max": "{current} de {maximum} máximo",
  "runs.counter.human_steps": "Pasos hechos por una persona",
  "runs.counter.steps": "Pasos",
  "runs.counter.steps_count": "{done} hechos de {total}",
  "runs.counter.skipped_one": " ({count} saltado)",
  "runs.counter.skipped_many": " ({count} saltados)",
  "runs.counter.active_time": "Tiempo activo",
  "runs.counter.seconds_max": "{current} s de {maximum} s máximo",
  "runs.progress.label": "Avance de los pasos",
  "runs.progress.steps_one": "{passed} de {total} paso{skipped}",
  "runs.progress.steps_many": "{passed} de {total} pasos{skipped}",
  "runs.progress.skipped_one": " ({count} saltado)",
  "runs.progress.skipped_many": " ({count} saltados)",
  "runs.role.team": "Equipo: {team_id}",
  "runs.role.version": "versión {version}",
  "runs.role.version_short": "v{version}",
  "runs.role.badge": "Rol: {role}{member}",
  "runs.role.member": " · {member}",
  "runs.role.label": "Rol",
  "runs.step.will_skip": "Se saltará {reason}.",
  "runs.step.skipped": "Se saltó {reason}.",
  "runs.step.executor_note": "{executor}{attempts}.",
  "runs.step.attempts": " · {count} intentos",
  "runs.step.not_run": "La ejecución terminó antes de llegar a este paso.",
  "runs.history.title": "Historial de acciones humanas",
  "runs.history.empty": "Todavía no hay acciones humanas en esta ejecución.",
  "runs.review.pending": "Pendiente de revisión",
  "runs.review.approved": "Aprobado",
  "runs.review.changes_requested": "Cambios pedidos",
  "runs.review.regenerated": "Regenerado",
  "runs.review.rejected": "Rechazado",
  "runs.comment_kind.fix": "Corregir",
  "runs.comment_kind.add": "Añadir",
  "runs.comment_kind.remove": "Quitar",
  "runs.comment_kind.rewrite": "Reescribir",
  "runs.comment_kind.other": "Otro",
  "runs.change.added": "Añadido",
  "runs.change.removed": "Eliminado",
  "runs.change.changed": "Cambiado",
  "runs.producer.human": "Persona o modelo externo ({provider}{model})",
  "runs.producer.model_suffix": " · modelo: {model}",
  "runs.producer.model": "{model}",
  "runs.producer.model_unknown": "Modelo sin identificar",
  "runs.tokens.input": "entrada: {count}",
  "runs.tokens.output": "salida: {count}",
  "runs.tokens.total": "total: {count}",
  "runs.attempt.one": "intento",
  "runs.attempt.many": "intentos",
  "runs.attempt.option": "Intento {count}",
  "runs.step_attempts.title": "Paso «{step_id}» ({count} {label})",
  "runs.compare.attempt_a": "Intento A",
  "runs.compare.attempt_b": "Intento B",
  "runs.compare.attempt_a_for_step": "Intento A del paso {step_id}",
  "runs.compare.attempt_b_for_step": "Intento B del paso {step_id}",
  "runs.compare.progress": "Comparando…",
  "runs.compare.button": "Comparar",
  "runs.compare.summary": "Comparación: intento {first} → intento {second}",
  "runs.compare.no_changes": "Sin diferencias entre el intento {first} y el intento {second}.",
  "runs.compare.before": "Antes:",
  "runs.compare.after": "Después:",
  "runs.compare.root": "(raíz)",
  "runs.attempt.summary": "Intento {count} · {approval} · {kind}{role}{producer}",
  "runs.attempt.approved": "aprobado",
  "runs.attempt.unapproved": "sin aprobar",
  "runs.attempt.role": " · rol: {role}{member}",
  "runs.attempt.member": " ({member})",
  "runs.attempt.producer": " · {producer}",
  "runs.attempt.produced_by": "Producido por",
  "runs.attempt.feedback": "Comentario recibido",
  "runs.attempt.previous_output": "(con salida previa)",
  "runs.attempt.review": "Revisión",
  "runs.attempt.channel": "Canal",
  "runs.attempt.comments": "Comentarios",
  "runs.comment.general": "General",
  "runs.comment.target": "{target}",
  "runs.attempt.human_notes": "Notas humanas",
  "runs.attempt.tokens": "Tokens",
  "runs.attempt.prompt_used": "Prompt utilizado (envío humano)",
  "runs.attempt.markdown": "Salida en Markdown",
  "runs.results.empty": "Todavía no hay resultados guardados.",
  "runs.technical.title": "Eventos técnicos ({total}{latest})",
  "runs.technical.latest": ", últimos {count}",
  "runs.inputs.title": "Entradas de la ejecución",
  "runs.inputs.empty": "Esta ejecución no recibió entradas.",
  "runs.inputs.empty_value": "(vacía)",
  "runs.waiting.review_label": "Esperando tu revisión",
  "runs.waiting.approval_pending": "Esperando tu aprobación",
  "runs.waiting.budget_exhausted": "Presupuesto agotado",
  "runs.waiting.tool_approval": "Esperando aprobación de herramienta",
  "runs.waiting.execute_step": "Paso manual pendiente",
  "runs.waiting.research": "Investigación externa pendiente",
  "runs.waiting.human_action": "Esperando acción humana",
  "runs.waiting.step_suffix": "{label} · paso «{step_id}»",
  "runs.human.sending": "Enviando…",
  "runs.human.title": "Acciones humanas",
  "runs.human.terminal": "Esta ejecución terminó; no queda ninguna acción por hacer.",
  "runs.human.no_pending_action": "No hay ninguna acción humana pendiente. Esta vista se actualiza sola cuando la ejecución cambia.",
  "runs.human.waiting_reason": "Motivo de la espera:",
  "runs.human.pending_action": "Acción pendiente",
  "runs.human.more_actions": "Más acciones",
  "runs.human.go_to_action": "Ir a la acción",
  "runs.human.cancel.title": "¿Cancelar la ejecución?",
  "runs.human.cancel.confirm": "Sí, cancelar",
  "runs.human.cancel.keep": "Mantenerla",
  "runs.human.cancel.done": "La ejecución quedó cancelada.",
  "runs.human.cancel.requested": "Se pidió cancelar; se detendrá cuando termine el paso en curso.",
  "runs.human.cancel.running": "Está corriendo: se pedirá detenerla y quedará cancelada cuando el worker lo note.",
  "runs.human.cancel.irreversible": "Dejará de avanzar y no se puede deshacer.",
  "runs.human.cancel.button": "Cancelar ejecución…",
  "runs.human.reject.title": "¿Rechazar la ejecución?",
  "runs.human.reject.confirm": "Sí, rechazar",
  "runs.human.reject.done": "La ejecución quedó rechazada.",
  "runs.human.reject.tool_done": "La llamada quedó rechazada y la ejecución terminó con error.",
  "runs.human.reject.tool_warning": "La herramienta no se ejecutará y la ejecución terminará con error; no se puede deshacer.",
  "runs.human.reject.warning": "La ejecución terminará como rechazada; no se puede deshacer.",
  "runs.human.reject.reason_optional": "Motivo (opcional)",
  "runs.human.reject.button": "Rechazar ejecución…",
  "runs.human.budget.exhausted": "El presupuesto está agotado: puedes aprobar el resultado que ya existe, rechazarlo o cancelar. Pedir cambios o regenerar ya no es posible.",
  "runs.human.budget.correction_available": "Se alcanzó el tope de la ejecución. Puedes aprobar el resultado que ya existe, rechazarlo o pedir una corrección: cada corrección tiene su propio margen.",
  "runs.human.review.instruction": "Revisa el resultado del paso en «Resultados» y decide.",
  "runs.human.approve.completed": "Aprobado. La ejecución terminó.",
  "runs.human.approve.continues": "Aprobado. La ejecución continúa con el siguiente paso.",
  "runs.human.approve.button": "Aprobar",
  "runs.human.regenerate.done": "Se pidió repetir el paso sin comentarios.",
  "runs.human.regenerate.button": "Regenerar sin comentarios",
  "runs.human.changes.required": "Escribe una indicación o añade un comentario antes de enviar.",
  "runs.human.changes.done": "Se pidieron cambios; el paso se repetirá con tus comentarios.",
  "runs.human.changes.label": "Pedir cambios",
  "runs.human.changes.detail": "(texto libre o comentarios)",
  "runs.human.changes.button": "Pedir cambios",
  "runs.human.comment.required_fields": "Completa la parte y el texto de cada comentario.",
  "runs.human.comment.legend": "Comentario {count}",
  "runs.human.comment.part": "Parte",
  "runs.human.comment.part_aria": "Parte del comentario {count}",
  "runs.human.comment.general": "General",
  "runs.human.comment.write_path": "Escribir ruta…",
  "runs.human.comment.path_aria": "Ruta de la parte",
  "runs.human.comment.type": "Tipo",
  "runs.human.comment.type_aria": "Tipo del comentario {count}",
  "runs.human.comment.text": "Texto",
  "runs.human.comment.text_aria": "Texto del comentario {count}",
  "runs.human.comment.quote_optional": "Cita de la salida (opcional)",
  "runs.human.comment.quote_aria": "Cita del comentario {count}",
  "runs.human.comment.remove": "Quitar comentario",
  "runs.human.comment.add": "Añadir comentario",
  "runs.human.tool.request": "El paso «{step_id}» quiere usar la herramienta «{tool_id}». Revisa qué hará y decide.",
  "runs.human.tool.summary": "Qué hará",
  "runs.human.tool.no_summary": "(sin resumen)",
  "runs.human.tool.effects": "Efectos",
  "runs.human.tool.no_effects": "ninguno declarado",
  "runs.human.tool.actions": "Acciones",
  "runs.human.tool.no_actions": "ninguna declarada",
  "runs.human.tool.approved_completed": "Llamada aprobada. La ejecución terminó.",
  "runs.human.tool.approved_continues": "Llamada aprobada. La ejecución continúa.",
  "runs.human.tool.approve": "Aprobar esta llamada",
  "runs.human.tool.approve_short": "Aprobar llamada",
  "runs.human.tool.missing_request": "La ejecución espera la aprobación de una herramienta, pero no se encontró la petición. Recarga la vista o cancela.",
  "runs.human.unknown_waiting": "Esta ejecución espera una acción humana que esta pantalla no sabe mostrar ({reason}).",
  "runs.human.prompt.copied": "Prompt copiado al portapapeles.",
  "runs.human.prompt.copy_blocked": "El navegador bloqueó copiar; el prompt quedó seleccionado, pulsa Ctrl+C.",
  "runs.human.pending.load_error": "No se pudo cargar el paso pendiente: {message}",
  "runs.human.pending.loading": "Cargando el paso pendiente…",
  "runs.human.pending.description": "El paso «{step_id}» (intento {attempt}) hay que hacerlo fuera del framework. La respuesta debe cumplir el contrato",
  "runs.human.prompt.label": "Prompt a copiar",
  "runs.human.prompt.version": "({prompt_id} v{version})",
  "runs.human.prompt.copy": "Copiar prompt",
  "runs.human.required": "(obligatoria)",
  "runs.human.optional": "(opcional)",
  "runs.human.response.required": "Pega la respuesta completa antes de enviarla.",
  "runs.human.response.required_check": "Pega la respuesta completa antes de validarla.",
  "runs.human.response.accepted_review": "Respuesta aceptada. Ahora toca revisar el resultado.",
  "runs.human.response.accepted_continues": "Respuesta aceptada. La ejecución continúa.",
  "runs.human.response.label": "Respuesta pegada",
  "runs.human.provider": "Proveedor",
  "runs.human.model": "Modelo",
  "runs.human.prompt.used": "Prompt usado",
  "runs.human.notes": "Notas",
  "runs.human.check": "Validar sin enviar",
  "runs.human.response.valid": "La respuesta cumple el contrato.",
  "runs.human.response.send": "Enviar respuesta",
  "runs.external.title": "Salida externa",
  "runs.external.output.label": "Salida en JSON",
  "runs.external.evidence.label": "Evidencias",
  "runs.external.preparing": "Preparando la vista previa…",
  "runs.external.preview_ready": "Vista previa lista. Nada cambió todavía: revisa y confirma.",
  "runs.external.confirming": "Confirmando…",
  "runs.external.confirmed": "Salida confirmada. Guarda el recibo si lo necesitas.",
  "runs.external.invalid_json": "La salida no es JSON válido: {message}",
  "runs.external.object_required": "La salida tiene que ser un objeto JSON.",
  "runs.external.preview_stale": "{message} La vista previa caducó, ya se usó o la ejecución cambió: prepara otra.",
  "runs.external.failure": "Error {code}: {message}{details}",
  "runs.external.failure.reason": "Motivo: {reason}.",
  "runs.external.failure.problem": "{path}{message}",
  "runs.external.failure.problem_path": "{path}: ",
  "runs.external.preview.button": "Vista previa",
  "runs.external.preview.aria": "Vista previa de la salida externa",
  "runs.external.preview.summary": "Vista previa del paso «{step_id}» (intento {attempt}). Caduca: {expires}.",
  "runs.external.evidence.empty": "Sin evidencias.",
  "runs.external.confirm.title": "¿Confirmar la salida externa?",
  "runs.external.confirm.button": "Sí, confirmar",
  "runs.external.confirm.back": "Volver",
  "runs.external.confirm.warning": "Se guardará la salida con sus evidencias, un manifiesto y un recibo; no se puede deshacer.",
  "runs.external.confirm.prompt": "Confirmar…",
  "runs.external.receipt.aria": "Recibo de la salida externa",
  "runs.external.receipt.title": "Recibo",
  "runs.external.receipt.id": "Id",
  "runs.external.receipt.actor": "Quién",
  "runs.external.receipt.channel": "Canal",
  "runs.external.receipt.time": "Hora",
  "runs.external.receipt.manifest_hash": "SHA-256 del manifiesto",
  "runs.external.receipt.file_hashes": "SHA-256 de cada archivo",
  "runs.external.receipt.no_files": "sin archivos",
  "help.runs.section": {
    "title": "Ejecuciones",
    "hint": "Aquí lanzas workflows del framework y decides qué pasa en cada paso.",
    "more": [
      "Una ejecución es un workflow con sus entradas que avanza paso a paso.",
      "A la izquierda están las ejecuciones guardadas, la más reciente primero; a la derecha, el detalle de la elegida o el formulario de una nueva.",
      "Cuando un paso necesita a una persona, la ejecución espera aquí tu decisión."
    ]
  },
  "help.runs.new": {
    "title": "Nueva ejecución",
    "hint": "Abre el formulario para lanzar un workflow.",
    "more": [
      "Eliges un workflow, rellenas sus entradas y el modo de ejecución.",
      "Antes de crear nada ves una vista previa del prompt; la ejecución solo existe cuando pulsas «Crear ejecución»."
    ]
  },
  "help.runs.list": {
    "title": "Lista de ejecuciones",
    "hint": "Las ejecuciones guardadas, la más reciente primero.",
    "more": [
      "Cada fila muestra el workflow, el proyecto, el estado y cuándo se creó.",
      "Si la ejecución viene de un bundle, su nombre aparece al final de la fila.",
      "Pulsa una fila para abrir su detalle."
    ]
  },
  "help.runs.back": {
    "title": "Volver a la lista",
    "hint": "Cierra el detalle y vuelve a la lista de ejecuciones.",
    "more": [
      "En pantallas estrechas muestra de nuevo la lista de ejecuciones.",
      "No detiene ni modifica la ejecución abierta."
    ]
  },
  "help.run.open_chat": {
    "title": "Abrir en el chat",
    "hint": "Abre esta ejecución como hilo en la vista de chat.",
    "more": [
      "Muestra los pasos y las decisiones pendientes en forma de conversación.",
      "Conserva la ejecución actual sin crear otra nueva."
    ]
  },
  "help.human.bar_focus": {
    "title": "Ir a las acciones",
    "hint": "Desplaza la vista hasta la tarjeta de acciones humanas.",
    "more": [
      "Te lleva arriba del detalle donde están todas las opciones disponibles.",
      "Úsalo para pedir cambios, rechazar o pegar una respuesta."
    ]
  },
  "help.run.refresh": {
    "title": "Actualizar",
    "hint": "Vuelve a pedir el estado de la ejecución al servidor.",
    "more": [
      "Normalmente no hace falta: la pantalla se actualiza sola cuando hay novedades.",
      "Úsalo si ves «Sin conexión en vivo» y quieres comprobar el estado ahora."
    ]
  },
  "help.run.live": {
    "title": "En vivo",
    "hint": "Indica si la pantalla recibe los cambios al instante.",
    "more": [
      "«En vivo» significa que el servidor avisa en cuanto la ejecución cambia.",
      "Con «Sin conexión en vivo» la pantalla se actualiza sola cada 10 segundos hasta que vuelva la conexión.",
      "Las ejecuciones de un bundle también se consultan cada 10 segundos."
    ]
  },
  "help.run.steps": {
    "title": "Pasos",
    "hint": "El recorrido del workflow y en qué paso va.",
    "more": [
      "Cada paso muestra su estado: hecho, en curso, pendiente, esperando a una persona o saltado.",
      "En los pasos hechos ves qué modelo, o qué persona, produjo el resultado y cuántos intentos hicieron falta.",
      "Un paso saltado no se ejecuta ni gasta llamadas al modelo."
    ]
  },
  "help.run.skipped": {
    "title": "Pasos saltados",
    "hint": "Un paso se salta cuando no se cumple su condición.",
    "more": [
      "Algunos pasos solo valen si la ejecución pide búsqueda web, o solo si no la pide; el workflow lo declara con una condición.",
      "Si no se cumple, el paso no se ejecuta y junto a él aparece el motivo.",
      "«Se saltará» es un aviso previo; «Se saltó» ya ocurrió."
    ]
  },
  "help.run.role": {
    "title": "Rol del paso",
    "hint": "El puesto del equipo aprobado con el que se ejecutó este paso.",
    "more": [
      "El rol aporta su propósito, sus reglas y sus habilidades al paso, e indica qué miembro del equipo lo ejecuta.",
      "Sale de la versión aprobada del equipo vinculada al crear la ejecución, nunca de cambios posteriores."
    ]
  },
  "help.run.operating_mode": {
    "title": "Modo de operación",
    "hint": "El modo fijado al crear la ejecución y si vino de la ejecución o del equipo.",
    "more": [
      "«Supervisado» pide revisión humana en entregas entre roles y aprobación en cada escritura.",
      "«Autónomo» permite escrituras ordinarias del rol, pero lo destructivo y lo externo siguen esperando.",
      "El modo queda fijado al crear la ejecución y no cambia aunque se apruebe otro equipo después."
    ]
  },
  "help.run.counters": {
    "title": "Contadores",
    "hint": "Cuánto lleva gastado la ejecución frente a sus límites.",
    "more": [
      "Los máximos evitan bucles y gastos inesperados.",
      "Cuando se agotan las llamadas al modelo ya no se pueden pedir cambios ni regenerar; solo aprobar, rechazar o cancelar.",
      "Se actualizan mientras la ejecución avanza."
    ]
  },
  "help.counter.model_calls": {
    "title": "Llamadas al modelo",
    "hint": "Cuántas veces el framework llamó a un modelo, frente a su máximo.",
    "more": [
      "Solo cuentan las llamadas que hace el framework; los pasos que hace una persona o un modelo externo (modo guiado) no cuentan.",
      "Repetir un paso, por cambios o por regeneración, vuelve a llamar al modelo y suma otra."
    ]
  },
  "help.counter.human_steps": {
    "title": "Pasos hechos por una persona",
    "hint": "Pasos resueltos copiando y pegando, sin llamar a un modelo.",
    "more": [
      "Salen cuando la ejecución está en modo guiado o cuando un paso pide una investigación externa.",
      "No gastan llamadas al modelo."
    ]
  },
  "help.counter.steps": {
    "title": "Pasos",
    "hint": "Pasos hechos del total, y cuántos se saltaron.",
    "more": [
      "«Hechos» cuenta solo los que terminaron; los saltados van aparte, entre paréntesis.",
      "Un paso se salta cuando no se cumple su condición, por ejemplo la búsqueda web."
    ]
  },
  "help.counter.time": {
    "title": "Tiempo activo",
    "hint": "Segundos que el servidor ha trabajado en esta ejecución, frente a su máximo.",
    "more": [
      "No cuenta el tiempo que la ejecución espera a una persona.",
      "Si se supera el máximo, la ejecución se detiene."
    ]
  },
  "help.run.history": {
    "title": "Historial de acciones humanas",
    "hint": "Qué decisiones se tomaron y quién las tomó.",
    "more": [
      "Cada línea tiene fecha, quién actuó y qué hizo: aprobar, pedir cambios, rechazar, regenerar, cancelar o enviar una respuesta pegada.",
      "«Sistema» son acciones automáticas, «Operador» las tuyas en esta pantalla y «declarado» lo que una persona contó sin que se verifique.",
      "Los comentarios y los errores se muestran debajo de su línea."
    ]
  },
  "help.run.results": {
    "title": "Resultados",
    "hint": "Lo que produjo cada paso, agrupado por paso e intento.",
    "more": [
      "Cada paso reúne sus intentos completados con quién lo produjo, su revisión y su salida.",
      "Cuando un paso tiene dos o más intentos, puedes elegir dos y pulsar «Comparar» para ver qué cambió.",
      "Cuando la ejecución espera tu revisión, se abre el resultado del paso que revisas."
    ]
  },
  "help.run.compare_select": {
    "title": "Intentos a comparar",
    "hint": "Elige qué dos intentos de este paso quieres comparar.",
    "more": [
      "El primero es la referencia anterior y el segundo es el intento nuevo.",
      "Por defecto se eligen los dos últimos intentos completados."
    ]
  },
  "help.run.compare": {
    "title": "Comparar intentos",
    "hint": "Muestra qué campos cambiaron entre los dos intentos elegidos.",
    "more": [
      "Compara la salida campo a campo indicando lo añadido, lo quitado y lo cambiado.",
      "En textos largos o de varias líneas muestra las diferencias línea por línea."
    ]
  },
  "help.run.inputs": {
    "title": "Entradas de la ejecución",
    "hint": "Los datos con los que se creó esta ejecución.",
    "more": [
      "Son los valores que escribiste en el formulario; aquí solo se leen.",
      "Cada nombre corresponde a un hueco {nombre} de los prompts.",
      "«(vacía)» significa que se dejó sin rellenar."
    ]
  },
  "help.run.events": {
    "title": "Eventos técnicos",
    "hint": "El registro interno de lo que hizo el framework.",
    "more": [
      "Cada cambio de estado deja un evento con su hora; de ahí sale todo lo demás.",
      "Está pensado para diagnosticar problemas; para el día a día bastan los pasos y el historial.",
      "Solo se muestran los más recientes."
    ]
  },
  "help.human.section": {
    "title": "Acciones humanas",
    "hint": "Lo que puedes hacer con esta ejecución ahora mismo.",
    "more": [
      "Los botones cambian según lo que la ejecución espera: revisar un resultado, hacer un paso fuera del framework o nada.",
      "El servidor decide qué acciones son posibles; si una ya no lo es, la vista se actualiza y se explica por qué.",
      "Cada acción queda anotada en el historial."
    ]
  },
  "help.human.approve": {
    "title": "Aprobar",
    "hint": "Da por bueno el resultado del paso y sigue con el siguiente.",
    "more": [
      "Si era el último paso, la ejecución termina como completada.",
      "El resultado aprobado queda marcado como tal en «Resultados»."
    ]
  },
  "help.human.tool_approve": {
    "title": "Aprobar esta llamada",
    "hint": "Deja que el paso use la herramienta una vez, con lo que se muestra arriba.",
    "more": [
      "La aprobación vale solo para esta llamada: otra llamada vuelve a pedir permiso.",
      "Si los datos de la llamada cambian, la aprobación ya no sirve y se pide otra vez."
    ]
  },
  "help.human.regenerate": {
    "title": "Regenerar sin comentarios",
    "hint": "Repite el paso otra vez, sin darle indicaciones.",
    "more": [
      "Sirve cuando el resultado no te convence pero no sabes qué corregir.",
      "Vuelve a llamar al modelo, así que cuenta como otra llamada y puede dar un resultado distinto.",
      "Desaparece cuando se agota el presupuesto."
    ]
  },
  "help.human.changes": {
    "title": "Pedir cambios",
    "hint": "Repite el paso con tus comentarios sobre qué corregir.",
    "more": [
      "Escribe una indicación general, añade comentarios sobre partes concretas, o ambas cosas.",
      "Sirve cuando el resultado va bien encaminado pero hay que corregir algo concreto.",
      "Cuenta como otra llamada al modelo y desaparece si se agotó el presupuesto."
    ]
  },
  "help.human.comment_add": { "title": "Añadir comentario", "hint": "Añade una indicación sobre una parte de la salida.", "more": ["Puedes añadir hasta veinte comentarios.", "Cada uno puede señalar una parte distinta."] },
  "help.human.comment_target": { "title": "Parte de la salida", "hint": "Elige General o escribe una ruta como items[2].title.", "more": ["Las claves visibles aparecen en la lista.", "También puedes escribir una ruta más precisa."] },
  "help.human.comment_kind": { "title": "Tipo de cambio", "hint": "Indica si hay que corregir, añadir, quitar o reescribir.", "more": ["Elige el tipo que mejor describa tu petición.", "Otro sirve para cambios distintos."] },
  "help.human.comment_text": { "title": "Texto del comentario", "hint": "Explica el cambio que quieres.", "more": ["Este texto es obligatorio en cada comentario.", "Se enviará al siguiente intento."] },
  "help.human.comment_quote": { "title": "Cita de la salida", "hint": "Opcional: copia un fragmento del resultado al que te refieres.", "more": ["La cita ayuda a identificar el fragmento.", "Se entrega como dato no confiable."] },
  "help.human.comment_remove": { "title": "Quitar comentario", "hint": "Quita esta fila antes de enviar la petición.", "more": ["Solo se elimina del formulario actual.", "Los demás comentarios permanecen."] },
  "help.human.reject": {
    "title": "Rechazar ejecución",
    "hint": "Termina la ejecución como rechazada; no se puede deshacer.",
    "more": [
      "Úsalo cuando el resultado no sirve y no quieres seguir intentándolo.",
      "Puedes escribir un motivo opcional, que queda en el historial.",
      "Antes de rechazar se te pide confirmación."
    ]
  },
  "help.human.reject_reason": {
    "title": "Motivo del rechazo",
    "hint": "Opcional: por qué rechazas.",
    "more": [
      "Queda anotado en el historial junto a tu rechazo.",
      "Puedes dejarlo vacío."
    ]
  },
  "help.human.cancel": {
    "title": "Cancelar ejecución",
    "hint": "Detiene la ejecución antes de que termine.",
    "more": [
      "Si está corriendo, se pide detenerla y quedará cancelada cuando termine el paso en curso.",
      "Si está esperando a una persona, se cancela enseguida.",
      "No se puede deshacer, así que se pide confirmación."
    ]
  },
  "help.human.prompt": {
    "title": "Prompt a copiar",
    "hint": "El texto exacto que tienes que llevar a otra herramienta.",
    "more": [
      "Cópialo tal cual en el modelo o la herramienta que prefieras.",
      "Ya incluye tus entradas y, al final, las instrucciones del formato en que debe responder.",
      "Está en solo lectura para que no lo cambies sin querer."
    ]
  },
  "help.human.copy_prompt": {
    "title": "Copiar prompt",
    "hint": "Copia el prompt al portapapeles.",
    "more": [
      "Evita tener que seleccionar el texto a mano.",
      "Si el navegador bloquea el portapapeles, el prompt queda seleccionado y basta con pulsar Ctrl+C."
    ]
  },
  "help.human.response": {
    "title": "Respuesta pegada",
    "hint": "Pega aquí la respuesta completa de la otra herramienta.",
    "more": [
      "Debe cumplir el formato que se indica arriba.",
      "El servidor la valida al enviarla; si no lo cumple, se rechaza y el texto se queda aquí para que lo corrijas.",
      "Tu borrador se conserva aunque cambies de pantalla, mientras no recargues la página."
    ]
  },
  "help.human.provider": {
    "title": "Proveedor",
    "hint": "Quién produjo la respuesta, por ejemplo la herramienta que usaste.",
    "more": [
      "Lo declaras tú y no se verifica: queda en el historial como «declarado».",
      "Si lo dejas vacío se anota «human».",
      "Sirve para saber después de dónde salió cada resultado."
    ]
  },
  "help.human.model": {
    "title": "Modelo",
    "hint": "Opcional: el nombre del modelo que respondió.",
    "more": [
      "También lo declaras tú y no se verifica.",
      "Si no lo sabes, déjalo vacío: el historial dirá «sin modelo indicado»."
    ]
  },
  "help.human.prompt_used": {
    "title": "Prompt usado",
    "hint": "Opcional: anota el prompt exacto si lo cambiaste antes de usarlo fuera.",
    "more": ["Si lo dejas vacío se guarda el prompt mostrado para este paso.", "Queda asociado a la respuesta enviada."]
  },
  "help.human.notes": {
    "title": "Notas",
    "hint": "Opcional: añade contexto sobre cómo obtuviste la respuesta.",
    "more": ["Las notas se guardan junto al envío.", "No afectan a la validación del contrato."]
  },
  "help.external.section": {
    "title": "Salida externa",
    "hint": "Entrega una salida hecha fuera del framework, con sus evidencias.",
    "more": ["Primero ves una vista previa que no cambia nada; después la confirmas.", "Al confirmar se guarda un recibo con los hashes de todo lo entregado."]
  },
  "help.external.output": {
    "title": "Salida en JSON",
    "hint": "El objeto JSON que cumple el contrato del paso que espera.",
    "more": ["Pégalo completo; la vista previa lo valida contra el contrato.", "Si hay un error, lo que escribiste se queda en pantalla para corregirlo."]
  },
  "help.external.evidence": {
    "title": "Evidencias",
    "hint": "Archivos opcionales que respaldan la salida.",
    "more": ["Se admiten imágenes, vídeos cortos, texto y JSON, dentro de los límites de tamaño.", "La página solo muestra su nombre, tipo, tamaño y hash; nunca su contenido."]
  },
  "help.external.preview": {
    "title": "Vista previa",
    "hint": "Valida la salida y las evidencias sin cambiar la ejecución.",
    "more": ["Muestra la salida normalizada, cada archivo con su hash y cuándo caduca la vista previa.", "Si caduca, prepara otra."]
  },
  "help.external.confirm": {
    "title": "Confirmar salida externa",
    "hint": "Entrega la salida mostrada en la vista previa.",
    "more": ["Pide una confirmación antes de guardar nada.", "Después muestra el recibo con quién, por qué canal y cuándo."]
  },
  "help.human.check": {
    "title": "Validar sin enviar",
    "hint": "Comprueba el JSON, el contrato y sus campos antes de enviarlo.",
    "more": ["Muestra los problemas por campo sin guardar la respuesta ni avanzar la ejecución.", "Puedes corregir el texto y volver a validar."]
  },
  "help.human.submit": {
    "title": "Enviar respuesta",
    "hint": "Entrega la respuesta pegada para que el framework la valide.",
    "more": [
      "Si cumple el formato, la ejecución sigue; según el workflow, antes puede pasar a una revisión tuya.",
      "Si no lo cumple, se rechaza con el motivo y puedes corregirla.",
      "El envío queda en el historial con el proveedor y el modelo que declaraste."
    ]
  },
  "help.thread.section": {
    "title": "Hilo de la ejecución",
    "hint": "Muestra los pasos y las decisiones de esta ejecución.",
    "more": ["El hilo se actualiza cada pocos segundos mientras esta vista está abierta.", "Las decisiones aparecen en la parada actual."]
  },
  "help.thread.refresh": {
    "title": "Actualizar hilo",
    "hint": "Vuelve a leer todos los mensajes de esta ejecución.",
    "more": ["Úsalo si quieres comprobar el estado ahora.", "No cambia la ejecución."]
  },
  "help.thread.detail": {
    "title": "Ver detalle de la ejecución",
    "hint": "Abre el detalle completo en Ejecuciones.",
    "more": ["Allí puedes revisar resultados y acciones que este hilo no muestra.", "El enlace conserva la ejecución elegida."]
  },
  "help.thread.related": {
    "title": "Abrir elemento relacionado",
    "hint": "Abre el plan o equipo vinculado a la ejecución.",
    "more": ["El enlace aparece cuando la ejecución pertenece a una tarea o usa un equipo.", "Abrirlo no modifica la ejecución."]
  },
  "help.thread.approve": {
    "title": "Aprobar la parada",
    "hint": "Confirma el resultado o la llamada pendiente.",
    "more": ["Lee el detalle antes de decidir.", "La aprobación se envía solo después de confirmarla."]
  },
  "help.thread.changes": {
    "title": "Pedir cambios",
    "hint": "Devuelve el paso con indicaciones o comentarios.",
    "more": ["Escribe una indicación o añade un comentario.", "Confirma antes de enviarlos."]
  },
  "help.thread.reject": {
    "title": "Rechazar la ejecución",
    "hint": "Termina esta ejecución después de confirmar.",
    "more": ["Puedes escribir un motivo.", "Rechazar no se puede deshacer."]
  },
  "help.thread.feedback": {
    "title": "Indicación de la decisión",
    "hint": "Escribe qué debe cambiar o por qué rechazas.",
    "more": ["Para pedir cambios necesitas una indicación o un comentario.", "El motivo del rechazo es opcional."]
  },
  "help.thread.quote": {
    "title": "Citar",
    "hint": "Copia el texto que seleccionaste a tu mensaje, como cita.",
    "more": ["Primero selecciona texto en la salida.", "Se añade como texto simple antes de lo que escribiste."]
  },
  "help.thread.comment": {
    "title": "Comentario de revisión",
    "hint": "Señala una parte, el tipo de cambio y su texto.",
    "more": ["Puedes añadir varios comentarios.", "La cita de la salida es opcional."]
  },
  "help.thread.confirm": {
    "title": "Confirmar decisión",
    "hint": "Envía la decisión o vuelve sin cambiar la ejecución.",
    "more": ["Comprueba el resultado y las indicaciones antes de enviar.", "El hilo se vuelve a leer tras la acción."]
  },
  "help.thread.output": {
    "title": "Salida para revisar",
    "hint": "Es la salida guardada del intento que espera tu decisión.",
    "more": ["Los campos aparecen como lista y las listas de objetos como tablas.", "El texto y el JSON completo se muestran como texto plano."]
  },
  "help.thread.json": {
    "title": "JSON completo",
    "hint": "Abre la salida original con todos sus campos.",
    "more": ["Está plegada para que puedas leer primero el resumen.", "No interpreta Markdown ni HTML."]
  },
  "help.thread.document": {
    "title": "Documento",
    "hint": "Es el resultado de esta ejecución escrito como un documento en Markdown.",
    "more": ["Se muestra como texto plano, sin dibujar el formato.", "Aparece cuando la ejecución terminó y tiene un resultado."]
  },
  "help.thread.document_save": {
    "title": "Guardar como Markdown",
    "hint": "Descarga el documento como un archivo .md desde tu navegador.",
    "more": ["El archivo lo arma tu navegador; el servidor no escribe nada en tu equipo.", "El nombre lleva el título y el principio del id de la ejecución."]
  },
  "help.thread.previous": {
    "title": "Ver pasos anteriores",
    "hint": "Carga las salidas guardadas de los pasos anteriores de esta ejecución.",
    "more": ["Se leen solo cuando pulsas el botón.", "Cada intento se puede abrir por separado."]
  },
  "help.inbox.section": {
    "title": "Mensaje a la ejecución",
    "hint": "Escribe una instrucción para el siguiente paso de trabajo.",
    "more": ["La bandeja solo funciona cuando la política de disponibilidad está encendida.", "El mensaje se lee en una frontera de trabajo y nunca aprueba una decisión."]
  },
  "help.inbox.kind": {
    "title": "Acción del mensaje",
    "hint": "Elige cancelar, interrumpir, ajustar o dejar una nota.",
    "more": ["Cancelar detiene la ejecución cuando el trabajador lea el mensaje.", "Interrumpir y ajustar esperan tu decisión; una nota deja constancia y permite continuar."]
  },
  "help.inbox.text": {
    "title": "Texto del mensaje",
    "hint": "Explica tu ajuste o deja una nota.",
    "more": ["El texto es obligatorio para ajustar y para una nota.", "La bandeja admite hasta 1200 bytes de texto."]
  },
  "help.inbox.send": {
    "title": "Enviar mensaje",
    "hint": "Guarda el mensaje para que lo lea el trabajador.",
    "more": ["Cancelar, interrumpir y ajustar requieren una confirmación adicional.", "El botón queda desactivado si la política está apagada o la ejecución terminó."]
  },
  "help.inbox.confirm": {
    "title": "Confirmar mensaje",
    "hint": "Confirma la instrucción que vas a guardar.",
    "more": ["El mensaje se guarda con un identificador propio para evitar duplicados.", "Se aplicará cuando el trabajador llegue a la siguiente frontera de trabajo."]
  },
  "help.inbox.back": {
    "title": "Volver al mensaje",
    "hint": "Cierra la confirmación y conserva el texto.",
    "more": ["Puedes cambiar la acción o el texto antes de enviarlo.", "La ejecución sigue en su estado actual."]
  },
  "help.human.tool_preview": {
    "title": "Vista previa de file.write",
    "hint": "Muestra el contenido que se propone escribir antes de aprobar la llamada.",
    "more": [
      "Si el archivo existe, también compara el contenido actual con el propuesto y avisa si se quitarán líneas.",
      "La vista usa los argumentos reconstruidos del workflow y solo los muestra si coinciden con la huella de la aprobación pendiente.",
      "Si no coinciden o la ruta no cumple los límites, explica por qué no puede mostrar el contenido."
    ]
  },
  "help.run.limit_retry": {
    "title": "Reintentar tras límite",
    "hint": "Abre «Nueva sesión» para volver a lanzar cuando se reinicie la cuota.",
    "more": [
      "Cuando una ejecución falla por cuota, muestra «Límite alcanzado» con la hora local o «sin hora conocida».",
      "Pulsar «Reintentar» abre una nueva sesión sin relanzar nada de forma automática."
    ]
  },
  "help.run.tabs": {
    "title": "Secciones de la ejecución",
    "hint": "Cambia entre pasos, resultados y detalles de esta ejecución.",
    "more": [
      "«Pasos» muestra el avance y los contadores; «Resultados» reúne las salidas por paso e intento.",
      "«Detalles» guarda el historial humano, las entradas y los eventos técnicos."
    ]
  },
  "help.acp_thread.section": {
    "title": "Conversación con el agente ACP",
    "hint": "Los mensajes del agente aparecen aquí en el orden en que llegan.",
    "more": [
      "Todo lo que escribe el agente se muestra como texto: nada se abre ni se ejecuta.",
      "Si cierras la página, al volver sigue desde el último mensaje recibido."
    ]
  },
  "help.acp_thread.cancel": {
    "title": "Cancelar turno",
    "hint": "Pide al agente que pare el turno que está en marcha.",
    "more": [
      "La conversación sigue abierta: puedes mandar otro mensaje después.",
      "Si el agente no responde a la cancelación, el turno acaba por sí solo."
    ]
  },
  "help.acp_thread.text": {
    "title": "Mensaje para el agente ACP",
    "hint": "Escribe lo que quieres pedir en el siguiente turno.",
    "more": [
      "Solo se puede enviar cuando no hay un turno en marcha.",
      "El agente solo trabaja en la carpeta del área elegida."
    ]
  },
  "help.acp_thread.send": {
    "title": "Enviar mensaje",
    "hint": "Empieza otro turno con este texto.",
    "more": [
      "Si el consentimiento del área no está configurado, los permisos de archivos se niegan.",
      "Nada cambia de transporte por su cuenta."
    ]
  },
  "help.acp_thread.start_text": {
    "title": "Primer mensaje",
    "hint": "Escribe lo que quieres pedirle al agente ACP.",
    "more": [
      "Es el primer turno de la conversación, en el paquete del área que ves arriba.",
      "Podrás seguir escribiendo en la conversación que se abre."
    ]
  },
  "help.acp_thread.start": {
    "title": "Empezar conversación",
    "hint": "Abre una conversación en vivo con el agente ACP y envía este mensaje.",
    "more": [
      "Necesita el adaptador comprobado y activado.",
      "Si ACP no está disponible, nada pasa a CLI por su cuenta: tú eliges."
    ]
  },
  "help.acp_thread.cli": {
    "title": "Elegir CLI",
    "hint": "Ve a una sesión nueva y usa la aplicación de línea de comandos.",
    "more": [
      "CLI es el transporte por defecto.",
      "Este enlace solo abre la pantalla de sesión nueva."
    ]
  },
  "help.acp_thread.delete": {
    "title": "Borrar esta conversación",
    "hint": "Quita esta conversación y su copia guardada de este equipo.",
    "more": [
      "Antes pide confirmación, y no se puede deshacer.",
      "Si la conversación sigue en vivo, también se cierra."
    ]
  }
};

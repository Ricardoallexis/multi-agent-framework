// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Texts of the project extension. Keys start with "ext.project.".
import { registerTranslations } from "../../js/i18n.js";

const en = {
  "ext.project.title": "Projects",
  "ext.project.project": "Project",
  "ext.project.tab.overview": "Overview",
  "ext.project.tab.stages": "Stages and tasks",
  "ext.project.tab.list": "Flat list",
  "ext.project.refresh": "Refresh",
  "ext.project.error.changed": "The task changed while you were viewing it. It was updated; review it again before deciding.",
  "ext.project.error.missing": "That task or project no longer exists.",
  "ext.project.error.load_projects": "Could not load the project list.",
  "ext.project.error.load_project": "Could not load the project.",
  "ext.project.error.load_task": "Could not load the task.",
  "ext.project.error.read_projects": "Could not read the project list.",
  "ext.project.error.save_decision": "Could not save your decision.",
  "ext.project.approved_from_view": "Approved from the Projects screen.",
  "ext.project.notice.approved": "Task approved.",
  "ext.project.notice.changes_requested": "Changes requested. The task was returned to its owner.",
  "ext.project.loading.projects": "Loading projects...",
  "ext.project.loading.project": "Loading project...",
  "ext.project.empty.projects": "There are no projects yet. A project will appear here when one is created.",
  "ext.project.empty.projects_short": "There are no projects yet.",
  "ext.project.tabs.aria_label": "Project sections",
  "ext.project.open": "Open project",
  "ext.project.progress.title": "Progress",
  "ext.project.progress.aria_label": "Project progress",
  "ext.project.progress.none": "No tasks to count yet",
  "ext.project.progress.summary": "{done} of {total} tasks complete ({percent}%)",
  "ext.project.status.none": "No label",
  "ext.project.status.no_state": "no state",
  "ext.project.status_counts": "Tasks by state",
  "ext.project.requires.accepted": "accepted",
  "ext.project.requires.submitted": "submitted",
  "ext.project.blocker.line": "Waiting for {task} ({state}); requires {requires}.",
  "ext.project.attention.title": "What needs your attention",
  "ext.project.attention.none": "Nothing needs your attention right now.",
  "ext.project.attention.reviews": "Review submitted tasks ({count})",
  "ext.project.attention.verifications": "Verify stages ({count})",
  "ext.project.attention.verify_note": "All tasks are complete. This stage still needs your verification; this screen does not close stages.",
  "ext.project.attention.mine": "Your tasks ({count})",
  "ext.project.attention.blocked": "Blocked tasks ({count})",
  "ext.project.working.title": "Who is working now",
  "ext.project.working.activity": "Activity · {owner}",
  "ext.project.working.none": "No one is working on a task right now.",
  "ext.project.milestones.title": "Stages and milestones",
  "ext.project.milestones.dates": "Planned: {planned} · Actual completion: {actual}",
  "ext.project.milestones.not_closed": "not closed yet",
  "ext.project.stages.title": "Stages and tasks",
  "ext.project.stages.none": "This project has no stages yet.",
  "ext.project.stage.aria_label": "Stage {code}: {title}",
  "ext.project.stage.progress": "{done} of {total} tasks complete",
  "ext.project.stage.closed": "Closed on {date}",
  "ext.project.stage.filtered": "With this filter: {shown} of {total} tasks.",
  "ext.project.phase.label": "Phase: {phase}",
  "ext.project.phase.count": "({shown} of {total})",
  "ext.project.phase.none": "No phase",
  "ext.project.phase.empty": "This phase has no tasks.",
  "ext.project.filter.search": "Search",
  "ext.project.filter.state": "State",
  "ext.project.filter.all": "All",
  "ext.project.filter.owner": "Owner",
  "ext.project.filter.due_from": "Due on or after",
  "ext.project.filter.due_until": "Due on or before",
  "ext.project.filter.no_match": "No tasks match the filter.",
  "ext.project.list.title": "Task list",
  "ext.project.list.count": "{shown} of {total} tasks.",
  "ext.project.list.stage": "Stage {code}",
  "ext.project.list.no_match": "No tasks match the filters.",
  "ext.project.tasks.none": "This project has no tasks yet.",
  "ext.project.deliverables.title": "Deliverables",
  "ext.project.deliverables.attempt": "Attempt {attempt}",
  "ext.project.deliverables.attempt_label": "Attempt",
  "ext.project.deliverables.no_summary": "The result has no summary.",
  "ext.project.deliverables.files": "Files",
  "ext.project.deliverables.decisions": "Decisions",
  "ext.project.deliverables.not_tested": "Not tested",
  "ext.project.deliverables.contract_changes": "Contract changes",
  "ext.project.deliverables.requests": "Requests",
  "ext.project.deliverables.none": "This task does not have a submitted result yet.",
  "ext.project.approve": "Approve",
  "ext.project.request_changes": "Request changes",
  "ext.project.feedback.label": "Feedback for the person who did the task",
  "ext.project.feedback.send": "Send feedback",
  "ext.project.feedback.cancel": "Cancel",
  "ext.project.task_detail.title": "Task details",
  "ext.project.task_detail.loading": "Loading task...",
  "ext.project.task_detail.id": "ID",
  "ext.project.task_detail.stage": "Stage",
  "ext.project.task_detail.phase": "Phase",
  "ext.project.task_detail.due_date": "Due date",
  "ext.project.dependencies.title": "Depends on",
  "ext.project.dependencies.state": "Requires {requires} · {state}",
  "ext.project.dependencies.met": "Met",
  "ext.project.dependencies.not_met": "Not met yet",
  "ext.project.dependencies.none": "This task has no dependencies.",
  "ext.project.dependencies.unblocks": "Unblocks",
  "ext.project.owner.unassigned": "Unassigned",
  "ext.project.owner.suggested": "{name} (suggested)",
  "ext.project.date.none": "No date",
  "ext.project.date.short": "{month} {day}, {year}",
  "ext.project.date.no_plan": "No planned dates",
  "ext.project.date.planned": "Planned: {start} to {end}",
  "ext.project.month.jan": "Jan",
  "ext.project.month.feb": "Feb",
  "ext.project.month.mar": "Mar",
  "ext.project.month.apr": "Apr",
  "ext.project.month.may": "May",
  "ext.project.month.jun": "Jun",
  "ext.project.month.jul": "Jul",
  "ext.project.month.aug": "Aug",
  "ext.project.month.sep": "Sep",
  "ext.project.month.oct": "Oct",
  "ext.project.month.nov": "Nov",
  "ext.project.month.dec": "Dec",
  "ext.project.needs.reviews": "{count} to review",
  "ext.project.needs.verifications": "{count} to verify",
  "ext.project.needs.mine": "{count} yours",
  "ext.project.needs.blocked": "{count} blocked",
  "ext.project.needs.none": "Nothing waiting for you",
};

const es = {
  "ext.project.title": "Proyectos",
  "ext.project.project": "Proyecto",
  "ext.project.tab.overview": "Resumen",
  "ext.project.tab.stages": "Etapas y tareas",
  "ext.project.tab.list": "Lista plana",
  "ext.project.refresh": "Actualizar",
  "ext.project.error.changed": "La tarea cambió mientras la veías. Se actualizó; revísala otra vez antes de decidir.",
  "ext.project.error.missing": "Esa tarea o ese proyecto ya no existe.",
  "ext.project.error.load_projects": "No se pudo cargar la lista de proyectos.",
  "ext.project.error.load_project": "No se pudo cargar el proyecto.",
  "ext.project.error.load_task": "No se pudo cargar la tarea.",
  "ext.project.error.read_projects": "No se pudo leer la lista de proyectos.",
  "ext.project.error.save_decision": "No se pudo guardar la decisión.",
  "ext.project.approved_from_view": "Aprobada desde la pantalla Proyectos.",
  "ext.project.notice.approved": "Tarea aprobada.",
  "ext.project.notice.changes_requested": "Cambios pedidos: la tarea vuelve a su responsable.",
  "ext.project.loading.projects": "Cargando proyectos...",
  "ext.project.loading.project": "Cargando el proyecto...",
  "ext.project.empty.projects": "Todavía no hay proyectos. Cuando se cree uno, aparecerá aquí.",
  "ext.project.empty.projects_short": "Todavía no hay proyectos.",
  "ext.project.tabs.aria_label": "Secciones del proyecto",
  "ext.project.open": "Abrir proyecto",
  "ext.project.progress.title": "Avance",
  "ext.project.progress.aria_label": "Avance del proyecto",
  "ext.project.progress.none": "Sin tareas que contar todavía",
  "ext.project.progress.summary": "{done} de {total} tareas terminadas ({percent} %)",
  "ext.project.status.none": "Sin etiqueta",
  "ext.project.status.no_state": "sin estado",
  "ext.project.status_counts": "Tareas por estado",
  "ext.project.requires.accepted": "aceptada",
  "ext.project.requires.submitted": "entregada",
  "ext.project.blocker.line": "Espera a {task} ({state}); se exige {requires}.",
  "ext.project.attention.title": "Qué tienes que hacer",
  "ext.project.attention.none": "Nada pendiente de ti por ahora.",
  "ext.project.attention.reviews": "Revisar lo entregado ({count})",
  "ext.project.attention.verifications": "Verificar etapas ({count})",
  "ext.project.attention.verify_note": "Todas sus tareas están terminadas. Falta que la verifiques; esta pantalla todavía no cierra etapas.",
  "ext.project.attention.mine": "Tus tareas ({count})",
  "ext.project.attention.blocked": "Bloqueos ({count})",
  "ext.project.working.title": "Quién trabaja ahora",
  "ext.project.working.activity": "Actividad · {owner}",
  "ext.project.working.none": "Nadie está trabajando en una tarea ahora mismo.",
  "ext.project.milestones.title": "Etapas e hitos",
  "ext.project.milestones.dates": "Previsto: {planned} · Cierre real: {actual}",
  "ext.project.milestones.not_closed": "todavía no cerrada",
  "ext.project.stages.title": "Etapas y tareas",
  "ext.project.stages.none": "Este proyecto todavía no tiene etapas.",
  "ext.project.stage.aria_label": "Etapa {code}: {title}",
  "ext.project.stage.progress": "{done} de {total} tareas terminadas",
  "ext.project.stage.closed": "Cerrada el {date}",
  "ext.project.stage.filtered": "Con el filtro: {shown} de {total} tareas.",
  "ext.project.phase.label": "Fase: {phase}",
  "ext.project.phase.count": "({shown} de {total})",
  "ext.project.phase.none": "Sin fase",
  "ext.project.phase.empty": "Esta fase no tiene tareas.",
  "ext.project.filter.search": "Buscar",
  "ext.project.filter.state": "Estado",
  "ext.project.filter.all": "Todos",
  "ext.project.filter.owner": "Responsable",
  "ext.project.filter.due_from": "Vence desde",
  "ext.project.filter.due_until": "Vence hasta",
  "ext.project.filter.no_match": "Ninguna tarea coincide con el filtro.",
  "ext.project.list.title": "Lista de tareas",
  "ext.project.list.count": "{shown} de {total} tareas.",
  "ext.project.list.stage": "Etapa {code}",
  "ext.project.list.no_match": "Ninguna tarea coincide con los filtros.",
  "ext.project.tasks.none": "Este proyecto todavía no tiene tareas.",
  "ext.project.deliverables.title": "Entregables",
  "ext.project.deliverables.attempt": "Intento {attempt}",
  "ext.project.deliverables.attempt_label": "Intento",
  "ext.project.deliverables.no_summary": "El resultado no trae resumen.",
  "ext.project.deliverables.files": "Archivos",
  "ext.project.deliverables.decisions": "Decisiones",
  "ext.project.deliverables.not_tested": "Sin probar",
  "ext.project.deliverables.contract_changes": "Cambios de contrato",
  "ext.project.deliverables.requests": "Peticiones",
  "ext.project.deliverables.none": "Esta tarea todavía no tiene un resultado entregado.",
  "ext.project.approve": "Aprobar",
  "ext.project.request_changes": "Pedir cambios",
  "ext.project.feedback.label": "Observaciones para quien la hizo",
  "ext.project.feedback.send": "Enviar observaciones",
  "ext.project.feedback.cancel": "Cancelar",
  "ext.project.task_detail.title": "Detalle de la tarea",
  "ext.project.task_detail.loading": "Cargando la tarea...",
  "ext.project.task_detail.id": "Identificador",
  "ext.project.task_detail.stage": "Etapa",
  "ext.project.task_detail.phase": "Fase",
  "ext.project.task_detail.due_date": "Fecha límite",
  "ext.project.dependencies.title": "Depende de",
  "ext.project.dependencies.state": "Se exige {requires} · {state}",
  "ext.project.dependencies.met": "Cumplida",
  "ext.project.dependencies.not_met": "Todavía no se cumple",
  "ext.project.dependencies.none": "No depende de ninguna otra tarea.",
  "ext.project.dependencies.unblocks": "Desbloquea",
  "ext.project.owner.unassigned": "Sin asignar",
  "ext.project.owner.suggested": "{name} (sugerido)",
  "ext.project.date.none": "Sin fecha",
  "ext.project.date.short": "{day} {month} {year}",
  "ext.project.date.no_plan": "Sin fechas previstas",
  "ext.project.date.planned": "Previsto: {start} a {end}",
  "ext.project.month.jan": "ene",
  "ext.project.month.feb": "feb",
  "ext.project.month.mar": "mar",
  "ext.project.month.apr": "abr",
  "ext.project.month.may": "may",
  "ext.project.month.jun": "jun",
  "ext.project.month.jul": "jul",
  "ext.project.month.aug": "ago",
  "ext.project.month.sep": "sep",
  "ext.project.month.oct": "oct",
  "ext.project.month.nov": "nov",
  "ext.project.month.dec": "dic",
  "ext.project.needs.reviews": "{count} por revisar",
  "ext.project.needs.verifications": "{count} por verificar",
  "ext.project.needs.mine": "{count} tuyas",
  "ext.project.needs.blocked": "{count} bloqueadas",
  "ext.project.needs.none": "Nada pendiente de ti",
  "help.project.view": {
    "title": "Proyectos",
    "hint": "Sigue el avance de un proyecto por etapas, fases y tareas.",
    "more": [
      "Todo lo que ves viene del núcleo del framework y no se inventa ningún dato.",
      "Desde aquí puedes aprobar una tarea entregada o pedir cambios; lo demás solo se lee."
    ]
  },
  "help.project.select": {
    "title": "Proyecto",
    "hint": "Elige qué proyecto activo quieres ver.",
    "more": [
      "La lista trae los proyectos que no están archivados.",
      "Al cambiar de proyecto se cierra el detalle de la tarea que tenías abierta."
    ]
  },
  "help.project.refresh": {
    "title": "Actualizar",
    "hint": "Vuelve a pedir los datos del proyecto al servidor.",
    "more": [
      "La pantalla se actualiza sola mientras otras personas o agentes trabajan; Actualizar lo pide ya.",
      "Usa este botón para ver los cambios más recientes."
    ]
  },
  "help.project.tabs": {
    "title": "Secciones",
    "hint": "Cambia entre el resumen, las etapas con sus tareas y la lista plana.",
    "more": [
      "«Resumen» muestra el avance y qué debes hacer tú.",
      "«Etapas y tareas» muestra cada etapa con sus fases, filtros y el detalle de una tarea.",
      "«Lista plana» muestra todas las tareas en una lista plana, con filtros por responsable, estado y fecha."
    ]
  },
  "help.project.progress": {
    "title": "Avance",
    "hint": "Tareas terminadas sobre las que cuentan, y cuántas hay en cada estado.",
    "more": [
      "Las tareas canceladas no cuentan en el avance.",
      "Una tarea cuenta como terminada cuando fue aceptada, no cuando se entrega."
    ]
  },
  "help.project.attention": {
    "title": "Qué tienes que hacer",
    "hint": "Lo que espera una decisión o una acción tuya.",
    "more": [
      "Reúne las tareas entregadas por revisar, las etapas por verificar y tus propias tareas.",
      "En cada bloqueo ves de qué tarea depende y en qué estado está, para saber dónde empujar."
    ]
  },
  "help.project.working_now": {
    "title": "Quién trabaja ahora",
    "hint": "Tareas y actividades en curso, con su responsable.",
    "more": [
      "Incluye lo que alguien tomó, está haciendo, tiene en pausa o se comprueba de forma automática.",
      "Si no hay responsable, la tarea aún no la tomó nadie.",
      "Las actividades se muestran aquí, pero no cuentan para el avance ni para los conteos por estado."
    ]
  },
  "help.project.milestones": {
    "title": "Etapas e hitos",
    "hint": "Fecha prevista y cierre real de cada etapa.",
    "more": [
      "La fecha prevista es la que se escribió en el plan; puede faltar.",
      "El cierre real se anota cuando la etapa se verifica y se cierra."
    ]
  },
  "help.project.stages_view": {
    "title": "Etapas y tareas",
    "hint": "Cada etapa con sus fases plegables y sus tareas.",
    "more": [
      "Abre una tarea para ver sus dependencias y lo que entregó.",
      "Las tareas sin fase aparecen aparte, en «Sin fase»."
    ]
  },
  "help.project.filter_text": {
    "title": "Buscar",
    "hint": "Filtra por texto en el título, el identificador o la fase.",
    "more": [
      "No distingue mayúsculas de minúsculas.",
      "Solo cambia lo que ves; no modifica ninguna tarea."
    ]
  },
  "help.project.filter_state": {
    "title": "Filtrar por estado",
    "hint": "Muestra solo las tareas con el estado elegido.",
    "more": [
      "Los estados son los mismos que usa el núcleo, como «Espera tu revisión» o «Bloqueada».",
      "Elige «Todos» para quitar el filtro."
    ]
  },
  "help.project.list_view": {
    "title": "Lista de tareas",
    "hint": "Todas las tareas del proyecto en una sola lista plana.",
    "more": [
      "Cada fila dice su etapa, quién la tiene, su fecha límite y las fechas previstas de su etapa.",
      "Lo que no tiene fecha dice «Sin fecha»; nunca se inventa una."
    ]
  },
  "help.project.list_owner": {
    "title": "Filtrar por responsable",
    "hint": "Muestra solo las tareas de la persona o el agente elegido.",
    "more": [
      "Cuenta quien tomó la tarea o, si nadie la tomó, quien está sugerido.",
      "Elige «Todos» para quitar el filtro."
    ]
  },
  "help.project.list_state": {
    "title": "Filtrar por estado",
    "hint": "Muestra solo las tareas con el estado elegido.",
    "more": [
      "Los estados son los mismos que usa el núcleo y que enseña Seguimiento.",
      "Elige «Todos» para quitar el filtro."
    ]
  },
  "help.project.list_dates": {
    "title": "Filtrar por fecha límite",
    "hint": "Muestra solo las tareas cuya fecha límite cae entre las dos fechas.",
    "more": [
      "Las dos fechas son opcionales y cuentan los días indicados.",
      "Con una fecha elegida, las tareas sin fecha límite no aparecen."
    ]
  },
  "help.project.phase_toggle": {
    "title": "Plegar fase",
    "hint": "Muestra u oculta las tareas de esta fase.",
    "more": [
      "Plegar una fase no cambia nada en el proyecto.",
      "El número entre paréntesis cuenta las tareas que cumplen el filtro y las que hay en total."
    ]
  },
  "help.project.task_detail": {
    "title": "Detalle de la tarea",
    "hint": "Responsable, fecha, dependencias y lo que entregó.",
    "more": [
      "Las dependencias dicen qué debe cumplirse antes y si ya se cumplió.",
      "Los entregables son los del último resultado que su responsable envió."
    ]
  },
  "help.project.deliverables": {
    "title": "Entregables",
    "hint": "El último resultado enviado por quien hizo la tarea.",
    "more": [
      "Incluye el resumen, los archivos, las decisiones y lo que no se probó.",
      "Léelo antes de aprobar o de pedir cambios."
    ]
  },
  "help.project.approve": {
    "title": "Aprobar",
    "hint": "Acepta el resultado y da la tarea por terminada.",
    "more": [
      "La tarea pasa a aceptada y sus dependientes pueden avanzar.",
      "Si alguien cambió la tarea mientras la veías, el servidor lo rechaza y debes revisarla de nuevo."
    ]
  },
  "help.project.request_changes": {
    "title": "Pedir cambios",
    "hint": "Devuelve la tarea a su responsable con tus observaciones.",
    "more": [
      "La tarea pasa a «Cambios pedidos» y su responsable debe volver a trabajarla.",
      "Es obligatorio explicar qué debe cambiar."
    ]
  },
  "help.project.feedback_input": {
    "title": "Observaciones",
    "hint": "Escribe qué debe corregirse o completarse.",
    "more": [
      "El texto se guarda como causa del cambio de estado.",
      "Admite hasta 500 caracteres."
    ]
  },
  "help.project.send_feedback": {
    "title": "Enviar observaciones",
    "hint": "Pide los cambios con el texto que escribiste.",
    "more": [
      "Se registra tu decisión y la tarea vuelve a su responsable.",
      "Sin texto no se envía."
    ]
  },
  "help.project.cancel_feedback": {
    "title": "Cancelar",
    "hint": "Cierra el formulario sin pedir cambios.",
    "more": [
      "La tarea sigue esperando tu revisión.",
      "Lo que escribiste se descarta."
    ]
  },
  "help.project.slot": {
    "title": "Proyecto",
    "hint": "Avance del proyecto y lo que espera de ti.",
    "more": [
      "Muestra el proyecto abierto en la pantalla Proyectos, o el primero que necesita algo de ti.",
      "Los datos se leen al abrir el panel y se actualizan cuando decides algo en la pantalla Proyectos."
    ]
  },
  "help.project.open": {
    "title": "Abrir proyecto",
    "hint": "Va a la pantalla Proyectos con este proyecto.",
    "more": [
      "Allí ves las etapas, las tareas y sus entregables.",
      "El enlace no cambia nada en el proyecto."
    ]
  }
};

export default { en, es };
registerTranslations("en", en);
registerTranslations("es", es);

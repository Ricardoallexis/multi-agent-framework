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
  "ext.project.verb.review": "Review",
  "ext.project.verb.verify": "Verify",
  "ext.project.verb.mine": "Reply",
  "ext.project.verb.blocked": "Unblock",
  "ext.project.attention.stage_hidden": "Its stage is hidden.",
  "ext.project.figures.aria_label": "Project figures",
  "ext.project.figures.stages": "Stages",
  "ext.project.figures.tasks": "Tasks done",
  "ext.project.figures.of": "{done} of {total}",
  "ext.project.figures.percent": "{percent}%",
  "ext.project.figures.weighted": "Weighted progress",
  "ext.project.figures.review": "In review",
  "ext.project.figures.blocked": "Blocked",
  "ext.project.figures.left": "What is left",
  "ext.project.figures.left_steps": "in at least {count} steps",
  "ext.project.figures.no_data": "No data",
  "ext.project.figures.tokens": "Tokens",
  "ext.project.figures.cost": "API cost",
  "ext.project.cost.note": "Tokens and cost add up the calls the framework recorded for this project. The cost is API money only and never includes the quota of a plan.",
  "ext.project.cost.tokens_n": "{count} tokens",
  "ext.project.cost.no_price": "No known price",
  "ext.project.cost.partial": "Partial: some calls have no known price",
  "ext.project.cost.tokens_partial": "Partial: some calls report no tokens",
  "ext.project.table.cost": "Tokens and cost",
  "ext.project.timeline.title": "Timeline",
  "ext.project.timeline.aria_label": "Stage timeline",
  "ext.project.timeline.range": "From {from} to {to}",
  "ext.project.timeline.no_forecast": "These are the planned dates and the real closings. It is not a forecast and promises no end date.",
  "ext.project.timeline.real_only": "No stage has planned dates, so only the real closings are shown.",
  "ext.project.timeline.planned": "Planned from {start} to {end}",
  "ext.project.timeline.planned_end": "Planned end {end}",
  "ext.project.timeline.planned_start": "Planned start {start}",
  "ext.project.timeline.no_plan": "No planned dates",
  "ext.project.timeline.closed": "Closed {date}",
  "ext.project.timeline.not_closed": "Not closed yet",
  "ext.project.timeline.late_one": "Closed 1 day after its planned end",
  "ext.project.timeline.late_other": "Closed {count} days after its planned end",
  "ext.project.timeline.ontime": "Closed on or before its planned end",
  "ext.project.segments.tasks": "{done} of {total} tasks done",
  "ext.project.segments.stages": "{done} of {total} stages closed",
  "ext.project.segments.by_stage_note": "More than {limit} tasks: the bar has one piece per stage.",
  "ext.project.table.title": "Stages at a glance",
  "ext.project.table.stage": "Stage",
  "ext.project.table.state": "State",
  "ext.project.table.bar": "Progress",
  "ext.project.table.done": "Done",
  "ext.project.table.weighted": "Weighted",
  "ext.project.table.plan": "Plan",
  "ext.project.table.changed": "Last change",
  "ext.project.table.actions": "Actions",
  "ext.project.table.n_of_m": "{done} of {total}",
  "ext.project.stages.hide": "Hide",
  "ext.project.stages.hide_stage": "Hide stage {code}: {title}",
  "ext.project.stages.hidden_count": "Hidden stages ({count})",
  "ext.project.stages.show": "Show",
  "ext.project.stages.hide_done": "Hide finished stages",
  "ext.project.stages.all_hidden": "All stages are hidden. Use Show to see them again.",
  "ext.project.draft.new": "New project",
  "ext.project.draft.title": "New project",
  "ext.project.draft.back": "Back to projects",
  "ext.project.draft.intro": "Say what you want to achieve. A model drafts the project in stages. Nothing is created until you confirm.",
  "ext.project.draft.text_label": "What do you want to achieve?",
  "ext.project.draft.model_note": "The text goes to the model chosen for new conversations. If it is a remote model, you are asked before anything is sent. The project will be private.",
  "ext.project.draft.send": "Draft the project",
  "ext.project.draft.drafting": "Drafting...",
  "ext.project.draft.cancel": "Cancel",
  "ext.project.draft.cloud_title": "Send the text to a remote model",
  "ext.project.draft.cloud_text": "Your text will be sent to {model}, a remote model.",
  "ext.project.draft.private_note": "The project will be created as private.",
  "ext.project.draft.cloud_local": "A local model keeps the text on this computer: {models}. You can choose one in Apps and agents.",
  "ext.project.draft.cloud_send": "Send to {model}",
  "ext.project.draft.apps_link": "Apps and agents",
  "ext.project.draft.approvals_link": "Approvals",
  "ext.project.draft.reason.disabled": "The model for new conversations is turned off, so no project can be drafted.",
  "ext.project.draft.reason.secret_review_required": "The text looks like it contains a secret. Approve it in Approvals, then draft again.",
  "ext.project.draft.reason.budget_cut": "The usage budget is reached, so the model was not called.",
  "ext.project.draft.reason.refused": "The egress policy did not let the text out, so the model was not called.",
  "ext.project.draft.reason.limit_reached": "The model reached its usage limit and nothing was drafted. Choose another model or try later.",
  "ext.project.draft.reason.invalid_output": "The model's answer did not have the shape of a project. Try again or say it in other words.",
  "ext.project.draft.reason.model_error": "The model could not be reached.",
  "ext.project.draft.reason.default": "The project could not be drafted.",
  "ext.project.draft.card_title": "Proposed project",
  "ext.project.draft.card_counts": "{stages} stages, {tasks} tasks",
  "ext.project.draft.card_model": "Drafted with {model} ({where})",
  "ext.project.draft.where_local": "on this computer",
  "ext.project.draft.where_remote": "remote model",
  "ext.project.draft.stage_number": "Stage {number}",
  "ext.project.draft.problems": "What could not be verified",
  "ext.project.draft.problem.project_exists": "A project with that name already exists. Ask for a different name.",
  "ext.project.draft.problem.default": "The draft could not be verified.",
  "ext.project.draft.no_executor": "No app is turned on to do the tasks yet. You can still create the project.",
  "ext.project.draft.not_created": "Nothing is created until you press Create project.",
  "ext.project.draft.create": "Create project",
  "ext.project.draft.change": "Change something",
  "ext.project.draft.change_label": "What do you want to change?",
  "ext.project.draft.redraft": "Draft again",
  "ext.project.draft.error.plan_changed": "The draft changed after you saw it. Nothing was created. Draft it again.",
  "ext.project.draft.error.create": "Could not create the project.",
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
  "ext.project.verb.review": "Revisar",
  "ext.project.verb.verify": "Verificar",
  "ext.project.verb.mine": "Responder",
  "ext.project.verb.blocked": "Desbloquear",
  "ext.project.attention.stage_hidden": "Su etapa está oculta.",
  "ext.project.figures.aria_label": "Cifras del proyecto",
  "ext.project.figures.stages": "Etapas",
  "ext.project.figures.tasks": "Tareas hechas",
  "ext.project.figures.of": "{done} de {total}",
  "ext.project.figures.percent": "{percent} %",
  "ext.project.figures.weighted": "Avance ponderado",
  "ext.project.figures.review": "En revisión",
  "ext.project.figures.blocked": "Bloqueadas",
  "ext.project.figures.left": "Lo que falta",
  "ext.project.figures.left_steps": "en al menos {count} pasos",
  "ext.project.figures.no_data": "Sin dato",
  "ext.project.figures.tokens": "Tokens",
  "ext.project.figures.cost": "Costo de API",
  "ext.project.cost.note": "Los tokens y el costo suman las llamadas que el marco registró para este proyecto. El costo es solo dinero de API y nunca incluye la cuota de un plan.",
  "ext.project.cost.tokens_n": "{count} tokens",
  "ext.project.cost.no_price": "Sin precio conocido",
  "ext.project.cost.partial": "Parcial: algunas llamadas no tienen precio conocido",
  "ext.project.cost.tokens_partial": "Parcial: algunas llamadas no informan tokens",
  "ext.project.table.cost": "Tokens y costo",
  "ext.project.timeline.title": "Línea de tiempo",
  "ext.project.timeline.aria_label": "Línea de tiempo de las etapas",
  "ext.project.timeline.range": "Del {from} al {to}",
  "ext.project.timeline.no_forecast": "Son las fechas previstas y los cierres reales. No es un pronóstico ni promete una fecha de fin.",
  "ext.project.timeline.real_only": "Ninguna etapa tiene fechas previstas, así que solo se muestran los cierres reales.",
  "ext.project.timeline.planned": "Previsto del {start} al {end}",
  "ext.project.timeline.planned_end": "Fin previsto {end}",
  "ext.project.timeline.planned_start": "Inicio previsto {start}",
  "ext.project.timeline.no_plan": "Sin fechas previstas",
  "ext.project.timeline.closed": "Cerrada el {date}",
  "ext.project.timeline.not_closed": "Todavía no cerrada",
  "ext.project.timeline.late_one": "Cerrada 1 día después de su fin previsto",
  "ext.project.timeline.late_other": "Cerrada {count} días después de su fin previsto",
  "ext.project.timeline.ontime": "Cerrada en su fin previsto o antes",
  "ext.project.segments.tasks": "{done} de {total} tareas hechas",
  "ext.project.segments.stages": "{done} de {total} etapas cerradas",
  "ext.project.segments.by_stage_note": "Más de {limit} tareas: la barra tiene un pedazo por etapa.",
  "ext.project.table.title": "Etapas de un vistazo",
  "ext.project.table.stage": "Etapa",
  "ext.project.table.state": "Estado",
  "ext.project.table.bar": "Avance",
  "ext.project.table.done": "Hechas",
  "ext.project.table.weighted": "Ponderado",
  "ext.project.table.plan": "Plan",
  "ext.project.table.changed": "Último cambio",
  "ext.project.table.actions": "Acciones",
  "ext.project.table.n_of_m": "{done} de {total}",
  "ext.project.stages.hide": "Ocultar",
  "ext.project.stages.hide_stage": "Ocultar la etapa {code}: {title}",
  "ext.project.stages.hidden_count": "Etapas ocultas ({count})",
  "ext.project.stages.show": "Mostrar",
  "ext.project.stages.hide_done": "Ocultar las terminadas",
  "ext.project.stages.all_hidden": "Todas las etapas están ocultas. Pulsa Mostrar para verlas otra vez.",
  "ext.project.draft.new": "Nuevo proyecto",
  "ext.project.draft.title": "Nuevo proyecto",
  "ext.project.draft.back": "Volver a los proyectos",
  "ext.project.draft.intro": "Cuenta qué quieres lograr. Un modelo redacta el proyecto por etapas. No se crea nada hasta que lo confirmes.",
  "ext.project.draft.text_label": "¿Qué quieres lograr?",
  "ext.project.draft.model_note": "El texto va al modelo elegido para las conversaciones nuevas. Si es un modelo remoto, se te pregunta antes de enviar nada. El proyecto será privado.",
  "ext.project.draft.send": "Redactar el proyecto",
  "ext.project.draft.drafting": "Redactando...",
  "ext.project.draft.cancel": "Cancelar",
  "ext.project.draft.cloud_title": "Enviar el texto a un modelo remoto",
  "ext.project.draft.cloud_text": "Tu texto se enviará a {model}, un modelo remoto.",
  "ext.project.draft.private_note": "El proyecto se creará como privado.",
  "ext.project.draft.cloud_local": "Un modelo local deja el texto en este equipo: {models}. Puedes elegir uno en «Apps y agentes».",
  "ext.project.draft.cloud_send": "Enviar a {model}",
  "ext.project.draft.apps_link": "Apps y agentes",
  "ext.project.draft.approvals_link": "Aprobaciones",
  "ext.project.draft.reason.disabled": "El modelo de las conversaciones nuevas está apagado, así que no se puede redactar un proyecto.",
  "ext.project.draft.reason.secret_review_required": "El texto parece llevar un secreto. Apruébalo en «Aprobaciones» y vuelve a redactar.",
  "ext.project.draft.reason.budget_cut": "Se alcanzó el presupuesto de uso, así que no se llamó al modelo.",
  "ext.project.draft.reason.refused": "La política de salida no dejó salir el texto, así que no se llamó al modelo.",
  "ext.project.draft.reason.limit_reached": "El modelo llegó a su límite de uso y no se redactó nada. Elige otro modelo o prueba más tarde.",
  "ext.project.draft.reason.invalid_output": "La respuesta del modelo no tenía forma de proyecto. Inténtalo otra vez o dilo con otras palabras.",
  "ext.project.draft.reason.model_error": "No se pudo llegar al modelo.",
  "ext.project.draft.reason.default": "No se pudo redactar el proyecto.",
  "ext.project.draft.card_title": "Proyecto propuesto",
  "ext.project.draft.card_counts": "{stages} etapas, {tasks} tareas",
  "ext.project.draft.card_model": "Redactado con {model} ({where})",
  "ext.project.draft.where_local": "en este equipo",
  "ext.project.draft.where_remote": "modelo remoto",
  "ext.project.draft.stage_number": "Etapa {number}",
  "ext.project.draft.problems": "Lo que no se pudo verificar",
  "ext.project.draft.problem.project_exists": "Ya existe un proyecto con ese nombre. Pide otro nombre.",
  "ext.project.draft.problem.default": "No se pudo verificar el borrador.",
  "ext.project.draft.no_executor": "Todavía no hay ninguna app activada para hacer las tareas. Aun así puedes crear el proyecto.",
  "ext.project.draft.not_created": "No se crea nada hasta que pulses «Crear proyecto».",
  "ext.project.draft.create": "Crear proyecto",
  "ext.project.draft.change": "Cambiar algo",
  "ext.project.draft.change_label": "¿Qué quieres cambiar?",
  "ext.project.draft.redraft": "Redactar otra vez",
  "ext.project.draft.error.plan_changed": "El borrador cambió después de que lo vieras. No se creó nada. Redáctalo otra vez.",
  "ext.project.draft.error.create": "No se pudo crear el proyecto.",
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
  },
  "help.project.stage_table": {
    "title": "Etapas de un vistazo",
    "hint": "Cada etapa con su estado, una barra pequeña y cuántas tareas lleva hechas.",
    "more": [
      "La barra tiene un pedazo por tarea de la etapa, y todos miden lo mismo.",
      "El modo avanzado añade el avance ponderado, el plan y la fecha del último cambio."
    ]
  },
  "help.project.stage_hide": {
    "title": "Ocultar etapa",
    "hint": "Quita esta etapa de la pantalla. No se borra nada.",
    "more": [
      "La etapa conserva su estado y el avance del proyecto la sigue contando.",
      "Lo que espera de ti dentro de ella se sigue viendo en «Qué tienes que hacer».",
      "La elección se recuerda solo en este navegador."
    ]
  },
  "help.project.stages_show": {
    "title": "Mostrar las etapas ocultas",
    "hint": "Devuelve todas las etapas que ocultaste.",
    "more": [
      "El número es cuántas etapas están ocultas en este proyecto.",
      "Las etapas ocultas se recuerdan en este navegador, no en el servidor."
    ]
  },
  "help.project.stages_hide_done": {
    "title": "Ocultar las terminadas",
    "hint": "Oculta todas las etapas que ya están cerradas.",
    "more": [
      "Solo se ocultan las etapas cerradas. Las que siguen en marcha se quedan.",
      "Con «Mostrar» las recuperas."
    ]
  },
  "help.project.timeline": {
    "title": "Línea de tiempo",
    "hint": "Cada etapa con su inicio y fin previstos y su cierre real.",
    "more": [
      "Está plegada hasta que la abres. Si una etapa no tiene fechas previstas, solo se ve lo real.",
      "Una etapa se marca como pasada solo si se cerró después de su fin previsto. No promete una fecha de fin."
    ]
  },
  "help.project.new": {
    "title": "Nuevo proyecto",
    "hint": "Abre un cuadro donde dices qué quieres lograr.",
    "more": [
      "Un modelo redacta el proyecto en etapas y tareas. Lo lees antes de que se cree nada.",
      "No ofrece ningún proyecto ya hecho: el borrador sale solo de lo que escribes."
    ]
  },
  "help.project.draft_back": {
    "title": "Volver a los proyectos",
    "hint": "Cierra el cuadro de nuevo proyecto y vuelve a los proyectos.",
    "more": [
      "Si sales sin pulsar «Crear proyecto», no se crea nada.",
      "El texto que escribiste no se guarda al salir."
    ]
  },
  "help.project.draft_text": {
    "title": "Lo que quieres lograr",
    "hint": "Escribe la meta con tus palabras.",
    "more": [
      "Hasta 2000 caracteres. Di qué quieres, para cuándo y qué importa más.",
      "El texto se envía al modelo elegido para las conversaciones nuevas. Si es remoto, se te pregunta antes."
    ]
  },
  "help.project.draft_send": {
    "title": "Redactar el proyecto",
    "hint": "Pide al modelo un proyecto en etapas y tareas.",
    "more": [
      "Se llama al modelo una vez. Todavía no se crea nada.",
      "Cada borrador pasa por las reglas de proyectos antes de que puedas crearlo."
    ]
  },
  "help.project.draft_cloud": {
    "title": "Modelo remoto",
    "hint": "Decide si el texto sale de este equipo.",
    "more": [
      "«Enviar» usa el modelo remoto que se nombra arriba. «Cancelar» no envía nada.",
      "El proyecto será privado. Un modelo local deja el texto en este equipo."
    ]
  },
  "help.project.draft_change": {
    "title": "Cambiar algo",
    "hint": "Dile al modelo qué cambiar y recibe otro borrador.",
    "more": [
      "Puedes pedirlo cuantas veces quieras. Cada borrador se vuelve a verificar.",
      "Pedir un cambio no crea nada."
    ]
  },
  "help.project.draft_redraft": {
    "title": "Redactar otra vez",
    "hint": "Envía tu corrección junto con el borrador que viste.",
    "more": [
      "El modelo devuelve el borrador entero con tu corrección aplicada.",
      "La tarjeta se reemplaza por el borrador nuevo."
    ]
  },
  "help.project.draft_create": {
    "title": "Crear proyecto",
    "hint": "Crea el proyecto tal como lo muestra la tarjeta.",
    "more": [
      "Es el único botón que crea algo. Está apagado mientras algo no se pudo verificar.",
      "El proyecto se crea como privado y sus etapas esperan a que las autorices."
    ]
  }
};

export default { en, es };
registerTranslations("en", en);
registerTranslations("es", es);

// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Help texts of the project view (PRY-T03): strict JSON, every key under "project." (see js/help-text.js for the rules).

export const HELP = {
  "project.view": {
    "title": "Proyectos",
    "hint": "Sigue el avance de un proyecto por etapas, fases y tareas.",
    "more": [
      "Todo lo que ves viene del núcleo del framework y no se inventa ningún dato.",
      "Desde aquí puedes aprobar una tarea entregada o pedir cambios; lo demás solo se lee."
    ]
  },
  "project.select": {
    "title": "Proyecto",
    "hint": "Elige qué proyecto activo quieres ver.",
    "more": [
      "La lista trae los proyectos que no están archivados.",
      "Al cambiar de proyecto se cierra el detalle de la tarea que tenías abierta."
    ]
  },
  "project.refresh": {
    "title": "Actualizar",
    "hint": "Vuelve a pedir los datos del proyecto al servidor.",
    "more": [
      "La pantalla no se actualiza sola mientras otras personas o agentes trabajan.",
      "Usa este botón para ver los cambios más recientes."
    ]
  },
  "project.tabs": {
    "title": "Secciones",
    "hint": "Cambia entre el resumen y las etapas con sus tareas.",
    "more": [
      "«Resumen» muestra el avance y qué debes hacer tú.",
      "«Etapas y tareas» muestra cada etapa con sus fases, filtros y el detalle de una tarea."
    ]
  },
  "project.progress": {
    "title": "Avance",
    "hint": "Tareas terminadas sobre las que cuentan, y cuántas hay en cada estado.",
    "more": [
      "Las tareas canceladas no cuentan en el avance.",
      "Una tarea cuenta como terminada cuando fue aceptada, no cuando se entrega."
    ]
  },
  "project.attention": {
    "title": "Qué tienes que hacer",
    "hint": "Lo que espera una decisión o una acción tuya.",
    "more": [
      "Reúne las tareas entregadas por revisar, las etapas por verificar y tus propias tareas.",
      "En cada bloqueo ves de qué tarea depende y en qué estado está, para saber dónde empujar."
    ]
  },
  "project.working_now": {
    "title": "Quién trabaja ahora",
    "hint": "Tareas y actividades en curso, con su responsable.",
    "more": [
      "Incluye lo que alguien tomó, está haciendo, tiene en pausa o se comprueba de forma automática.",
      "Si no hay responsable, la tarea aún no la tomó nadie."
    ]
  },
  "project.milestones": {
    "title": "Etapas e hitos",
    "hint": "Fecha prevista y cierre real de cada etapa.",
    "more": [
      "La fecha prevista es la que se escribió en el plan; puede faltar.",
      "El cierre real se anota cuando la etapa se verifica y se cierra."
    ]
  },
  "project.stages_view": {
    "title": "Etapas y tareas",
    "hint": "Cada etapa con sus fases plegables y sus tareas.",
    "more": [
      "Abre una tarea para ver sus dependencias y lo que entregó.",
      "Las tareas sin fase aparecen aparte, en «Sin fase»."
    ]
  },
  "project.filter_text": {
    "title": "Buscar",
    "hint": "Filtra por texto en el título, el identificador o la fase.",
    "more": [
      "No distingue mayúsculas de minúsculas.",
      "Solo cambia lo que ves; no modifica ninguna tarea."
    ]
  },
  "project.filter_state": {
    "title": "Filtrar por estado",
    "hint": "Muestra solo las tareas con el estado elegido.",
    "more": [
      "Los estados son los mismos que usa el núcleo, como «Espera tu revisión» o «Bloqueada».",
      "Elige «Todos» para quitar el filtro."
    ]
  },
  "project.phase_toggle": {
    "title": "Plegar fase",
    "hint": "Muestra u oculta las tareas de esta fase.",
    "more": [
      "Plegar una fase no cambia nada en el proyecto.",
      "El número entre paréntesis cuenta las tareas que cumplen el filtro y las que hay en total."
    ]
  },
  "project.task_detail": {
    "title": "Detalle de la tarea",
    "hint": "Responsable, fecha, dependencias y lo que entregó.",
    "more": [
      "Las dependencias dicen qué debe cumplirse antes y si ya se cumplió.",
      "Los entregables son los del último resultado que su responsable envió."
    ]
  },
  "project.deliverables": {
    "title": "Entregables",
    "hint": "El último resultado enviado por quien hizo la tarea.",
    "more": [
      "Incluye el resumen, los archivos, las decisiones y lo que no se probó.",
      "Léelo antes de aprobar o de pedir cambios."
    ]
  },
  "project.approve": {
    "title": "Aprobar",
    "hint": "Acepta el resultado y da la tarea por terminada.",
    "more": [
      "La tarea pasa a aceptada y sus dependientes pueden avanzar.",
      "Si alguien cambió la tarea mientras la veías, el servidor lo rechaza y debes revisarla de nuevo."
    ]
  },
  "project.request_changes": {
    "title": "Pedir cambios",
    "hint": "Devuelve la tarea a su responsable con tus observaciones.",
    "more": [
      "La tarea pasa a «Cambios pedidos» y su responsable debe volver a trabajarla.",
      "Es obligatorio explicar qué debe cambiar."
    ]
  },
  "project.feedback_input": {
    "title": "Observaciones",
    "hint": "Escribe qué debe corregirse o completarse.",
    "more": [
      "El texto se guarda como causa del cambio de estado.",
      "Admite hasta 500 caracteres."
    ]
  },
  "project.send_feedback": {
    "title": "Enviar observaciones",
    "hint": "Pide los cambios con el texto que escribiste.",
    "more": [
      "Se registra tu decisión y la tarea vuelve a su responsable.",
      "Sin texto no se envía."
    ]
  },
  "project.cancel_feedback": {
    "title": "Cancelar",
    "hint": "Cierra el formulario sin pedir cambios.",
    "more": [
      "La tarea sigue esperando tu revisión.",
      "Lo que escribiste se descarta."
    ]
  },
  "project.slot": {
    "title": "Proyecto",
    "hint": "Avance del proyecto y lo que espera de ti.",
    "more": [
      "Muestra el proyecto abierto en la pantalla Proyectos, o el primero que necesita algo de ti.",
      "Los datos se leen al abrir el panel y se actualizan cuando decides algo en la pantalla Proyectos."
    ]
  },
  "project.open": {
    "title": "Abrir proyecto",
    "hint": "Va a la pantalla Proyectos con este proyecto.",
    "more": [
      "Allí ves las etapas, las tareas y sus entregables.",
      "El enlace no cambia nada en el proyecto."
    ]
  }
};

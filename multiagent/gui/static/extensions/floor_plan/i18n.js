// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Texts of the floor plan extension. Keys start with "ext.floor_plan."; the Spanish of each help topic is "help.<topic>".
import { registerTranslations } from "../../js/i18n.js";

const en = {
  "ext.floor_plan.view": "Floor plan",
  "ext.floor_plan.aria": "Office floor plan",
  "ext.floor_plan.loading": "Reading the office…",
  "ext.floor_plan.picker": "Choose an office to see its floor plan.",
  "ext.floor_plan.picker_empty": "No offices are installed yet.",
  "ext.floor_plan.picker_error": "The list of offices could not be read.",
  "ext.floor_plan.tabs": "Office views",
  "ext.floor_plan.tab.plan": "Floor plan",
  "ext.floor_plan.tab.list": "List",
  "ext.floor_plan.ask": "Ask the office",
  "ext.floor_plan.layers": "Floor plan layers",
  "ext.floor_plan.layer.assignment": "Assignment",
  "ext.floor_plan.layer.handoff": "Handoff",
  "ext.floor_plan.layer.review": "Review",
  "ext.floor_plan.layer.question": "Question",
  "ext.floor_plan.zoom": "Floor plan zoom",
  "ext.floor_plan.zoom_in": "Zoom in",
  "ext.floor_plan.zoom_out": "Zoom out",
  "ext.floor_plan.zoom_fit": "Fit all",
  "ext.floor_plan.svg_label": "{office} floor plan. Areas: {count}.",
  "ext.floor_plan.list_label": "Floor plan as a list",
  "ext.floor_plan.coordinator": "Coordinator",
  "ext.floor_plan.coordinator_plan": "{done} of {tasks} tasks",
  "ext.floor_plan.member": "Position",
  "ext.floor_plan.area": "Area",
  "ext.floor_plan.privacy": "Privacy: {level}",
  "ext.floor_plan.level.normal": "Normal",
  "ext.floor_plan.level.sensitive": "Sensitive",
  "ext.floor_plan.level.local": "Local only",
  "ext.floor_plan.level.none": "No level set",
  "ext.floor_plan.state.review": "Needs your OK",
  "ext.floor_plan.state.changes": "Changes requested",
  "ext.floor_plan.state.failed": "Needs attention",
  "ext.floor_plan.state.blocked": "Blocked",
  "ext.floor_plan.state.doing": "Working",
  "ext.floor_plan.state.ready": "Ready",
  "ext.floor_plan.state.todo": "To do",
  "ext.floor_plan.state.done": "Done",
  "ext.floor_plan.state.cancelled": "Cancelled",
  "ext.floor_plan.state.idle": "Free",
  "ext.floor_plan.state.unknown": "Unknown",
  "ext.floor_plan.node_label": "{name}, {area}: {state}",
  "ext.floor_plan.selected": "{name} in {area}: {state}",
  "ext.floor_plan.selected_coordinator": "Coordinator: {state}",
  "ext.floor_plan.list.coordinator": "Coordinator: {state}.",
  "ext.floor_plan.list.coordinator_plan": "Coordinator: {state}, {progress}.",
  "ext.floor_plan.list.area": "{area}. Privacy: {level}. Status: {state}. {members}",
  "ext.floor_plan.list.positions": "Positions: {list}.",
  "ext.floor_plan.list.no_positions": "No positions.",
  "ext.floor_plan.list.member": "{name}, {state}",
  "ext.floor_plan.list.relation": "{layer}: {from} to {to}.",
  "ext.floor_plan.note.offline": "The office could not be read just now. The plan shows the last data.",
  "ext.floor_plan.note.missing": "This office was not found.",
  "ext.floor_plan.note.unsupported": "This plan cannot read the office data.",
  "ext.floor_plan.note.truncated": "Only recent activity is shown.",
  "ext.floor_plan.note.empty": "This office has no areas yet.",
};

const es = {
  "ext.floor_plan.view": "Plano",
  "ext.floor_plan.aria": "Plano de la oficina",
  "ext.floor_plan.loading": "Leyendo la oficina…",
  "ext.floor_plan.picker": "Elige una oficina para ver su plano.",
  "ext.floor_plan.picker_empty": "Todavía no hay oficinas instaladas.",
  "ext.floor_plan.picker_error": "No se pudo leer la lista de oficinas.",
  "ext.floor_plan.tabs": "Vistas de la oficina",
  "ext.floor_plan.tab.plan": "Plano",
  "ext.floor_plan.tab.list": "Lista",
  "ext.floor_plan.ask": "Preguntar a la oficina",
  "ext.floor_plan.layers": "Capas del plano",
  "ext.floor_plan.layer.assignment": "Encargo",
  "ext.floor_plan.layer.handoff": "Traspaso",
  "ext.floor_plan.layer.review": "Revisión",
  "ext.floor_plan.layer.question": "Pregunta",
  "ext.floor_plan.zoom": "Zoom del plano",
  "ext.floor_plan.zoom_in": "Acercar",
  "ext.floor_plan.zoom_out": "Alejar",
  "ext.floor_plan.zoom_fit": "Ver todo",
  "ext.floor_plan.svg_label": "Plano de {office}. Áreas: {count}.",
  "ext.floor_plan.list_label": "El plano en forma de lista",
  "ext.floor_plan.coordinator": "Coordinador",
  "ext.floor_plan.coordinator_plan": "{done} de {tasks} tareas",
  "ext.floor_plan.member": "Puesto",
  "ext.floor_plan.area": "Área",
  "ext.floor_plan.privacy": "Privacidad: {level}",
  "ext.floor_plan.level.normal": "Normal",
  "ext.floor_plan.level.sensitive": "Sensible",
  "ext.floor_plan.level.local": "Solo en este equipo",
  "ext.floor_plan.level.none": "Sin nivel",
  "ext.floor_plan.state.review": "Necesita tu visto bueno",
  "ext.floor_plan.state.changes": "Piden cambios",
  "ext.floor_plan.state.failed": "Necesita atención",
  "ext.floor_plan.state.blocked": "Bloqueado",
  "ext.floor_plan.state.doing": "Trabajando",
  "ext.floor_plan.state.ready": "Listo para empezar",
  "ext.floor_plan.state.todo": "Por hacer",
  "ext.floor_plan.state.done": "Terminado",
  "ext.floor_plan.state.cancelled": "Cancelado",
  "ext.floor_plan.state.idle": "Libre",
  "ext.floor_plan.state.unknown": "Sin estado",
  "ext.floor_plan.node_label": "{name}, {area}: {state}",
  "ext.floor_plan.selected": "{name} en {area}: {state}",
  "ext.floor_plan.selected_coordinator": "Coordinador: {state}",
  "ext.floor_plan.list.coordinator": "Coordinador: {state}.",
  "ext.floor_plan.list.coordinator_plan": "Coordinador: {state}, {progress}.",
  "ext.floor_plan.list.area": "{area}. Privacidad: {level}. Estado: {state}. {members}",
  "ext.floor_plan.list.positions": "Puestos: {list}.",
  "ext.floor_plan.list.no_positions": "Sin puestos.",
  "ext.floor_plan.list.member": "{name}, {state}",
  "ext.floor_plan.list.relation": "{layer}: de {from} a {to}.",
  "ext.floor_plan.note.offline": "Ahora no se pudo leer la oficina. El plano muestra los últimos datos.",
  "ext.floor_plan.note.missing": "No se encontró esta oficina.",
  "ext.floor_plan.note.unsupported": "Este plano no puede leer los datos de la oficina.",
  "ext.floor_plan.note.truncated": "Solo se muestra la actividad reciente.",
  "ext.floor_plan.note.empty": "Esta oficina todavía no tiene áreas.",
  "help.floor_plan.view": {
    "title": "Plano",
    "hint": "Mira toda la oficina de un vistazo: sus áreas, quién está en cada una y qué hace.",
    "more": [
      "El coordinador está en el centro y cada área es una caja con sus puestos.",
      "El plano lee la oficina cada pocos segundos y solo la muestra; desde aquí no se cambia nada.",
      "La misma información está también en una lista para lectores de pantalla."
    ]
  },
  "help.floor_plan.tabs": {
    "title": "Plano y lista",
    "hint": "Cambia entre el plano y la lista de oficinas.",
    "more": [
      "El plano muestra la oficina como un dibujo.",
      "La lista muestra cada oficina como una tarjeta con sus áreas."
    ]
  },
  "help.floor_plan.ask": {
    "title": "Preguntar a la oficina",
    "hint": "Abre una conversación con esta oficina.",
    "more": [
      "Escribes lo que necesitas y el coordinador lo reparte entre las áreas.",
      "No se envía nada hasta que pulses enviar."
    ]
  },
  "help.floor_plan.zoom": {
    "title": "Zoom",
    "hint": "Acércate, aléjate o vuelve a ver todo.",
    "more": [
      "También puedes mantener Ctrl y girar la rueda del ratón, o pellizcar en una pantalla táctil.",
      "Arrastra el plano para moverlo cuando estés acercado.",
      "Las teclas más, menos y cero acercan, alejan y ajustan el plano. El zoom no se guarda."
    ]
  },
  "help.floor_plan.layers": {
    "title": "Capas",
    "hint": "Muestra u oculta cada tipo de línea entre áreas.",
    "more": [
      "Las líneas de encargo van del coordinador a cada área.",
      "Las de traspaso, revisión y pregunta van entre áreas.",
      "Cada tipo tiene su propio trazo, así no dependes del color."
    ]
  },
  "help.floor_plan.node": {
    "title": "Puestos",
    "hint": "Cada caja es un puesto de un área, con lo que hace ahora.",
    "more": [
      "Elige uno con el ratón o con Enter para leer su estado bajo el plano.",
      "Un borde de color y las palabras dicen si te necesita, falló o terminó."
    ]
  },
};

export default { en, es };
registerTranslations("en", en);
registerTranslations("es", es);

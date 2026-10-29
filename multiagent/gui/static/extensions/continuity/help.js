// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Help texts of the continuity screens (CNT-T06). Strict JSON, like js/help-text.js, with every key under "continuity.".

export const HELP = {
  "continuity.view": {
    "title": "Decisiones de continuidad",
    "hint": "Tareas que esperan porque una aplicación alcanzó su límite de uso.",
    "more": [
      "Cada tarjeta dice quién alcanzó el límite, cuándo se restablece y de dónde sale esa hora.",
      "Nada se hace solo: cada opción pide tu confirmación antes de guardarse.",
      "Cuando llega la hora, la tarea se reanuda sola si no pediste delegarla."
    ]
  },
  "continuity.slot": {
    "title": "Continuidad de esta sesión",
    "hint": "Muestra si la tarea de esta sesión espera por un límite de uso.",
    "more": [
      "Si la sesión no espera nada, aquí no hay nada que decidir.",
      "Si espera, verás la misma tarjeta de decisión que en la pantalla de decisiones."
    ]
  },
  "continuity.usage": {
    "title": "Política de continuidad",
    "hint": "Cómo actúa el framework cuando una aplicación alcanza su límite.",
    "more": [
      "Muestra el modo, cuánto se espera antes de proponer otro agente y cuántos traspasos se permiten.",
      "Los valores son los predeterminados del framework; esta pantalla solo los muestra.",
      "Pasar la tarea a otro agente nunca ocurre durante una integración."
    ]
  },
  "continuity.wait": {
    "title": "Esperar hasta la hora indicada",
    "hint": "Acepta que la tarea espere hasta que se restablezca el límite.",
    "more": [
      "Pide una confirmación antes de guardar tu decisión.",
      "La tarea sigue pausada y se reanuda sola poco después de la hora indicada.",
      "La hora viene de la aplicación o, si no la dio, de una pausa configurada."
    ]
  },
  "continuity.delegate": {
    "title": "Delegar a otro agente",
    "hint": "Pide que otro agente tome la tarea en lugar de esperar.",
    "more": [
      "Pide una confirmación que dice a quién se delega y qué se enviaría.",
      "Se enviaría el sobre de la tarea y el parche guardado, nunca la conversación.",
      "Hoy solo se guarda tu decisión: la tarea no se reanuda sola hasta que se traspase o vuelvas a esperar."
    ]
  },
  "continuity.later": {
    "title": "Decidir después",
    "hint": "Cierra las opciones sin cambiar nada.",
    "more": [
      "La tarea sigue en espera y la decisión queda pendiente.",
      "Puedes volver a abrir las opciones cuando quieras."
    ]
  },
  "continuity.refresh": {
    "title": "Actualizar",
    "hint": "Vuelve a leer las tareas que esperan.",
    "more": [
      "La lista también se actualiza sola cada cierto tiempo.",
      "Actualizar no cambia ninguna tarea."
    ]
  }
};

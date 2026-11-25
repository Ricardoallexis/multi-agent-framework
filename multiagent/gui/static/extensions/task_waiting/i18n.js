// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Texts of the task_waiting extension. Keys start with "ext.task_waiting.".
import { registerTranslations } from "../../js/i18n.js";

const en = {
  "ext.task_waiting.title": "Waiting for your decision",
  "ext.task_waiting.recipient": "Waiting for",
  "ext.task_waiting.since": "Since",
  "ext.task_waiting.reset": "Resets",
  "ext.task_waiting.reference": "Reference",
  "ext.task_waiting.task_state": "Task state",
  "ext.task_waiting.loading": "Reading waits...",
  "ext.task_waiting.error.refresh": "Could not refresh waits.",
  "ext.task_waiting.count.one": "{count} task is waiting for your decision.",
  "ext.task_waiting.count.many": "{count} tasks are waiting for your decision.",
  "ext.task_waiting.refresh": "Refresh",
  "ext.task_waiting.error.stale": "The data shown is from before the latest request.",
  "ext.task_waiting.empty.decisions": "No tasks are waiting for your decision.",
  "ext.task_waiting.others": "Other waits",
  "ext.task_waiting.empty.others": "There are no other waits.",
};

const es = {
  "ext.task_waiting.title": "Esperan tu decisión",
  "ext.task_waiting.recipient": "Espera a",
  "ext.task_waiting.since": "Desde",
  "ext.task_waiting.reset": "Restablecimiento",
  "ext.task_waiting.reference": "Referencia",
  "ext.task_waiting.task_state": "Estado de la tarea",
  "ext.task_waiting.loading": "Leyendo esperas…",
  "ext.task_waiting.error.refresh": "No se pudieron actualizar las esperas.",
  "ext.task_waiting.count.one": "{count} tarea espera tu decisión.",
  "ext.task_waiting.count.many": "{count} tareas esperan tu decisión.",
  "ext.task_waiting.refresh": "Actualizar",
  "ext.task_waiting.error.stale": "Los datos visibles son anteriores al último intento.",
  "ext.task_waiting.empty.decisions": "Ninguna tarea espera tu decisión.",
  "ext.task_waiting.others": "Otras esperas",
  "ext.task_waiting.empty.others": "No hay otras esperas.",
  "help.task_waiting.view": {
    "title": "Esperan tu decisión",
    "hint": "Preguntas de tareas que necesitan tu respuesta; también puedes consultar las demás esperas.",
    "more": [
      "Cada tarjeta muestra la pregunta, a quién espera, desde cuándo y su referencia.",
      "Esta vista solo consulta datos: no aprueba ni resuelve ninguna espera."
    ]
  },
  "help.task_waiting.panel": {
    "title": "Preguntas pendientes",
    "hint": "Tareas de todo el sistema que esperan tu decisión, independientemente de la sesión abierta.",
    "more": [
      "La insignia de espera es independiente del estado de ejecución de la tarea.",
      "Consultar el panel no responde la pregunta ni autoriza una ejecución."
    ]
  },
  "help.task_waiting.refresh": {
    "title": "Actualizar esperas",
    "hint": "Vuelve a consultar las esperas sin cambiar las tareas.",
    "more": [
      "La lista se actualiza periódicamente mientras la pestaña está visible.",
      "Si una consulta falla, los datos anteriores se conservan con un aviso."
    ]
  },
  "help.task_waiting.others": {
    "title": "Otras esperas",
    "hint": "Tareas que esperan a un agente, otra tarea, una cuota o una ventana de integración.",
    "more": [
      "Estas esperas no son preguntas dirigidas al usuario.",
      "La tarjeta conserva el estado de la tarea y muestra el destinatario de la espera."
    ]
  }
};

export default { en, es };
registerTranslations("en", en);
registerTranslations("es", es);

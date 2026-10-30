// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
export const HELP = {
  "task_waiting.view": {
    "title": "Esperan tu decisión",
    "hint": "Preguntas de tareas que necesitan tu respuesta; también puedes consultar las demás esperas.",
    "more": [
      "Cada tarjeta muestra la pregunta, a quién espera, desde cuándo y su referencia.",
      "Esta vista solo consulta datos: no aprueba ni resuelve ninguna espera."
    ]
  },
  "task_waiting.panel": {
    "title": "Preguntas pendientes",
    "hint": "Tareas de todo el sistema que esperan tu decisión, independientemente de la sesión abierta.",
    "more": [
      "La insignia de espera es independiente del estado de ejecución de la tarea.",
      "Consultar el panel no responde la pregunta ni autoriza una ejecución."
    ]
  },
  "task_waiting.refresh": {
    "title": "Actualizar esperas",
    "hint": "Vuelve a consultar las esperas sin cambiar las tareas.",
    "more": [
      "La lista se actualiza periódicamente mientras la pestaña está visible.",
      "Si una consulta falla, los datos anteriores se conservan con un aviso."
    ]
  },
  "task_waiting.others": {
    "title": "Otras esperas",
    "hint": "Tareas que esperan a un agente, otra tarea, una cuota o una ventana de integración.",
    "more": [
      "Estas esperas no son preguntas dirigidas al usuario.",
      "La tarjeta conserva el estado de la tarea y muestra el destinatario de la espera."
    ]
  }
};

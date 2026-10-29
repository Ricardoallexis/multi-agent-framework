// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0

export const HELP = {
  "document_alerts.view": {
    "title": "Alertas de documentos",
    "hint": "Textos de fuentes no confiables puestos en cuarentena o a la espera de revisión.",
    "more": [
      "Cada alerta dice de qué fuente viene el texto, qué fragmento se apartó y qué política se aplicó.",
      "El texto original no se muestra aquí: queda guardado aparte.",
      "Esta pantalla solo informa; una revisión se decide en la propia ejecución."
    ]
  },
  "document_alerts.card": {
    "title": "Alertas de la ejecución",
    "hint": "Alertas de documentos de la ejecución abierta: fuente, fragmento y política.",
    "more": [
      "Aparece una alerta por cada texto que el detector apartó.",
      "Si una revisión espera una decisión, se avisa aquí.",
      "Aprobar o rechazar se hace en la propia ejecución, nunca desde esta sección."
    ]
  },
  "document_alerts.refresh": {
    "title": "Actualizar las alertas",
    "hint": "Vuelve a leer las alertas del servidor local.",
    "more": [
      "La lista también se actualiza sola cada pocos segundos.",
      "Úsalo si acabas de decidir una revisión y quieres ver su estado."
    ]
  },
  "document_alerts.open_run": {
    "title": "Abrir la ejecución",
    "hint": "Abre la ejecución a la que pertenece la alerta.",
    "more": [
      "Allí se ve el estado de la ejecución y, si espera una decisión, los botones para decidir.",
      "Este enlace no aprueba ni rechaza nada."
    ]
  }
};

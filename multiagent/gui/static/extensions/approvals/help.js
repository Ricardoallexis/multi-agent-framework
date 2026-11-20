// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Contextual help for the «Aprobaciones» extension (SEC-T04): strict JSON under "approvals.".

export const HELP = {
  "approvals.view": {
    "title": "Aprobaciones",
    "hint": "Aquí decides envíos con forma de secreto, consultas externas y cargas reservadas.",
    "more": [
      "Se frenan los envíos con claves o tokens a modelos externos o ejecutores, y consultas externas como OSV.dev.",
      "Solo se aprueban aquí con sesión de navegador y un código de un solo uso para evitar que otro proceso apruebe por ti.",
      "Un proceso del mismo usuario podría leer cookies o consola del navegador; abre esta ventana solo tú y rechaza si dudas."
    ]
  },
  "approvals.refresh": {
    "title": "Actualizar aprobaciones",
    "hint": "Vuelve a leer las solicitudes pendientes desde el servidor local.",
    "more": [
      "Esta lectura no aprueba, rechaza ni consume ninguna solicitud.",
      "Úsala para comprobar si apareció una solicitud nueva o si otra ya cambió."
    ]
  },
  "approvals.approve": {
    "title": "Aprobar este envío",
    "hint": "Pide un código de un solo uso y autoriza solo esta solicitud exacta.",
    "more": [
      "El código vale una sola vez durante 120 segundos para el contenido y destino mostrados.",
      "No recuerda la decisión ni autoriza futuros envíos o cambios en el texto."
    ]
  },
  "approvals.reject": {
    "title": "Rechazar solicitud",
    "hint": "Niega esta solicitud para que nada reservado salga ni se cargue.",
    "more": [
      "La solicitud queda cerrada como rechazada y no puede reutilizarse.",
      "También usa la sesión del navegador para dejar constancia en el historial."
    ]
  },
  "approvals.retry_code": {
    "title": "Intentar de nuevo",
    "hint": "Pide un código nuevo de un solo uso cuando el anterior venció.",
    "more": [
      "Cada intento genera un código distinto válido por 120 segundos.",
      "Si el problema sigue tras reiniciar el servidor, recarga desde «multiagent gui»."
    ]
  }
};

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
  "approvals.memory_trust": {
    "title": "Aprobar este apunte",
    "hint": "El apunte pasa a fiable: se tratará como un apunte que tú confirmaste.",
    "more": [
      "Usa un código de un solo uso y vale solo para este apunte, con este texto.",
      "Si no lo escribiste tú, pulsa Rechazar: el apunte se queda como no fiable."
    ]
  },
  "approvals.memory_delete": {
    "title": "Aprobar el borrado",
    "hint": "El apunte se borra y no se puede deshacer.",
    "more": [
      "Usa un código de un solo uso y vale solo para este apunte.",
      "Si no pediste borrarlo, pulsa Rechazar: el apunte se queda como está."
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
  },
  "approvals.osv_allow": {
    "title": "Permitir la consulta",
    "hint": "Autoriza una consulta de paquetes a api.osv.dev para escanear este bundle.",
    "more": [
      "Solo envía nombres y versiones de los paquetes declarados en los manifiestos indicados.",
      "Vale una sola vez para esta huella mediante código de un solo uso."
    ]
  },
  "approvals.osv_deny": {
    "title": "No permitir",
    "hint": "Niega la consulta a api.osv.dev; el bundle quedará sin analizar.",
    "more": [
      "No se ejecutará SkillSpector ni saldrán datos de paquetes hacia internet.",
      "Al cargar el bundle se pedirá confirmación manual porque quedó sin analizar."
    ]
  },
  "approvals.bundle_allow": {
    "title": "Cargar este bundle",
    "hint": "Autoriza cargar esta versión exacta del bundle tras revisar el veredicto.",
    "more": [
      "Solo vale para esta huella; cualquier cambio en los archivos volverá a pedir revisión.",
      "Usa el código de un solo uso de la sesión abierta en esta pestaña."
    ]
  },
  "approvals.bundle_risk": {
    "title": "Asumo los riesgos",
    "hint": "Confirma que revisaste el resultado antes de cargar este bundle.",
    "more": [
      "SkillSpector puede recomendar no instalarlo o no haberlo analizado por completo.",
      "El servidor exige esta confirmación para cada carga que requiere tu aprobación."
    ]
  },
  "approvals.bundle_deny": {
    "title": "No cargar",
    "hint": "Mantiene bloqueada la carga de este bundle para su huella actual.",
    "more": [
      "Los contratos del bundle no se importarán ni se ejecutarán.",
      "La decisión queda registrada en el historial de aprobaciones."
    ]
  }
};

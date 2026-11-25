// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Contextual help for the Offices view (VOF-T01).

export const HELP = {
  "offices.view": {
    "title": "Oficinas",
    "hint": "Reúne tus oficinas, sus áreas y el estado de cada puesto.",
    "more": [
      "El coordinador, los equipos y sus áreas muestran el estado que informa el programa.",
      "Los avisos señalan qué falta y cómo corregirlo; la lista se actualiza cada cuatro segundos.",
      "Los permisos del equipo son declaraciones, no garantías de lo que puede hacer."
    ]
  },
  "offices.plan": {
    "title": "Plan de la oficina",
    "hint": "Abre el plan que el coordinador repartió entre las áreas.",
    "more": [
      "Ahí ves cada tarea, el área que la hace y su estado.",
      "Desde esta vista solo lo abres; no cambia nada."
    ]
  },
  "offices.member_state": {
    "title": "Estado del puesto",
    "hint": "El estado indica qué hace ahora cada agente o persona.",
    "more": [
      "«Libre» significa que no hay un trabajo activo para ese puesto.",
      "El estado cambia cuando llegan datos nuevos."
    ]
  },
  "offices.degraded": {
    "title": "Oficina que funciona a medias",
    "hint": "El aviso explica qué falta y muestra una forma de corregirlo.",
    "more": [
      "Un equipo puede necesitar aprobación o un área puede necesitar un agente.",
      "Si cambió una oficina, vuelve a importarla después de revisar el aviso."
    ]
  },
  "offices.declared": {
    "title": "Permisos declarados",
    "hint": "El equipo declara permisos, pero verlos no significa que se hagan valer.",
    "more": [
      "El programa no garantiza esos permisos durante el trabajo.",
      "Consulta la descripción del equipo antes de confiar en sus declaraciones."
    ]
  },
  "offices.levels": {
    "title": "Nivel del área",
    "hint": "El nivel indica cómo se limita el uso de los datos del área.",
    "more": [
      "Normal y Sensible son niveles distintos; Solo local nunca sale del equipo.",
      "Sin nivel indica que el área no declara uno."
    ]
  },
  "offices.import_office": {
    "title": "Importar una oficina",
    "hint": "Elige un archivo .zip para revisar la oficina antes de instalarla.",
    "more": [
      "También puedes reimportar una oficina o añadir un área a una oficina que ya tienes.",
      "En el modo avanzado puedes elegir una carpeta."
    ]
  },
  "offices.card": {
    "title": "Ficha de la oficina",
    "hint": "Revisa las áreas, sus miembros, lo que necesitan y lo que declaran.",
    "more": [
      "Al instalar, también se aprueban los equipos que aparecen en la ficha.",
      "La ficha muestra si la oficina trae código y el resultado de la revisión."
    ]
  },
  "offices.install": {
    "title": "Instalar oficina",
    "hint": "La oficina se instala después de revisar esta ficha.",
    "more": [
      "Si hace falta, primero puedes permitir una consulta de seguridad.",
      "Asume los riesgos solo si revisaste lo que trae la oficina."
    ]
  },
  "offices.export": {
    "title": "Exportar oficina o área",
    "hint": "Guarda una oficina o un área como archivo .zip.",
    "more": [
      "El archivo se prepara en esta computadora y se descarga aquí.",
      "Los avisos indican qué podría no funcionar en otra computadora."
    ]
  },
  "offices.include_code": {
    "title": "Incluir código",
    "hint": "El código propio solo se incluye al exportar desde el modo avanzado.",
    "more": [
      "El archivo puede contener instrucciones que ejecuta un agente activado.",
      "Revisa el código antes de compartir el archivo."
    ]
  },
  "offices.remove": {
    "title": "Quitar oficina o área",
    "hint": "Quitarla solo la saca de la lista de oficinas.",
    "more": [
      "Se conservan sus datos y las copias guardadas.",
      "La confirmación aparece antes de quitarla."
    ]
  },
  "offices.ask": {
    "title": "Pedir a la oficina",
    "hint": "Abre «Nueva sesión» con esta oficina elegida.",
    "more": [
      "«Pídele algo a la oficina» deja que el coordinador prepare un borrador del plan.",
      "«Pedir a esta área» abre sus opciones en Sesión única.",
      "Abrir esta pantalla no guarda ni ejecuta nada."
    ]
  },
  "offices.privacy": {
    "title": "Privacidad de la oficina",
    "hint": "Ajusta los niveles de cada área y el permiso para modelos de la nube.",
    "more": [
      "Subir el nivel o quitar el permiso de nube se aplica al momento.",
      "Bajar un nivel o permitir modelos de la nube requiere un código de un solo uso.",
      "Un área Sensible o Solo local nunca usa la nube, aunque el permiso esté activo."
    ]
  },
  "offices.privacy_note": {
    "title": "Lo que esta opción no libera",
    "hint": "La nota explica qué protecciones siguen vigentes.",
    "more": [
      "El nivel del área y el permiso de nube son ajustes separados.",
      "Esta opción no autoriza destinos ni permite enviar datos a un destino concreto."
    ]
  },
  "offices.lower": {
    "title": "Bajar el nivel",
    "hint": "Este cambio necesita una aprobación de un solo uso.",
    "more": [
      "El nivel no cambia hasta completar la aprobación.",
      "Cancelar rechaza la aprobación y conserva el nivel actual."
    ]
  },
  "offices.level_question": {
    "title": "Pregunta de privacidad",
    "hint": "La primera petición a un área sin nivel pregunta qué opción guardar.",
    "more": [
      "La opción elegida se guarda y la petición se repite una sola vez.",
      "Si vuelve a fallar, verás el error y no se vuelve a preguntar."
    ]
  }
};

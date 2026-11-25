// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Help texts of teams and plans, their chat cards, the work map and the agent tree.
// One part of the help catalog: help-text.js joins every part into HELP, so a view's texts live in one file.
// Strict JSON under the rules of help-text.js: { title, hint, more }, keys as the views pass them to <Help topic=...>.

export const HELP = {
  "work_map.refresh": {
    "title": "Actualizar el mapa",
    "hint": "Vuelve a leer proyectos y sesiones disponibles.",
    "more": ["El mapa usa una lectura protegida del servidor local.", "Los avisos de las fuentes no muestran sus identificadores privados."]
  },
  "work_map.search": {
    "title": "Buscar en el mapa",
    "hint": "Filtra con el mismo texto el árbol y la lista.",
    "more": ["Se conservan los ancestros de cada coincidencia.", "La búsqueda cubre proyectos, tareas, sesiones y agentes."]
  },
  "work_map.active": {
    "title": "Solo activos",
    "hint": "Muestra sesiones y agentes que siguen activos.",
    "more": ["El filtro inicial incluye sesiones activas y sin actividad reciente.", "Puedes quitarlo para incluir elementos terminados."]
  },
  "work_map.fold_finished": {
    "title": "Plegar terminados",
    "hint": "Pliega los hijos de elementos terminados.",
    "more": ["La vista inicia con esta opción activada.", "Usa el botón junto a cada elemento para abrir o cerrar sus hijos."]
  },
  "work_map.tree": {
    "title": "Vista de árbol",
    "hint": "Organiza proyectos, etapas, tareas y sesiones por relación.",
    "more": ["Los botones de cada rama permiten plegar sus hijos.", "Las sesiones muestran los agentes enlazados que permite la fuente."]
  },
  "work_map.list": {
    "title": "Vista de lista",
    "hint": "Presenta los mismos elementos en filas para pantallas estrechas.",
    "more": ["La búsqueda y el filtro activo se comparten con el árbol.", "Cada fila conserva su nivel de relación."]
  },
  "work_map.fold": {
    "title": "Plegar rama",
    "hint": "Oculta o muestra los elementos descendientes.",
    "more": ["El nombre del botón indica la acción y la rama afectada.", "Los elementos terminados pueden iniciar plegados."]
  },
  "work_map.open_session": {
    "title": "Abrir sesión",
    "hint": "Abre el detalle de esta sesión en la vista Sesiones.",
    "more": ["La navegación conserva el identificador de la sesión.", "El contenido se vuelve a leer desde el servidor local."]
  },
  "work_map.assignment_edge": {
    "title": "Encargo de tarea",
    "hint": "Abre la sesión enlazada a esta tarea.",
    "more": ["La relación viene del registro de tareas del proyecto.", "El botón funciona con teclado y conserva el foco visible."]
  },
  "work_map.notice_edge": {
    "title": "Aviso a sesión hija",
    "hint": "Abre la sesión hija enlazada a este agente.",
    "more": ["La relación usa el identificador de sesión permitido por la fuente.", "No se muestra el texto privado de la descripción del hijo."]
  },

  "teams.section": {
    "title": "Equipos",
    "hint": "Consulta los equipos de los bundles, su organigrama y sus versiones.",
    "more": [
      "Cada equipo se define en el archivo team.yaml de su bundle y se propone desde la línea de comandos o la API.",
      "Solo una persona puede aprobar o rechazar una propuesta, y aquí queda registrada a nombre del operador."
    ]
  },
  "teams.refresh": {
    "title": "Actualizar los equipos",
    "hint": "Vuelve a leer los equipos o el equipo abierto.",
    "more": [
      "La pantalla conserva la última respuesta si la lectura falla.",
      "Una propuesta nueva aparece al actualizar."
    ]
  },
  "teams.list": {
    "title": "Lista de equipos",
    "hint": "Muestra cada equipo con su versión aprobada y su propuesta pendiente.",
    "more": [
      "Solo aparecen los bundles que tienen un equipo definido.",
      "Abre un equipo con su nombre para ver su organigrama, sus versiones y decidir sobre la propuesta."
    ]
  },
  "teams.decision": {
    "title": "Propuesta pendiente",
    "hint": "Muestra la última propuesta que espera una decisión y permite aprobarla o rechazarla.",
    "more": [
      "Aprobar la convierte en la versión activa del equipo; rechazarla deja la versión aprobada como estaba.",
      "Si otra persona ya decidió, el servidor lo dice y la pantalla se actualiza."
    ]
  },
  "teams.approve": {
    "title": "Aprobar la propuesta",
    "hint": "Pide confirmación antes de aprobar la propuesta pendiente.",
    "more": [
      "La versión aprobada pasa a ser la activa del equipo.",
      "La decisión queda registrada a nombre del operador, con el canal de la interfaz y la nota que escribas."
    ]
  },
  "teams.reject": {
    "title": "Rechazar la propuesta",
    "hint": "Pide confirmación antes de rechazar la propuesta pendiente.",
    "more": [
      "La propuesta queda rechazada y la versión aprobada actual no cambia.",
      "Una propuesta rechazada no se puede aprobar después: hay que proponer una versión nueva."
    ]
  },
  "teams.note": {
    "title": "Nota de la decisión",
    "hint": "Texto opcional que queda guardado junto a la decisión.",
    "more": [
      "Sirve para explicar por qué se aprueba o se rechaza.",
      "Puede quedar vacía."
    ]
  },
  "teams.chart": {
    "title": "Organigrama",
    "hint": "Muestra los puestos como una lista anidada: cada puesto cuelga de aquel a quien reporta.",
    "more": [
      "Cada puesto dice su propósito, quién lo ocupa y su estado en palabras: Ocupado, Vacante o Pendiente de aprobar.",
      "Pendiente de aprobar marca lo que cambia en la propuesta; Se quitará indica un puesto que la propuesta elimina.",
      "Si el equipo tiene varias raíces, la vista lo avisa."
    ]
  },
  "teams.links": {
    "title": "Relaciones aparte",
    "hint": "Lista las revisiones y delegaciones, que no forman parte de la jerarquía.",
    "more": [
      "Revisa a indica quién revisa el trabajo de otro puesto; delega en, a quién pasa trabajo.",
      "No cambian el árbol: la jerarquía solo sigue las relaciones de reporte."
    ]
  },
  "teams.mermaid": {
    "title": "Texto Mermaid",
    "hint": "Es el mismo organigrama como diagrama en texto, listo para pegar en otra herramienta.",
    "more": [
      "Esta vista no dibuja el diagrama: solo muestra el texto.",
      "Pégalo en un editor que entienda Mermaid para verlo dibujado."
    ]
  },
  "teams.copy": {
    "title": "Copiar el texto Mermaid",
    "hint": "Copia el texto del diagrama al portapapeles.",
    "more": [
      "Si el navegador no deja copiar, la vista lo avisa y puedes seleccionar el texto a mano.",
      "No se envía nada fuera de tu equipo."
    ]
  },
  "teams.versions": {
    "title": "Versiones del equipo",
    "hint": "Lista las versiones guardadas, de la más antigua a la más reciente.",
    "more": [
      "Cada fila dice su estado: Borrador, Propuesta, Aprobada o Rechazada, y quién decidió.",
      "La versión activa es la última aprobada."
    ]
  },
  "teams.diff": {
    "title": "Cambios de una versión",
    "hint": "Muestra qué cambia esa versión respecto de la versión aprobada.",
    "more": [
      "Separa puestos, miembros, asignaciones y relaciones añadidos, quitados o cambiados.",
      "Si no hay versión aprobada, todo cuenta como añadido."
    ]
  },
  "plans.section": {
    "title": "Planes propuestos",
    "hint": "Revisa las propuestas de plan guardadas antes de crear tareas.",
    "more": [
      "Una propuesta reúne las tareas, dependencias y flujos sugeridos para un objetivo.",
      "Solo una persona puede aprobar o rechazar una propuesta, y aquí queda registrada a nombre del operador."
    ]
  },
  "plans.refresh": {
    "title": "Actualizar los planes",
    "hint": "Vuelve a leer la lista de propuestas o la propuesta abierta.",
    "more": [
      "La pantalla conserva la última respuesta si la lectura falla.",
      "Una propuesta guardada o editada por otro canal aparece al actualizar."
    ]
  },
  "plans.filter": {
    "title": "Filtrar por estado",
    "hint": "Muestra todas las propuestas o solo las pendientes, aprobadas o rechazadas.",
    "more": [
      "Propuesta indica que espera una decisión; Aprobada, que sus tareas ya se crearon.",
      "El filtro consulta al servidor local cada vez que cambias la selección."
    ]
  },
  "plans.list": {
    "title": "Lista de propuestas",
    "hint": "Muestra cada propuesta con su grupo, estado, revisión y número de tareas.",
    "more": [
      "Cada cambio guardado en una propuesta pendiente sube su número de revisión.",
      "Abre una propuesta por su enlace para ver sus tareas, historial y acciones."
    ]
  },
  "plans.detail": {
    "title": "Tareas y dependencias",
    "hint": "Lista las tareas de la propuesta, de qué tareas dependen, su flujo y su preferente.",
    "more": [
      "Las dependencias indican qué tareas deben terminar antes de que otra pueda empezar.",
      "El preferente señala el agente o persona sugerida para tomar la tarea, si existe."
    ]
  },
  "plans.history": {
    "title": "Historial de cambios",
    "hint": "Muestra quién guardó, editó, reasignó o decidió cada revisión de la propuesta.",
    "more": [
      "Cada línea anota la fecha, el número de revisión, el actor, el canal y las tareas modificadas.",
      "Las notas opcionales escritas al confirmar quedan guardadas debajo de cada movimiento."
    ]
  },
  "plans.edit": {
    "title": "Editar tareas",
    "hint": "Modifica títulos, objetivos, dependencias, flujos o preferentes antes de aprobar.",
    "more": [
      "Al confirmar se valida la propuesta completa sobre la revisión que estás viendo.",
      "Si alguien cambió la propuesta antes, la pantalla avisa del conflicto y vuelve a cargarla."
    ]
  },
  "plans.reassign": {
    "title": "Reasignar una tarea",
    "hint": "Cambia el preferente, el workflow o el bundle de una sola tarea sin editar las demás.",
    "more": [
      "Deja vacío el bundle si el workflow pertenece a los incluidos en el framework.",
      "Pide confirmación antes de enviar y sube la revisión si no hay conflicto."
    ]
  },
  "plans.approve": {
    "title": "Aprobar el plan",
    "hint": "Pide confirmación antes de aprobar la revisión visible y crear las tareas.",
    "more": [
      "Al aprobar se crean el grupo si falta y todas las tareas, y arrancan las que no tienen dependencias.",
      "Si la revisión cambió o ya existe una tarea con ese identificador, el servidor rechaza el cambio."
    ]
  },
  "plans.reject": {
    "title": "Rechazar el plan",
    "hint": "Pide confirmación antes de rechazar la propuesta sin crear tareas.",
    "more": [
      "La propuesta queda cerrada como rechazada y no admite más ediciones ni decisiones.",
      "La decisión queda anotada a nombre del operador con el canal de la interfaz."
    ]
  },
  "plans.advance": {
    "title": "Avanzar el plan",
    "hint": "Pide confirmación y avanza una vez el plan de esta propuesta aprobada.",
    "more": [
      "Pasa a revisión las tareas cuyo run terminó y arranca las que ya pueden empezar. Si no hay nada que mover, lo avisa.",
      "Queda a nombre del operador con el canal de la interfaz; el plan no avanza solo."
    ]
  },
  "plans.note": {
    "title": "Nota de la acción",
    "hint": "Texto opcional que queda guardado en el historial de la propuesta.",
    "more": [
      "Sirve para explicar por qué editas, reasignas, apruebas o rechazas.",
      "Puedes dejar este campo vacío."
    ]
  },

  "agent_tree.open": {
    "title": "Árbol de agentes",
    "hint": "Abre el árbol de agentes de esta sesión.",
    "more": [
      "Muestra quién trabaja en la sesión y en qué: el orquestador y los agentes que lanzó, agrupados por nivel.",
      "Es una vista de solo lectura; no inicia ni detiene ningún trabajo.",
      "Se actualiza sola mientras la sesión cambia."
    ]
  },
  "agent_tree.view": {
    "title": "Árbol de la sesión",
    "hint": "El orquestador y sus agentes, por nivel, con su estado.",
    "more": [
      "Arriba está el orquestador, que es la sesión que abriste; debajo, los agentes que lanzó, agrupados de T1 a T4.",
      "Cada nodo dice su modelo, su nivel, su estado y en qué trabaja; el nivel y el estado siempre van escritos, nunca solo en color.",
      "El registro de abajo anota los cambios de estado desde que abriste esta pantalla."
    ]
  },
  "agent_tree.back": {
    "title": "Volver a la sesión",
    "hint": "Regresa a la conversación de esta sesión.",
    "more": [
      "El árbol no cambia la sesión ni ejecuta acciones.",
      "Puedes volver a abrirlo desde la cabecera de la sesión."
    ]
  },
  "agent_tree.filter": {
    "title": "Solo activos",
    "hint": "Oculta los agentes que ya terminaron.",
    "more": [
      "Deja los que trabajan, los que llevan un rato sin actividad y los que esperan un límite de uso o a una persona.",
      "Quita los terminados y los detenidos; el orquestador siempre se ve.",
      "Desactívalo para volver a ver todos."
    ]
  },
  "agent_tree.node": {
    "title": "Moverse por el árbol",
    "hint": "Flechas para moverte, Enter para elegir, Esc para soltar.",
    "more": [
      "Con el foco en el árbol, las flechas arriba y abajo pasan de un nodo al siguiente; Inicio y Fin saltan al primero y al último.",
      "Enter o la barra espaciadora elige el nodo y muestra sus datos a un lado; Esc quita la elección.",
      "También puedes elegir un nodo con el ratón o con el dedo."
    ]
  },
  "agent_tree.log": {
    "title": "Registro de cambios",
    "hint": "Los cambios de estado de los agentes desde que abriste esta pantalla.",
    "more": [
      "Anota cuando un agente aparece o cambia de estado, con la hora local.",
      "Guarda solo los últimos doce cambios y empieza vacío cada vez que abres la pantalla.",
      "No es un historial de la sesión: para eso está su conversación."
    ]
  },
  "agent_tree.open_child": {
    "title": "Abrir su conversación",
    "hint": "Abre la conversación del agente o la ejecución de su paso.",
    "more": [
      "Si el agente tiene conversación propia, se abre en esta misma pantalla.",
      "Los pasos de una ejecución del framework abren la ejecución que los contiene.",
      "Este enlace solo cambia la vista; no inicia ni modifica ningún trabajo."
    ]
  },

  "team_card.mode": {
    "title": "Modo del equipo",
    "hint": "Dice con qué modo corren las ejecuciones que usan este equipo: supervisado o autónomo.",
    "more": ["Una ejecución hereda este modo si no se elige otro al lanzarla.", "Los valores posibles son supervisado y autónomo.", "Si la versión no declara un modo, la tarjeta lo dice."]
  },
  "team_card.section": {
    "title": "Puestos, reglas, permisos y modelo",
    "hint": "Por cada puesto: sus reglas, lo que puede hacer y el modelo que usaría su agente.",
    "more": ["Las reglas y los permisos salen de la versión que vas a decidir.", "El modelo se lee de los flujos y del catálogo del bundle tal como están ahora.", "Si un puesto no tiene agente o su modelo no está en el catálogo, la tarjeta lo dice en lugar de ocultarlo."]
  },
  "team_card.diff": {
    "title": "Cambios de la versión",
    "hint": "Lo que cambia frente a la versión aprobada, o todo lo nuevo si no hay ninguna.",
    "more": ["Incluye los datos del equipo, los puestos con sus reglas y permisos, los miembros, las asignaciones y las relaciones.", "La primera versión se compara contra un equipo vacío."]
  },
  "team_card.approve": {
    "title": "Aprobar la versión",
    "hint": "Pide confirmación antes de aprobar esta versión del equipo.",
    "more": ["La versión aprobada pasa a ser la activa del equipo.", "La decisión queda registrada a nombre del operador, con el canal de la interfaz y tu nota."]
  },
  "team_card.reject": {
    "title": "Rechazar la versión",
    "hint": "Pide confirmación antes de rechazar esta versión del equipo.",
    "more": ["La versión queda rechazada y la aprobada actual no cambia.", "Una versión rechazada no se puede aprobar después: hay que proponer otra."]
  },
  "team_card.note": {
    "title": "Nota de la decisión",
    "hint": "Texto opcional que queda guardado junto a la decisión.",
    "more": ["Sirve para explicar por qué se aprueba o se rechaza.", "Puede quedar vacía."]
  },
  "team_card.confirm": {
    "title": "Confirmar la decisión",
    "hint": "Envía la decisión sobre esta versión o vuelve sin decidir.",
    "more": ["Sí envía la decisión con tu nota.", "Volver cierra la confirmación y no cambia nada."]
  },
  "team_card.open": {
    "title": "Ver en Equipos",
    "hint": "Abre el equipo en la vista de Equipos, con su organigrama y sus versiones.",
    "more": ["La decisión se puede tomar aquí o allí: es la misma.", "No cambia nada por sí solo."]
  },
  "team_card.refresh": {
    "title": "Actualizar la versión",
    "hint": "Vuelve a leer la versión del equipo y su tarjeta.",
    "more": ["Sirve si otra persona ya decidió o si cambió el catálogo del bundle.", "No cambia nada en el servidor."]
  },

  "plan_card.inputs": {
    "title": "Entradas de las tareas",
    "hint": "Muestra lo que recibirá cada tarea del plan al correr.",
    "more": ["Los textos largos van plegados y un objeto se resume en campos legibles.", "Salen del documento del plan guardado en la propuesta, sin cambios.", "Para editarlas hay que usar la vista de Planes."]
  },
  "plan_card.approve": {
    "title": "Aprobar el plan",
    "hint": "Pide confirmación antes de aprobar el plan con la revisión que ves.",
    "more": ["Se crean las tareas y arrancan las que no esperan a otra.", "La decisión queda registrada a nombre del operador, con tu nota.", "Si el plan cambió mientras lo mirabas, el servidor lo rechaza y la tarjeta se vuelve a leer.", "Se desactiva si falta el equipo aprobado de un paquete con puestos."]
  },
  "plan_card.reject": {
    "title": "Rechazar el plan",
    "hint": "Pide confirmación antes de rechazar el plan.",
    "more": ["El plan queda cerrado como rechazado y no se crean tareas.", "Un plan rechazado no se puede aprobar después: hay que proponer otro."]
  },
  "plan_card.note": {
    "title": "Nota de la decisión",
    "hint": "Texto opcional que queda guardado junto a la decisión.",
    "more": ["Sirve para explicar por qué se aprueba o se rechaza.", "Puede quedar vacía."]
  },
  "plan_card.confirm": {
    "title": "Confirmar la decisión",
    "hint": "Envía la decisión sobre este plan o vuelve sin decidir.",
    "more": ["Sí envía la decisión con tu nota y la revisión que ves.", "Volver cierra la confirmación y no cambia nada."]
  },
  "plan_card.team": {
    "title": "Equipo que falta",
    "hint": "Abre la versión propuesta del equipo, o la vista de Equipos si no hay ninguna.",
    "more": ["Los pasos con puestos necesitan un equipo aprobado del paquete.", "Al aprobar el equipo, vuelve a este plan y pulsa Actualizar."]
  },
  "plan_card.run": {
    "title": "Run de la tarea",
    "hint": "Abre el hilo del run que creó el plan para esta tarea.",
    "more": ["Ahí ves los pasos, las paradas y el resultado.", "No cambia nada por sí solo."]
  },
  "plan_card.open": {
    "title": "Ver en Planes",
    "hint": "Abre el plan en la vista de Planes, con su historial y sus ediciones.",
    "more": ["La decisión se puede tomar aquí o allí: es la misma.", "No cambia nada por sí solo."]
  },
  "plan_card.refresh": {
    "title": "Actualizar el plan",
    "hint": "Vuelve a leer la propuesta, sus avisos y los runs de sus tareas.",
    "more": ["Sirve si otra persona ya decidió o si se aprobó el equipo que faltaba.", "No cambia nada en el servidor."]
  },

  "plan.auto_advance": {
    "title": "El plan avanza solo",
    "hint": "Cuando termina un run del plan o aceptas una de sus tareas, las que esperaban arrancan solas.",
    "more": ["Lo hace el sistema (actor system:worker) cada vez que termina un run del plan o se acepta una de sus tareas, y lo deja anotado aquí.", "Si no pudo avanzar, el mensaje dice por qué y aparece «Avanzar el plan» para hacerlo a mano.", "Un run que se recuperó al arrancar no avanza solo: usa «Avanzar el plan»."]
  }
};

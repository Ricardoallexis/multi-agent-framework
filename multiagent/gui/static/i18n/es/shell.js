// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
export default {
  "shell.skip_link": "Saltar al contenido",
  "shell.menu": "Menú",
  "shell.mock_title": "Resultados sintéticos, sin llamadas a modelos",
  "shell.nav_label": "Sesiones y ejecuciones",
  "shell.customize": "Personalizar",
  "shell.help": "Ayuda",
  "shell.panel": "Contexto",
  "shell.panel_has_content": " (hay contenido)",
  "shell.panel_aria": "Contexto de la sesión",
  "shell.sections": "Secciones",
  "shell.new_session": "Nueva sesión",
  "shell.search": "Buscar",
  "shell.more": "Más",
  "shell.more_notice": "Las demás secciones están en «Más». Puedes fijarlas en la barra.",
  "shell.understood": "Entendido",
  "shell.sessions": "sesiones",
  "shell.sessions_heading": "Sesiones",
  "shell.group_count": "{count} de {label}",
  "shell.runs": "ejecuciones",
  "shell.activities": "Actividades",
  "shell.view_all": "Ver todas",
  "shell.loading_activities": "Cargando las actividades…",
  "shell.activities_failed": "No se pudieron cargar las actividades.",
  "shell.no_open_activities": "Todavía no hay actividades abiertas.",
  "shell.loading_list": "Cargando la lista…",
  "shell.no_sessions_or_runs": "Todavía no hay sesiones ni ejecuciones.",
  "shell.list_summary": "{count} {item_label} en {projects} {project_label}.",
  "shell.item": "elemento",
  "shell.items": "elementos",
  "shell.project": "proyecto",
  "shell.projects": "proyectos",
  "shell.invalid_activities_response": "Respuesta de actividades inválida.",
  "shell.activities_read_failed": "No se pudieron leer las actividades. Vuelve a activar el filtro para reintentarlo.",
  "shell.list_read_failed": "No se pudo leer la lista de {kinds}; se reintentará sola.",
  "shell.or": " ni de ",
  "shell.session_roots_hint": "Las sesiones de Claude, Codex y Copilot necesitan carpetas permitidas (gui_session_roots). En «Sesiones» se explica cómo.",
  "shell.workflows": "Workflows",
  "shell.no_project": "Sin proyecto",
  "shell.unassigned": "Sin asignar",
  "shell.advanced_only": "Esta vista es del modo avanzado",
  "shell.more_information": "Más información: {title}",
  "shell.what_is_this": "Qué es esto",
  "shell.local_server_unreachable": "No se pudo contactar con el servidor local.",
  "shell.restart_for_approvals": "Reinicia la GUI para tomar las aprobaciones.",
  "shell.approval_session_failed": "No se pudo iniciar la sesión de aprobaciones.",
  "shell.quota_five_hours": "Cinco horas",
  "shell.quota_week": "Semana",
  "shell.quota_model_week": "Semana por modelo",
  "shell.quota_month": "Mes",
  "shell.quota_spend": "Gasto",
  "shell.quota_usage": "Uso",
  "shell.quota_reset": "reinicia el {date}",
  "shell.unknown": "desconocido",
  "shell.remaining_quota_no_data": "Cuota restante: sin datos.",
  "shell.quota_no_data": "Cuota: sin datos.",
  "shell.quota_line": "{name}: {percent} {used}{reset}",
  "shell.remaining": "restante",
  "shell.used": "usado",
  "shell.hide_sidebar": "Ocultar la barra lateral",
  "shell.show_sidebar": "Mostrar la barra lateral",
  "shell.show_panel": "Mostrar el panel de contexto",
  "shell.resize_sidebar": "Cambiar el ancho de la barra lateral",
  "shell.resize_panel": "Cambiar el ancho del panel de contexto",
  "shell.offices_heading": "Oficinas",
  "shell.projects_heading": "Proyectos",
  "shell.recent_heading": "Recientes",
  "shell.add_office": "Importar una oficina",
  "shell.new_in": "Nueva conversación en {name}",
  "shell.show_more": "Ver {count} más",
  "shell.show_fewer": "Ver menos",
  "shell.view_plan": "Ver plano",
  "shell.office_areas": "Áreas de {name}",
  "shell.needs_attention": "Espera tu atención",
  "shell.no_offices": "Todavía no hay oficinas.",
  "shell.no_projects": "Todavía no hay proyectos.",
  "help.create.back": {
    "title": "Volver",
    "hint": "Cierra el formulario sin crear nada.",
    "more": [
      "Vuelves a la pantalla desde la que abriste el formulario: la lista de ejecuciones o el chat de nueva sesión.",
      "Lo que escribiste en el formulario se descarta."
    ]
  },
  "help.create.bundle": {
    "title": "Origen del workflow",
    "hint": "De dónde salen los workflows: los incluidos o los de un bundle.",
    "more": [
      "«Workflows incluidos» son los que trae el framework.",
      "Un bundle es una carpeta con workflows propios que registraste al arrancar la interfaz con la opción --bundle nombre=carpeta.",
      "Al cambiar el origen se vacía el workflow elegido y se carga la lista del nuevo origen."
    ]
  },
  "help.create.workflow": {
    "title": "Workflow",
    "hint": "La receta que se va a ejecutar: una serie de pasos con sus prompts.",
    "more": [
      "Al elegirlo aparecen su descripción, sus pasos y las entradas que pide.",
      "Los pasos se ejecutan en orden y algunos se saltan solos según la configuración.",
      "Es obligatorio."
    ]
  },
  "help.create.steps": {
    "title": "Pasos del workflow",
    "hint": "Los pasos que recorrerá la ejecución, en este orden.",
    "more": [
      "Cada paso produce un resultado que puede usar el siguiente.",
      "«Se salta por defecto» significa que ese paso solo se ejecuta si una condición lo pide, por ejemplo una búsqueda web.",
      "Aquí solo se muestran: no se ejecuta ninguno hasta crear la ejecución."
    ]
  },
  "help.create.project": {
    "title": "Proyecto",
    "hint": "Un nombre para reconocer esta ejecución en la lista.",
    "more": [
      "Es solo una etiqueta tuya: no cambia lo que hace el workflow.",
      "Necesita entre 2 y 120 caracteres.",
      "Sirve para agrupar ejecuciones del mismo trabajo."
    ]
  },
  "help.create.inputs.required": {
    "title": "Entradas obligatorias",
    "hint": "Datos que el workflow necesita sí o sí.",
    "more": [
      "Cada entrada es un hueco {nombre} de los prompts que se rellena con tu texto.",
      "Mientras falte alguna no se puede crear la ejecución, y el campo se marca al salir de él sin rellenar.",
      "Debajo de cada campo ves su descripción y, si existe, un ejemplo en gris."
    ]
  },
  "help.create.inputs.optional": {
    "title": "Entradas opcionales",
    "hint": "Datos que puedes dejar vacíos.",
    "more": [
      "Si los dejas vacíos, el hueco queda en blanco en el prompt.",
      "La vista previa avisa de cuáles quedan vacíos.",
      "Rellénalos cuando quieras orientar más al modelo."
    ]
  },
  "help.create.inputs.context": {
    "title": "Contexto adicional opcional",
    "hint": "Apoyo extra: notas o antecedentes que ayudan, pero no hacen falta.",
    "more": [
      "Son las entradas cuyo nombre termina en _context, y nunca son obligatorias.",
      "Están plegadas para no estorbar; ábrelas solo si te sirven."
    ]
  },
  "help.create.mode": {
    "title": "Cómo se ejecuta",
    "hint": "Quién ejecuta cada paso: el framework con un modelo, o tú a mano.",
    "more": [
      "Automático: cada paso usa el modelo que el workflow le asigna (local, salvo que haga falta búsqueda web).",
      "Modelo local: todos los pasos usan un modelo de tu equipo y no se envía nada a la nube.",
      "Modelo en la nube: usa modelos de un proveedor externo; puede costar dinero y no vale con contenido sensible.",
      "Guiado por una persona: el framework no llama a ningún modelo; tú copias el prompt, lo usas donde quieras y pegas la respuesta."
    ]
  },
  "help.create.operating_mode": {
    "title": "Modo",
    "hint": "Opcional: fija si la ejecución pide revisión entre roles o permite escrituras del rol.",
    "more": [
      "«Sin modo» usa el modo del equipo aprobado si tiene uno, o el comportamiento habitual sin modo.",
      "«Supervisado» pide aprobación humana en cada entrega entre roles y en cada escritura de herramienta.",
      "«Autónomo» deja pasar escrituras ordinarias permitidas al rol dentro de los límites de la ejecución.",
      "Lo destructivo, lo externo y aprobar un plan siguen esperando siempre a una persona."
    ]
  },
  "help.create.sensitive": {
    "title": "Contenido sensible",
    "hint": "Márcalo si el texto no debe salir de tu equipo.",
    "more": [
      "En modo automático obliga a usar solo modelos locales.",
      "En modo «Modelo en la nube» la ejecución se rechaza.",
      "En modo guiado se añade un aviso de privacidad al principio del prompt que vas a copiar."
    ]
  },
  "help.create.confirm_real": {
    "title": "Confirmar modelos reales",
    "hint": "El servidor no está en modo Mock: puede usar modelos reales.",
    "more": [
      "En modo Mock los resultados son sintéticos y no cuestan nada; aquí no.",
      "Una ejecución con modelos reales puede tardar y tener coste según el proveedor.",
      "Marca la casilla para confirmar que lo entiendes; sin ella no se puede crear."
    ]
  },
  "help.create.submit": {
    "title": "Crear ejecución",
    "hint": "Crea la ejecución y la deja en cola para que el servidor la tome.",
    "more": [
      "Se activa cuando el workflow, el proyecto y las entradas obligatorias están completos.",
      "Si no se activa, el mensaje de encima dice qué falta.",
      "Pulsarlo dos veces con los mismos datos no crea dos ejecuciones."
    ]
  },
  "help.create.cancel": {
    "title": "Cancelar",
    "hint": "Descarta el formulario sin crear nada.",
    "more": [
      "Vuelves a la lista de ejecuciones.",
      "Lo que escribiste se pierde."
    ]
  },
  "help.preview.panel": {
    "title": "Vista previa del prompt",
    "hint": "Así quedará el prompt de cada paso con lo que escribiste.",
    "more": [
      "Solo se muestra: no se ejecuta nada ni se guarda nada.",
      "Al ejecutar, el framework añade al final de cada prompt las instrucciones del formato de respuesta.",
      "Los pasos que dependen de resultados anteriores salen con esos huecos vacíos hasta que se ejecuten."
    ]
  },
  "help.preview.copy": {
    "title": "Copiar este prompt",
    "hint": "Copia el prompt de este paso al portapapeles.",
    "more": [
      "Sirve para probarlo en otra herramienta antes de ejecutar.",
      "Si el navegador bloquea el portapapeles, selecciona el texto y pulsa Ctrl+C."
    ]
  },
  "help.shell.menu": {
    "title": "Menú de sesiones",
    "hint": "Abre o cierra la lista de sesiones en pantallas estrechas.",
    "more": [
      "En pantallas estrechas la lista de sesiones se oculta para dejar sitio al contenido.",
      "Este botón la muestra como un cajón sobre la pantalla y vuelve a ocultarla.",
      "También se cierra con la tecla Esc o al elegir un elemento de la lista."
    ]
  },
  "help.shell.help_mode": {
    "title": "Modo ayuda",
    "hint": "Muestra u oculta los «?» y las pistas de cada control.",
    "more": [
      "Apagado, solo se ven los «?» de los títulos y de las aprobaciones; la ayuda sigue disponible para los lectores de pantalla.",
      "También se cambia con la tecla F1.",
      "Este navegador recuerda el modo que dejaste."
    ]
  },
  "help.shell.panel": {
    "title": "Contexto",
    "hint": "Muestra u oculta el panel de contexto de la sesión o la ejecución abierta.",
    "more": [
      "Empieza cerrado y solo aparece con una sesión o una ejecución abierta.",
      "Reúne los cambios, los subagentes, los tokens y las fuentes, y solo dibuja las tarjetas con contenido.",
      "Un punto junto al botón avisa de que, con el panel cerrado, hay algo que ver.",
      "En pantallas estrechas aparece debajo del contenido."
    ]
  },
  "help.shell.new_session": {
    "title": "Nueva sesión",
    "hint": "Empieza un trabajo nuevo con un chat guiado.",
    "more": [
      "Describes lo que necesitas o eliges una opción, y el chat te pregunta las entradas del workflow de una en una.",
      "No se ejecuta nada hasta que pulsas «Lanzar» en el resumen final.",
      "Si prefieres el formulario completo, lo abres desde el propio chat con «Ver como formulario»."
    ]
  },
  "help.chat.welcome": {
    "title": "Chat de nueva sesión",
    "hint": "Aquí empiezas un trabajo: escribes lo que necesitas o eliges una opción.",
    "more": [
      "El chat propone workflows, te pregunta las entradas una a una y al final muestra un resumen.",
      "Un modelo puede interpretar tu texto y proponer un workflow; si es de nube, el chat pide confirmación antes de enviarlo.",
      "Si no lo envías, el chat compara las palabras con los nombres y descripciones de los workflows.",
      "La ejecución solo se crea cuando pulsas «Lanzar»."
    ]
  },
  "help.chat.office": {
    "title": "Oficina",
    "hint": "Elige la oficina o una opción de sus áreas.",
    "more": [
      "El selector muestra solo las oficinas instaladas.",
      "En Sesión única, las opciones aparecen agrupadas por área; al elegir una, te preguntaré sus datos.",
      "Al pedir algo a la oficina, el coordinador puede preparar un plan."
    ]
  },
  "help.chat.starters": {
    "title": "Opciones de inicio",
    "hint": "Atajos para empezar sin escribir.",
    "more": [
      "«Trabajar en un workflow» muestra los workflows disponibles.",
      "«Continuar una ejecución» lista las que puedes retomar.",
      "«Ayuda» explica cómo funciona el chat y sus comandos."
    ]
  },
  "help.chat.composer": {
    "title": "Caja de escritura",
    "hint": "Escribe aquí tu petición o tu respuesta; Intro la envía.",
    "more": [
      "Intro envía el mensaje y Mayús+Intro añade una línea nueva.",
      "Puedes escribir los comandos /workflow, /continuar y /ayuda.",
      "Durante las preguntas, lo que escribes es la respuesta a la entrada que se muestra."
    ]
  },
  "help.chat.send": {
    "title": "Enviar mensaje",
    "hint": "Envía lo que escribiste en la caja.",
    "more": [
      "Hace lo mismo que pulsar Intro dentro de la caja.",
      "En una pregunta opcional, enviar la caja vacía equivale a omitirla."
    ]
  },
  "help.chat.cloud_confirm": {
    "title": "Enviar a la nube",
    "hint": "Permite enviar este texto al modelo de nube indicado para proponer un workflow.",
    "more": [
      "El texto y el catálogo de workflows se envían al modelo que aparece en la pregunta.",
      "Si eliges «No enviar», el chat compara las palabras sin llamar al modelo.",
      "Cada llamada cuenta en el uso y respeta los topes de presupuesto."
    ]
  },
  "help.chat.skip": {
    "title": "Omitir pregunta",
    "hint": "Deja vacía esta entrada opcional y pasa a la siguiente.",
    "more": [
      "Solo aparece en las entradas opcionales.",
      "Su hueco queda en blanco en el prompt.",
      "Con «Editar» en el resumen puedes volver a rellenarla."
    ]
  },
  "help.chat.back": {
    "title": "Pregunta anterior",
    "hint": "Vuelve a la pregunta anterior sin perder lo respondido.",
    "more": [
      "Tu respuesta anterior aparece en la caja para que la corrijas.",
      "Volver atrás no ejecuta nada."
    ]
  },
  "help.chat.restart": {
    "title": "Empezar de nuevo",
    "hint": "Descarta esta conversación y vuelve al inicio.",
    "more": [
      "Se borran las respuestas dadas hasta ahora.",
      "No afecta a ninguna ejecución que ya hayas creado."
    ]
  },
  "help.chat.form": {
    "title": "Ver como formulario",
    "hint": "Pasa al formulario completo con lo que ya respondiste.",
    "more": [
      "El formulario tiene los mismos campos y además los ajustes avanzados.",
      "Lo que respondiste en el chat aparece ya escrito en él.",
      "Si vuelves al chat, los cambios hechos en el formulario no se copian de vuelta."
    ]
  },
  "help.chat.suggestion": {
    "title": "Elegir un workflow",
    "hint": "Pulsa una tarjeta para trabajar con ese workflow.",
    "more": [
      "Cada tarjeta muestra el nombre, la descripción y el origen del workflow.",
      "Al elegir uno empiezan las preguntas; todavía no se ejecuta nada.",
      "Si ninguno encaja, escribe otras palabras o usa «Ver todos los workflows»."
    ]
  },
  "help.chat.all": {
    "title": "Ver todos los workflows",
    "hint": "Muestra la lista completa, sin filtrar por tu texto.",
    "more": [
      "Úsalo cuando ninguna propuesta encaje.",
      "Las tarjetas son las mismas, ordenadas por origen y nombre."
    ]
  },
  "help.chat.choice": {
    "title": "Elegir una opción",
    "hint": "Pulsa la opción que quieres como respuesta.",
    "more": [
      "Esta entrada solo admite las opciones mostradas.",
      "También puedes escribir el nombre de la opción en la caja."
    ]
  },
  "help.chat.runs": {
    "title": "Ejecuciones para continuar",
    "hint": "Pulsa una para abrir su hilo y seguir donde quedó.",
    "more": [
      "Primero aparecen las que esperan una acción tuya o se interrumpieron.",
      "Si no hay ninguna, se muestran las más recientes.",
      "Abrir el hilo no modifica la ejecución."
    ]
  },
  "help.chat.question": {
    "title": "Pregunta del chat",
    "hint": "Cada pregunta corresponde a una entrada del workflow.",
    "more": [
      "Ves su nombre, para qué sirve y un ejemplo.",
      "Responde en la caja de abajo.",
      "Puedes volver a la pregunta anterior y, si la entrada es opcional, omitirla."
    ]
  },
  "help.chat.confirm": {
    "title": "Resumen antes de lanzar",
    "hint": "Revisa tus respuestas y el prompt antes de crear la ejecución.",
    "more": [
      "La vista previa es el texto que recibirá cada paso con tus respuestas.",
      "No se crea ninguna ejecución hasta que pulses «Lanzar».",
      "Con «Editar» vuelves a las preguntas y con «Ver como formulario» pasas al formulario."
    ]
  },
  "help.chat.routing": {
    "title": "Ajustes de ejecución",
    "hint": "Configura el modo y las preferencias de cada paso antes de lanzar.",
    "more": [
      "Puedes elegir por paso el modo supervisado, automático o único, activar el perfil ECO y ajustar el nivel, modelo y esfuerzo.",
      "Estas preferencias se guardan al crear la ejecución y se validan con el catálogo ECO, las reglas de privacidad, los límites de nivel y el presupuesto.",
      "Los chips muestran preferencias, no garantizan qué modelo se usará efectivamente; la vista previa solo muestra el prompt y no ejecuta nada."
    ]
  },
  "help.chat.session_mode": {
    "title": "Tipo de sesión",
    "hint": "Elige Orquestador o Sesión única antes de empezar.",
    "more": [
      "Orquestador conserva las preguntas del workflow, el equipo y el plan actuales.",
      "Sesión única permite elegir un modelo del paquete general o un ejecutor conectado.",
      "Puedes volver a elegir el modo antes de lanzar."
    ]
  },
  "help.chat.connect_other": {
    "title": "Conectar otro",
    "hint": "Abre «Apps y agentes» para ver qué le falta a cada app o modelo.",
    "more": [
      "Allí cada app y cada modelo muestra solo el paso que le falta: instalarlo, iniciar sesión, configurar la clave o habilitarlo.",
      "Este enlace solo cambia de pantalla: no instala, no inicia sesión y no habilita nada."
    ]
  },
  "help.chat.single_model": {
    "title": "Modelo para la tarea",
    "hint": "Elige un modelo admitido y conectado.",
    "more": [
      "Solo aparecen modelos del catálogo del paquete general cuyo proveedor está conectado.",
      "Si un proveedor no tiene un modelo admitido, se muestra el motivo.",
      "El servidor valida de nuevo la elección al revisar y lanzar."
    ]
  },
  "help.chat.single_task": {
    "title": "Tarea de sesión única",
    "hint": "Escribe una tarea de texto libre y revísala antes de lanzar.",
    "more": [
      "La tarea pasa al único paso del workflow ask_once.",
      "Revisar muestra el prompt y comprueba el modelo sin ejecutar nada.",
      "Lanzar crea la ejecución y abre su hilo."
    ]
  },
  "help.chat.model_picker": {
    "title": "Modelos y ejecutores conectados",
    "hint": "Elige una opción comprobada para la próxima sesión única.",
    "more": [
      "La lista muestra cuándo se comprobó cada conexión y la cuota de cada ejecutor.",
      "Esta elección no lanza nada ni cambia el enrutamiento del workflow actual."
    ]
  },
  "help.chat.model_choice": {
    "title": "Elegir modelo o ejecutor",
    "hint": "Solo se pueden elegir opciones habilitadas y conectadas.",
    "more": [
      "Las opciones sin conexión aparecen plegadas con el motivo y el paso pendiente.",
      "Antigravity usa tu suscripción y pide aprobación en cada lanzamiento."
    ]
  },
  "help.chat.model_disconnected": {
    "title": "Opciones no conectadas",
    "hint": "Muestra las opciones que todavía no se pueden elegir.",
    "more": [
      "Cada opción indica el paso pendiente o el motivo que la bloquea.",
      "Vuelve a comprobarla desde Apps y agentes después de completar ese paso."
    ]
  },
  "help.chat.model_retry": {
    "title": "Reintentar conexiones",
    "hint": "Vuelve a leer el estado de conexión que guarda el servidor.",
    "more": [
      "No inicia ninguna sesión ni ejecuta un modelo.",
      "Si sigue fallando, comprueba que el servidor local está disponible."
    ]
  },
  "help.chat.launch": {
    "title": "Lanzar",
    "hint": "Crea la ejecución con estas respuestas.",
    "more": [
      "Es el único botón del chat que ejecuta algo.",
      "Si el servidor no está en modo Mock, antes debes confirmar que puede usar modelos reales.",
      "Al crearla, el chat sigue el avance de la ejecución."
    ]
  },
  "help.chat.edit": {
    "title": "Editar respuestas",
    "hint": "Vuelve a las preguntas con tus respuestas ya escritas.",
    "more": [
      "Cada pregunta aparece con tu respuesta anterior en la caja.",
      "Pulsa Intro para conservarla o escribe otra."
    ]
  },
  "help.chat.retry": {
    "title": "Reintentar la lista de workflows",
    "hint": "Vuelve a pedir la lista cuando falló.",
    "more": [
      "Este botón solo aparece si no se pudo leer la lista de workflows.",
      "Si el fallo continúa, abre el formulario con «Ver como formulario»."
    ]
  },
  "help.shell.search": {
    "title": "Buscar",
    "hint": "Abre la búsqueda (Ctrl K) para encontrar una sección, una sesión, una ejecución o una ayuda.",
    "more": [
      "Busca en el título, el proyecto, la aplicación y el estado de cada elemento.",
      "No distingue mayúsculas ni acentos, y pide que aparezcan todas las palabras escritas.",
      "Solo abre pantallas y explica: no busca dentro de las conversaciones."
    ]
  },
  "help.shell.activities": {
    "title": "Actividades",
    "hint": "Muestra en la lista las actividades abiertas de tus proyectos, en lugar de las sesiones y ejecuciones.",
    "more": [
      "Cada actividad lleva su estado y quién trabaja en ella; al elegirla se abre el tablero de su proyecto.",
      "Las actividades no cuentan en el avance del proyecto.",
      "Vuelve a pulsar el botón para ver otra vez las sesiones y ejecuciones."
    ]
  },
  "help.chat.executor_launch": {
    "title": "Lanzar con un ejecutor",
    "hint": "Revisa ejecutor, modelo, esfuerzo y cuota antes de aprobar.",
    "more": [
      "La tarea y la selección no inician nada; primero se muestra una confirmación con los ajustes y la cuota restante.",
      "Solo «Aprobar y lanzar» envía la confirmación explícita al servidor.",
      "Antigravity muestra su aviso y respeta el interruptor, el uso de créditos desactivado y sus pisos de cuota.",
      "El seguimiento consulta el estado de la tarea y enlaza su sesión en Sesiones."
    ]
  },
  "help.chat.limit_notice": {
    "title": "Límite alcanzado",
    "hint": "Aviso cuando un modelo o ejecutor agota su cuota o ritmo.",
    "more": [
      "Muestra «vuelve a las HH:MM» en tu hora local si hay dato, o «sin hora conocida».",
      "Nunca cambia de modelo ni de ejecutor por su cuenta en un mensaje nuevo.",
      "Las sugerencias disponibles aparecen como botones para que elijas tú."
    ]
  },
  "help.chat.limit_suggestion": {
    "title": "Sugerencia alternativa",
    "hint": "Cambia la selección a otra opción disponible sin enviar nada.",
    "more": [
      "Conserva el texto que ya escribiste en la caja.",
      "Solo cambia el modelo o ejecutor elegido; enviar o lanzar sigue siendo tuyo."
    ]
  },
  "help.chat.connection_checks": {
    "title": "Estado de Gemini y Copilot",
    "hint": "Gemini «Sin comprobar» se prueba al enviar; Copilot usa su caché del mes.",
    "more": [
      "«Sin comprobar» significa que Gemini tiene clave y es elegible, pero aún no respondió en esta sesión.",
      "Copilot solo es elegible cuando su caché local del mes está vigente; sin caché válida sigue sin comprobarse.",
      "Puedes revisar el detalle de cada conexión en «Apps y agentes»."
    ]
  },
  "help.shell.more": {
    "title": "Más secciones",
    "hint": "Muestra las secciones que no están fijadas en la barra.",
    "more": [
      "Aquí siguen disponibles las demás pantallas.",
      "Abrir una sección solo cambia la vista.",
      "Puedes fijar las que uses con frecuencia."
    ]
  },
  "help.shell.more_notice": {
    "title": "Entendido",
    "hint": "Cierra este aviso de primera visita.",
    "more": [
      "Las secciones siguen disponibles en «Más».",
      "El aviso no vuelve a salir en este navegador.",
      "Si el almacenamiento está bloqueado, vuelve en la próxima carga."
    ]
  },
  "help.chat.box_menu": {
    "title": "Más opciones",
    "hint": "Abre las otras formas de empezar.",
    "more": [
      "Puedes usar un ejecutor o abrir el formulario completo.",
      "Elegir una opción no lanza nada."
    ]
  },
  "help.chat.project_select": {
    "title": "Proyecto de la sesión",
    "hint": "Elige un proyecto reciente o confírmalo durante el flujo.",
    "more": [
      "La pregunta de proyecto sigue en el flujo guiado.",
      "Puedes corregir el nombre antes de lanzar.",
      "El lanzamiento con ejecutor no usa proyectos."
    ]
  },
  "help.customize.pin": {
    "title": "Fijar en la barra",
    "hint": "Pone esta sección entre las fijas de la barra lateral.",
    "more": [
      "Vuelve a pulsar para soltarla: pasa otra vez a «Más».",
      "Soltarla no quita la sección: sigue en «Más» y en la búsqueda."
    ]
  },
  "help.customize.card": {
    "title": "Cuándo se ve la tarjeta",
    "hint": "Automática: solo con algo que mostrar. Siempre: aunque esté vacía. Nunca: oculta.",
    "more": [
      "Lo urgente se ve en cualquier modo.",
      "Es la tarjeta del panel «Contexto»; cambiarlo no borra sus datos."
    ]
  },
  "help.customize.help": {
    "title": "Mostrar siempre los «?»",
    "hint": "Deja a la vista las ayudas «?» de cada control.",
    "more": [
      "Es el mismo interruptor que «Ayuda» y la tecla F1.",
      "Apagado, las ayudas siguen disponibles al activarlo."
    ]
  },
  "help.customize.reset": {
    "title": "Restablecer",
    "hint": "Vuelve a los valores por defecto de la barra, las tarjetas, la ayuda y el color.",
    "more": [
      "Suelta las secciones fijadas, deja las tarjetas en su modo inicial, apaga el modo ayuda y vuelve al color Neutro.",
      "No toca sesiones, ejecuciones ni datos."
    ]
  },
  "help.shell.customize": {
    "title": "Personalizar",
    "hint": "Abre la pantalla para elegir qué se ve.",
    "more": [
      "Se puede fijar secciones en la barra, elegir las tarjetas de «Contexto» y dejar la ayuda a la vista.",
      "Solo cambia lo que se muestra."
    ]
  },
  "help.customize.accent": {
    "title": "Paleta de colores",
    "hint": "Elige el color de los botones principales, los enlaces y la selección.",
    "more": [
      "Neutro es el de siempre. Los fondos, el texto y los avisos no cambian con la paleta.",
      "Se recuerda en este navegador."
    ]
  },
  "help.customize.advanced_mode": {
    "title": "Modo avanzado",
    "hint": "Muestra todas las secciones. Apagado, la barra solo trae lo esencial.",
    "more": [
      "Es un ajuste de esta instalación: vale en todos los navegadores y se guarda en el servidor.",
      "Apagarlo esconde las secciones avanzadas, no las borra: las que fijaste siguen fijadas para cuando lo vuelvas a encender.",
      "La GUI se redibuja al cambiarlo, sin recargar la página."
    ]
  },
  "help.shell.advanced_only": {
    "title": "Vista del modo avanzado",
    "hint": "Esta pantalla solo se muestra en el modo avanzado.",
    "more": [
      "Pulsa «Activar modo avanzado» para verla. Puedes volver al modo simple desde «Personalizar».",
      "No se pierde nada: la dirección sigue igual."
    ]
  },
  "help.chat.plan_draft": {
    "title": "Borrador del plan",
    "hint": "Tu pedido necesita varios pasos: esto es lo que se guardaría. Todavía no se guardó nada.",
    "more": [
      "Cada tarea muestra su paso y los textos que recibiría. Si un paquete necesita un equipo que nadie aprobó, aquí se dice cuál se propondrá.",
      "Mientras no pulses «Guardar el plan», no se crea nada y no se llama al servidor.",
      "En el modo avanzado cada tarea muestra además su workflow y su paquete."
    ]
  },
  "help.chat.office_plan": {
    "title": "Plan por defecto de la oficina",
    "hint": "El aviso explica por qué se preparó este plan.",
    "more": [
      "El borrador solo muestra lo que se guardaría; no aprueba ni ejecuta nada.",
      "«Guardar el plan» lo conserva para que lo revises después."
    ]
  },
  "help.chat.plan_save": {
    "title": "Guardar el plan",
    "hint": "«Guardar el plan» lo guarda para que lo revises. «Cancelar» lo descarta.",
    "more": [
      "Guardar no aprueba nada: abre el hilo del plan, donde están «Aprobar» y «Rechazar».",
      "Cuando guardas, los equipos que faltan quedan propuestos, no aprobados. Aprobar el plan antes que el equipo da un aviso en el hilo.",
      "«Cancelar» solo cierra el borrador: no se guarda ni se pierde nada más."
    ]
  },
  "help.shell.instance_label": {
    "title": "Etiqueta de esta instancia",
    "hint": "Es el nombre que se puso a esta instancia. También aparece en el título de la pestaña.",
    "more": [
      "Se cambia con el ajuste INSTANCE_LABEL y el servidor se reinicia para que la tome.",
      "Sirve para distinguir dos instancias abiertas a la vez. Es solo texto: no cambia dónde se guardan los datos."
    ]
  },
};

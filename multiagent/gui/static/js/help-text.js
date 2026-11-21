// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Contextual help (GUI-T12): every control and section of the screen has a short hint (one line, always visible)
// and a longer explanation (2 to 4 sentences, behind the "?" button). Spanish, plain text, no jargon.
//
// To edit a text, change it here and nothing else. The block below is strict JSON on purpose, so that a test can
// read it without running the browser: double quotes, no trailing commas, no comments, one sentence per line.
// Each entry is { title, hint, more }: title names the control for screen readers, hint is the visible line and
// more is the list of sentences that opens with the "?" button. The key is what the view passes to <Help topic=...>.
// tests/test_gui_help.py checks that every control of the views has an entry and that the texts stay short.

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
  "runs.section": {
    "title": "Ejecuciones",
    "hint": "Aquí lanzas workflows del framework y decides qué pasa en cada paso.",
    "more": [
      "Una ejecución es un workflow con sus entradas que avanza paso a paso.",
      "A la izquierda están las ejecuciones guardadas, la más reciente primero; a la derecha, el detalle de la elegida o el formulario de una nueva.",
      "Cuando un paso necesita a una persona, la ejecución espera aquí tu decisión."
    ]
  },
  "runs.new": {
    "title": "Nueva ejecución",
    "hint": "Abre el formulario para lanzar un workflow.",
    "more": [
      "Eliges un workflow, rellenas sus entradas y el modo de ejecución.",
      "Antes de crear nada ves una vista previa del prompt; la ejecución solo existe cuando pulsas «Crear ejecución»."
    ]
  },
  "runs.list": {
    "title": "Lista de ejecuciones",
    "hint": "Las ejecuciones guardadas, la más reciente primero.",
    "more": [
      "Cada fila muestra el workflow, el proyecto, el estado y cuándo se creó.",
      "Si la ejecución viene de un bundle, su nombre aparece al final de la fila.",
      "Pulsa una fila para abrir su detalle."
    ]
  },
  "runs.back": {
    "title": "Volver a la lista",
    "hint": "Cierra el detalle y vuelve a la lista de ejecuciones.",
    "more": [
      "En pantallas estrechas muestra de nuevo la lista de ejecuciones.",
      "No detiene ni modifica la ejecución abierta."
    ]
  },
  "run.open_chat": {
    "title": "Abrir en el chat",
    "hint": "Abre esta ejecución como hilo en la vista de chat.",
    "more": [
      "Muestra los pasos y las decisiones pendientes en forma de conversación.",
      "Conserva la ejecución actual sin crear otra nueva."
    ]
  },
  "human.bar_focus": {
    "title": "Ir a las acciones",
    "hint": "Desplaza la vista hasta la tarjeta de acciones humanas.",
    "more": [
      "Te lleva arriba del detalle donde están todas las opciones disponibles.",
      "Úsalo para pedir cambios, rechazar o pegar una respuesta."
    ]
  },

  "create.back": {
    "title": "Volver",
    "hint": "Cierra el formulario sin crear nada.",
    "more": [
      "Vuelves a la pantalla desde la que abriste el formulario: la lista de ejecuciones o el chat de nueva sesión.",
      "Lo que escribiste en el formulario se descarta."
    ]
  },
  "create.bundle": {
    "title": "Origen del workflow",
    "hint": "De dónde salen los workflows: los incluidos o los de un bundle.",
    "more": [
      "«Workflows incluidos» son los que trae el framework.",
      "Un bundle es una carpeta con workflows propios que registraste al arrancar la interfaz con la opción --bundle nombre=carpeta.",
      "Al cambiar el origen se vacía el workflow elegido y se carga la lista del nuevo origen."
    ]
  },
  "create.workflow": {
    "title": "Workflow",
    "hint": "La receta que se va a ejecutar: una serie de pasos con sus prompts.",
    "more": [
      "Al elegirlo aparecen su descripción, sus pasos y las entradas que pide.",
      "Los pasos se ejecutan en orden y algunos se saltan solos según la configuración.",
      "Es obligatorio."
    ]
  },
  "create.steps": {
    "title": "Pasos del workflow",
    "hint": "Los pasos que recorrerá la ejecución, en este orden.",
    "more": [
      "Cada paso produce un resultado que puede usar el siguiente.",
      "«Se salta por defecto» significa que ese paso solo se ejecuta si una condición lo pide, por ejemplo una búsqueda web.",
      "Aquí solo se muestran: no se ejecuta ninguno hasta crear la ejecución."
    ]
  },
  "create.project": {
    "title": "Proyecto",
    "hint": "Un nombre para reconocer esta ejecución en la lista.",
    "more": [
      "Es solo una etiqueta tuya: no cambia lo que hace el workflow.",
      "Necesita entre 2 y 120 caracteres.",
      "Sirve para agrupar ejecuciones del mismo trabajo."
    ]
  },
  "create.inputs.required": {
    "title": "Entradas obligatorias",
    "hint": "Datos que el workflow necesita sí o sí.",
    "more": [
      "Cada entrada es un hueco {nombre} de los prompts que se rellena con tu texto.",
      "Mientras falte alguna no se puede crear la ejecución, y el campo se marca al salir de él sin rellenar.",
      "Debajo de cada campo ves su descripción y, si existe, un ejemplo en gris."
    ]
  },
  "create.inputs.optional": {
    "title": "Entradas opcionales",
    "hint": "Datos que puedes dejar vacíos.",
    "more": [
      "Si los dejas vacíos, el hueco queda en blanco en el prompt.",
      "La vista previa avisa de cuáles quedan vacíos.",
      "Rellénalos cuando quieras orientar más al modelo."
    ]
  },
  "create.inputs.context": {
    "title": "Contexto adicional opcional",
    "hint": "Apoyo extra: notas o antecedentes que ayudan, pero no hacen falta.",
    "more": [
      "Son las entradas cuyo nombre termina en _context, y nunca son obligatorias.",
      "Están plegadas para no estorbar; ábrelas solo si te sirven."
    ]
  },
  "create.mode": {
    "title": "Cómo se ejecuta",
    "hint": "Quién ejecuta cada paso: el framework con un modelo, o tú a mano.",
    "more": [
      "Automático: cada paso usa el modelo que el workflow le asigna (local, salvo que haga falta búsqueda web).",
      "Modelo local: todos los pasos usan un modelo de tu equipo y no se envía nada a la nube.",
      "Modelo en la nube: usa modelos de un proveedor externo; puede costar dinero y no vale con contenido sensible.",
      "Guiado por una persona: el framework no llama a ningún modelo; tú copias el prompt, lo usas donde quieras y pegas la respuesta."
    ]
  },
  "create.operating_mode": {
    "title": "Modo",
    "hint": "Opcional: fija si la ejecución pide revisión entre roles o permite escrituras del rol.",
    "more": [
      "«Sin modo» usa el modo del equipo aprobado si tiene uno, o el comportamiento habitual sin modo.",
      "«Supervisado» pide aprobación humana en cada entrega entre roles y en cada escritura de herramienta.",
      "«Autónomo» deja pasar escrituras ordinarias permitidas al rol dentro de los límites de la ejecución.",
      "Lo destructivo, lo externo y aprobar un plan siguen esperando siempre a una persona."
    ]
  },
  "create.sensitive": {
    "title": "Contenido sensible",
    "hint": "Márcalo si el texto no debe salir de tu equipo.",
    "more": [
      "En modo automático obliga a usar solo modelos locales.",
      "En modo «Modelo en la nube» la ejecución se rechaza.",
      "En modo guiado se añade un aviso de privacidad al principio del prompt que vas a copiar."
    ]
  },
  "create.confirm_real": {
    "title": "Confirmar modelos reales",
    "hint": "El servidor no está en modo Mock: puede usar modelos reales.",
    "more": [
      "En modo Mock los resultados son sintéticos y no cuestan nada; aquí no.",
      "Una ejecución con modelos reales puede tardar y tener coste según el proveedor.",
      "Marca la casilla para confirmar que lo entiendes; sin ella no se puede crear."
    ]
  },
  "create.submit": {
    "title": "Crear ejecución",
    "hint": "Crea la ejecución y la deja en cola para que el servidor la tome.",
    "more": [
      "Se activa cuando el workflow, el proyecto y las entradas obligatorias están completos.",
      "Si no se activa, el mensaje de encima dice qué falta.",
      "Pulsarlo dos veces con los mismos datos no crea dos ejecuciones."
    ]
  },
  "create.cancel": {
    "title": "Cancelar",
    "hint": "Descarta el formulario sin crear nada.",
    "more": [
      "Vuelves a la lista de ejecuciones.",
      "Lo que escribiste se pierde."
    ]
  },

  "preview.panel": {
    "title": "Vista previa del prompt",
    "hint": "Así quedará el prompt de cada paso con lo que escribiste.",
    "more": [
      "Solo se muestra: no se ejecuta nada ni se guarda nada.",
      "Al ejecutar, el framework añade al final de cada prompt las instrucciones del formato de respuesta.",
      "Los pasos que dependen de resultados anteriores salen con esos huecos vacíos hasta que se ejecuten."
    ]
  },
  "preview.copy": {
    "title": "Copiar este prompt",
    "hint": "Copia el prompt de este paso al portapapeles.",
    "more": [
      "Sirve para probarlo en otra herramienta antes de ejecutar.",
      "Si el navegador bloquea el portapapeles, selecciona el texto y pulsa Ctrl+C."
    ]
  },

  "run.refresh": {
    "title": "Actualizar",
    "hint": "Vuelve a pedir el estado de la ejecución al servidor.",
    "more": [
      "Normalmente no hace falta: la pantalla se actualiza sola cuando hay novedades.",
      "Úsalo si ves «Sin conexión en vivo» y quieres comprobar el estado ahora."
    ]
  },
  "run.live": {
    "title": "En vivo",
    "hint": "Indica si la pantalla recibe los cambios al instante.",
    "more": [
      "«En vivo» significa que el servidor avisa en cuanto la ejecución cambia.",
      "Con «Sin conexión en vivo» la pantalla se actualiza sola cada 10 segundos hasta que vuelva la conexión.",
      "Las ejecuciones de un bundle también se consultan cada 10 segundos."
    ]
  },
  "run.steps": {
    "title": "Pasos",
    "hint": "El recorrido del workflow y en qué paso va.",
    "more": [
      "Cada paso muestra su estado: hecho, en curso, pendiente, esperando a una persona o saltado.",
      "En los pasos hechos ves qué modelo, o qué persona, produjo el resultado y cuántos intentos hicieron falta.",
      "Un paso saltado no se ejecuta ni gasta llamadas al modelo."
    ]
  },
  "run.skipped": {
    "title": "Pasos saltados",
    "hint": "Un paso se salta cuando no se cumple su condición.",
    "more": [
      "Algunos pasos solo valen si la ejecución pide búsqueda web, o solo si no la pide; el workflow lo declara con una condición.",
      "Si no se cumple, el paso no se ejecuta y junto a él aparece el motivo.",
      "«Se saltará» es un aviso previo; «Se saltó» ya ocurrió."
    ]
  },
  "run.role": {
    "title": "Rol del paso",
    "hint": "El puesto del equipo aprobado con el que se ejecutó este paso.",
    "more": [
      "El rol aporta su propósito, sus reglas y sus habilidades al paso, e indica qué miembro del equipo lo ejecuta.",
      "Sale de la versión aprobada del equipo vinculada al crear la ejecución, nunca de cambios posteriores."
    ]
  },
  "run.operating_mode": {
    "title": "Modo de operación",
    "hint": "El modo fijado al crear la ejecución y si vino de la ejecución o del equipo.",
    "more": [
      "«Supervisado» pide revisión humana en entregas entre roles y aprobación en cada escritura.",
      "«Autónomo» permite escrituras ordinarias del rol, pero lo destructivo y lo externo siguen esperando.",
      "El modo queda fijado al crear la ejecución y no cambia aunque se apruebe otro equipo después."
    ]
  },
  "run.counters": {
    "title": "Contadores",
    "hint": "Cuánto lleva gastado la ejecución frente a sus límites.",
    "more": [
      "Los máximos evitan bucles y gastos inesperados.",
      "Cuando se agotan las llamadas al modelo ya no se pueden pedir cambios ni regenerar; solo aprobar, rechazar o cancelar.",
      "Se actualizan mientras la ejecución avanza."
    ]
  },
  "counter.model_calls": {
    "title": "Llamadas al modelo",
    "hint": "Cuántas veces el framework llamó a un modelo, frente a su máximo.",
    "more": [
      "Solo cuentan las llamadas que hace el framework; los pasos que hace una persona o un modelo externo (modo guiado) no cuentan.",
      "Repetir un paso, por cambios o por regeneración, vuelve a llamar al modelo y suma otra."
    ]
  },
  "counter.human_steps": {
    "title": "Pasos hechos por una persona",
    "hint": "Pasos resueltos copiando y pegando, sin llamar a un modelo.",
    "more": [
      "Salen cuando la ejecución está en modo guiado o cuando un paso pide una investigación externa.",
      "No gastan llamadas al modelo."
    ]
  },
  "counter.steps": {
    "title": "Pasos",
    "hint": "Pasos hechos del total, y cuántos se saltaron.",
    "more": [
      "«Hechos» cuenta solo los que terminaron; los saltados van aparte, entre paréntesis.",
      "Un paso se salta cuando no se cumple su condición, por ejemplo la búsqueda web."
    ]
  },
  "counter.time": {
    "title": "Tiempo activo",
    "hint": "Segundos que el servidor ha trabajado en esta ejecución, frente a su máximo.",
    "more": [
      "No cuenta el tiempo que la ejecución espera a una persona.",
      "Si se supera el máximo, la ejecución se detiene."
    ]
  },
  "run.history": {
    "title": "Historial de acciones humanas",
    "hint": "Qué decisiones se tomaron y quién las tomó.",
    "more": [
      "Cada línea tiene fecha, quién actuó y qué hizo: aprobar, pedir cambios, rechazar, regenerar, cancelar o enviar una respuesta pegada.",
      "«Sistema» son acciones automáticas, «Operador» las tuyas en esta pantalla y «declarado» lo que una persona contó sin que se verifique.",
      "Los comentarios y los errores se muestran debajo de su línea."
    ]
  },
  "run.results": {
    "title": "Resultados",
    "hint": "Lo que produjo cada paso, agrupado por paso e intento.",
    "more": [
      "Cada paso reúne sus intentos completados con quién lo produjo, su revisión y su salida.",
      "Cuando un paso tiene dos o más intentos, puedes elegir dos y pulsar «Comparar» para ver qué cambió.",
      "Cuando la ejecución espera tu revisión, se abre el resultado del paso que revisas."
    ]
  },
  "run.compare_select": {
    "title": "Intentos a comparar",
    "hint": "Elige qué dos intentos de este paso quieres comparar.",
    "more": [
      "El primero es la referencia anterior y el segundo es el intento nuevo.",
      "Por defecto se eligen los dos últimos intentos completados."
    ]
  },
  "run.compare": {
    "title": "Comparar intentos",
    "hint": "Muestra qué campos cambiaron entre los dos intentos elegidos.",
    "more": [
      "Compara la salida campo a campo indicando lo añadido, lo quitado y lo cambiado.",
      "En textos largos o de varias líneas muestra las diferencias línea por línea."
    ]
  },
  "run.inputs": {
    "title": "Entradas de la ejecución",
    "hint": "Los datos con los que se creó esta ejecución.",
    "more": [
      "Son los valores que escribiste en el formulario; aquí solo se leen.",
      "Cada nombre corresponde a un hueco {nombre} de los prompts.",
      "«(vacía)» significa que se dejó sin rellenar."
    ]
  },
  "run.events": {
    "title": "Eventos técnicos",
    "hint": "El registro interno de lo que hizo el framework.",
    "more": [
      "Cada cambio de estado deja un evento con su hora; de ahí sale todo lo demás.",
      "Está pensado para diagnosticar problemas; para el día a día bastan los pasos y el historial.",
      "Solo se muestran los más recientes."
    ]
  },

  "human.section": {
    "title": "Acciones humanas",
    "hint": "Lo que puedes hacer con esta ejecución ahora mismo.",
    "more": [
      "Los botones cambian según lo que la ejecución espera: revisar un resultado, hacer un paso fuera del framework o nada.",
      "El servidor decide qué acciones son posibles; si una ya no lo es, la vista se actualiza y se explica por qué.",
      "Cada acción queda anotada en el historial."
    ]
  },
  "human.approve": {
    "title": "Aprobar",
    "hint": "Da por bueno el resultado del paso y sigue con el siguiente.",
    "more": [
      "Si era el último paso, la ejecución termina como completada.",
      "El resultado aprobado queda marcado como tal en «Resultados»."
    ]
  },
  "human.tool_approve": {
    "title": "Aprobar esta llamada",
    "hint": "Deja que el paso use la herramienta una vez, con lo que se muestra arriba.",
    "more": [
      "La aprobación vale solo para esta llamada: otra llamada vuelve a pedir permiso.",
      "Si los datos de la llamada cambian, la aprobación ya no sirve y se pide otra vez."
    ]
  },
  "human.regenerate": {
    "title": "Regenerar sin comentarios",
    "hint": "Repite el paso otra vez, sin darle indicaciones.",
    "more": [
      "Sirve cuando el resultado no te convence pero no sabes qué corregir.",
      "Vuelve a llamar al modelo, así que cuenta como otra llamada y puede dar un resultado distinto.",
      "Desaparece cuando se agota el presupuesto."
    ]
  },
  "human.changes": {
    "title": "Pedir cambios",
    "hint": "Repite el paso con tus comentarios sobre qué corregir.",
    "more": [
      "Escribe una indicación general, añade comentarios sobre partes concretas, o ambas cosas.",
      "Sirve cuando el resultado va bien encaminado pero hay que corregir algo concreto.",
      "Cuenta como otra llamada al modelo y desaparece si se agotó el presupuesto."
    ]
  },
  "human.comment_add": { "title": "Añadir comentario", "hint": "Añade una indicación sobre una parte de la salida.", "more": ["Puedes añadir hasta veinte comentarios.", "Cada uno puede señalar una parte distinta."] },
  "human.comment_target": { "title": "Parte de la salida", "hint": "Elige General o escribe una ruta como items[2].title.", "more": ["Las claves visibles aparecen en la lista.", "También puedes escribir una ruta más precisa."] },
  "human.comment_kind": { "title": "Tipo de cambio", "hint": "Indica si hay que corregir, añadir, quitar o reescribir.", "more": ["Elige el tipo que mejor describa tu petición.", "Otro sirve para cambios distintos."] },
  "human.comment_text": { "title": "Texto del comentario", "hint": "Explica el cambio que quieres.", "more": ["Este texto es obligatorio en cada comentario.", "Se enviará al siguiente intento."] },
  "human.comment_quote": { "title": "Cita de la salida", "hint": "Opcional: copia un fragmento del resultado al que te refieres.", "more": ["La cita ayuda a identificar el fragmento.", "Se entrega como dato no confiable."] },
  "human.comment_remove": { "title": "Quitar comentario", "hint": "Quita esta fila antes de enviar la petición.", "more": ["Solo se elimina del formulario actual.", "Los demás comentarios permanecen."] },
  "human.reject": {
    "title": "Rechazar ejecución",
    "hint": "Termina la ejecución como rechazada; no se puede deshacer.",
    "more": [
      "Úsalo cuando el resultado no sirve y no quieres seguir intentándolo.",
      "Puedes escribir un motivo opcional, que queda en el historial.",
      "Antes de rechazar se te pide confirmación."
    ]
  },
  "human.reject_reason": {
    "title": "Motivo del rechazo",
    "hint": "Opcional: por qué rechazas.",
    "more": [
      "Queda anotado en el historial junto a tu rechazo.",
      "Puedes dejarlo vacío."
    ]
  },
  "human.cancel": {
    "title": "Cancelar ejecución",
    "hint": "Detiene la ejecución antes de que termine.",
    "more": [
      "Si está corriendo, se pide detenerla y quedará cancelada cuando termine el paso en curso.",
      "Si está esperando a una persona, se cancela enseguida.",
      "No se puede deshacer, así que se pide confirmación."
    ]
  },
  "human.prompt": {
    "title": "Prompt a copiar",
    "hint": "El texto exacto que tienes que llevar a otra herramienta.",
    "more": [
      "Cópialo tal cual en el modelo o la herramienta que prefieras.",
      "Ya incluye tus entradas y, al final, las instrucciones del formato en que debe responder.",
      "Está en solo lectura para que no lo cambies sin querer."
    ]
  },
  "human.copy_prompt": {
    "title": "Copiar prompt",
    "hint": "Copia el prompt al portapapeles.",
    "more": [
      "Evita tener que seleccionar el texto a mano.",
      "Si el navegador bloquea el portapapeles, el prompt queda seleccionado y basta con pulsar Ctrl+C."
    ]
  },
  "human.response": {
    "title": "Respuesta pegada",
    "hint": "Pega aquí la respuesta completa de la otra herramienta.",
    "more": [
      "Debe cumplir el formato que se indica arriba.",
      "El servidor la valida al enviarla; si no lo cumple, se rechaza y el texto se queda aquí para que lo corrijas.",
      "Tu borrador se conserva aunque cambies de pantalla, mientras no recargues la página."
    ]
  },
  "human.provider": {
    "title": "Proveedor",
    "hint": "Quién produjo la respuesta, por ejemplo la herramienta que usaste.",
    "more": [
      "Lo declaras tú y no se verifica: queda en el historial como «declarado».",
      "Si lo dejas vacío se anota «human».",
      "Sirve para saber después de dónde salió cada resultado."
    ]
  },
  "human.model": {
    "title": "Modelo",
    "hint": "Opcional: el nombre del modelo que respondió.",
    "more": [
      "También lo declaras tú y no se verifica.",
      "Si no lo sabes, déjalo vacío: el historial dirá «sin modelo indicado»."
    ]
  },
  "human.prompt_used": {
    "title": "Prompt usado",
    "hint": "Opcional: anota el prompt exacto si lo cambiaste antes de usarlo fuera.",
    "more": ["Si lo dejas vacío se guarda el prompt mostrado para este paso.", "Queda asociado a la respuesta enviada."]
  },
  "human.notes": {
    "title": "Notas",
    "hint": "Opcional: añade contexto sobre cómo obtuviste la respuesta.",
    "more": ["Las notas se guardan junto al envío.", "No afectan a la validación del contrato."]
  },
  "external.section": {
    "title": "Salida externa",
    "hint": "Entrega una salida hecha fuera del framework, con sus evidencias.",
    "more": ["Primero ves una vista previa que no cambia nada; después la confirmas.", "Al confirmar se guarda un recibo con los hashes de todo lo entregado."]
  },
  "external.output": {
    "title": "Salida en JSON",
    "hint": "El objeto JSON que cumple el contrato del paso que espera.",
    "more": ["Pégalo completo; la vista previa lo valida contra el contrato.", "Si hay un error, lo que escribiste se queda en pantalla para corregirlo."]
  },
  "external.evidence": {
    "title": "Evidencias",
    "hint": "Archivos opcionales que respaldan la salida.",
    "more": ["Se admiten imágenes, vídeos cortos, texto y JSON, dentro de los límites de tamaño.", "La página solo muestra su nombre, tipo, tamaño y hash; nunca su contenido."]
  },
  "external.preview": {
    "title": "Vista previa",
    "hint": "Valida la salida y las evidencias sin cambiar la ejecución.",
    "more": ["Muestra la salida normalizada, cada archivo con su hash y cuándo caduca la vista previa.", "Si caduca, prepara otra."]
  },
  "external.confirm": {
    "title": "Confirmar salida externa",
    "hint": "Entrega la salida mostrada en la vista previa.",
    "more": ["Pide una confirmación antes de guardar nada.", "Después muestra el recibo con quién, por qué canal y cuándo."]
  },
  "human.check": {
    "title": "Validar sin enviar",
    "hint": "Comprueba el JSON, el contrato y sus campos antes de enviarlo.",
    "more": ["Muestra los problemas por campo sin guardar la respuesta ni avanzar la ejecución.", "Puedes corregir el texto y volver a validar."]
  },
  "human.submit": {
    "title": "Enviar respuesta",
    "hint": "Entrega la respuesta pegada para que el framework la valide.",
    "more": [
      "Si cumple el formato, la ejecución sigue; según el workflow, antes puede pasar a una revisión tuya.",
      "Si no lo cumple, se rechaza con el motivo y puedes corregirla.",
      "El envío queda en el historial con el proveedor y el modelo que declaraste."
    ]
  },

  "sessions.list": {
    "title": "Todas las sesiones",
    "hint": "Sesiones de Claude, Codex y Copilot y ejecuciones del framework, la más reciente primero.",
    "more": [
      "Es una vista de solo lectura: este visor no modifica ni borra ninguna sesión.",
      "Solo se leen las carpetas que permitas con la opción gui_session_roots.",
      "Entra en una fila para ver la conversación completa y seguirla en vivo."
    ]
  },
  "sessions.refresh": {
    "title": "Actualizar la lista",
    "hint": "Vuelve a leer las sesiones del disco.",
    "more": [
      "Úsalo si empezaste una sesión hace un momento y no aparece.",
      "Mantiene los filtros que tengas puestos."
    ]
  },
  "sessions.source": {
    "title": "Origen de la sesión",
    "hint": "De qué herramienta viene la sesión.",
    "more": [
      "Claude, Codex y Copilot son las herramientas desde las que abriste cada sesión; Framework son las ejecuciones de este programa.",
      "«Todos» no filtra por origen.",
      "El filtro se aplica en cuanto eliges una opción."
    ]
  },
  "sessions.surface": {
    "title": "Superficie",
    "hint": "Dónde se usó: app de escritorio o terminal.",
    "more": [
      "«Escritorio» es la app de escritorio y «CLI» la terminal; «Framework» son las ejecuciones de este programa.",
      "«Desconocida» significa que la fuente no lo indica.",
      "Se combina con los demás filtros."
    ]
  },
  "sessions.text": {
    "title": "Título contiene",
    "hint": "Busca una palabra dentro del título de la sesión.",
    "more": [
      "Solo mira el título, no el contenido de la conversación, y no distingue mayúsculas de minúsculas.",
      "Se aplica un instante después de dejar de escribir.",
      "Se combina con los demás filtros."
    ]
  },
  "sessions.cwd": {
    "title": "Carpeta",
    "hint": "Muestra solo las sesiones de esta carpeta o de las que cuelgan de ella.",
    "more": [
      "Escribe una ruta completa, por ejemplo C:\\proyectos\\web.",
      "Aunque la escribas, solo se ven las carpetas permitidas con gui_session_roots.",
      "Las ejecuciones del framework no tienen carpeta, así que desaparecen con este filtro."
    ]
  },
  "sessions.since": {
    "title": "Actualizada desde",
    "hint": "Solo sesiones con actividad a partir de este día.",
    "more": [
      "Cuenta la fecha de la última actualización, no la de creación.",
      "El día elegido se incluye entero, desde las 00:00 de tu hora local.",
      "Puedes usar solo «desde», solo «hasta» o las dos."
    ]
  },
  "sessions.until": {
    "title": "Actualizada hasta",
    "hint": "Solo sesiones con actividad hasta este día.",
    "more": [
      "Cuenta la fecha de la última actualización, no la de creación.",
      "El día elegido se incluye entero, hasta las 23:59 de tu hora local.",
      "Puedes usar solo «desde», solo «hasta» o las dos."
    ]
  },
  "sessions.subagents": {
    "title": "Incluir subagentes",
    "hint": "Muestra también las sesiones que lanzó otra sesión.",
    "more": [
      "Un subagente es una sesión auxiliar que una sesión principal crea para una tarea concreta.",
      "Por defecto se ocultan para no llenar la lista; siguen accesibles desde su sesión principal.",
      "Cada fila de subagente lleva la etiqueta «Subagente»."
    ]
  },
  "sessions.search": {
    "title": "Buscar",
    "hint": "Aplica los filtros ahora.",
    "more": [
      "Los menús y las fechas se aplican al elegirlos y los textos, un instante después de escribir.",
      "Este botón lo aplica todo al momento y vuelve a leer la lista."
    ]
  },
  "sessions.clear": {
    "title": "Limpiar filtros",
    "hint": "Quita todos los filtros.",
    "more": [
      "Vuelve a mostrar todas las sesiones permitidas.",
      "No cambia la casilla de subagentes."
    ]
  },
  "sessions.warnings": {
    "title": "Avisos de las fuentes",
    "hint": "Problemas leves al leer alguna fuente de sesiones.",
    "more": [
      "Por ejemplo, una carpeta que no se pudo leer o líneas dañadas que se omitieron.",
      "Las demás sesiones se siguen mostrando con normalidad.",
      "Si falta alguna sesión, mira aquí primero."
    ]
  },
  "sessions.more": {
    "title": "Cargar más",
    "hint": "Trae la siguiente tanda de sesiones.",
    "more": [
      "La lista se lee por tandas de 50 para que abra rápido.",
      "El botón aparece solo si hay más sesiones que las mostradas.",
      "Los filtros siguen aplicados."
    ]
  },
  "sessions.retry": {
    "title": "Reintentar",
    "hint": "Vuelve a intentar leer la lista.",
    "more": [
      "Se usa cuando falló la lectura, por ejemplo si el servidor no respondió.",
      "Mantiene los filtros que tengas puestos."
    ]
  },
  "sessions.empty": {
    "title": "Sin sesiones visibles",
    "hint": "Por qué la lista puede estar vacía.",
    "more": [
      "El visor solo muestra las sesiones cuya carpeta esté dentro de las carpetas permitidas: la opción gui_session_roots (variable GUI_SESSION_ROOTS).",
      "Si esperabas ver alguna, comprueba que su carpeta de trabajo cuelga de una de esas rutas y reinicia la interfaz después de cambiarlas.",
      "Si tienes filtros puestos, prueba a limpiarlos."
    ]
  },
  "sessions.roots": {
    "title": "Carpetas permitidas",
    "hint": "Por privacidad, el visor no muestra nada hasta que lo permitas.",
    "more": [
      "La opción gui_session_roots (variable GUI_SESSION_ROOTS) lista las carpetas de trabajo cuyas sesiones se pueden ver.",
      "Se separan con «;» o se escriben como lista JSON; también sirve la misma línea en el archivo .env de la carpeta local.",
      "Después de cambiarla hay que volver a arrancar la interfaz."
    ]
  },

  "session.back": {
    "title": "Volver a la lista",
    "hint": "Cierra esta sesión y vuelve a la lista.",
    "more": [
      "Los filtros y las páginas que ya cargaste se conservan.",
      "También funciona la tecla Esc."
    ]
  },
  "session.run_link": {
    "title": "Ver la ejecución",
    "hint": "Abre esta ejecución del framework en la pantalla «Ejecuciones».",
    "more": [
      "Allí puedes aprobar, pedir cambios o cancelar, cosa que aquí no se puede.",
      "Esta pantalla solo muestra la conversación y los eventos de la ejecución."
    ]
  },
  "session.refresh": {
    "title": "Actualizar datos",
    "hint": "Vuelve a leer el resumen de la sesión.",
    "more": [
      "Refresca fechas, modelos, tokens y número de subagentes.",
      "El contenido de la conversación no se recarga: de eso se encarga el seguimiento en vivo."
    ]
  },
  "session.facts": {
    "title": "Datos de la sesión",
    "hint": "Dónde, cuándo y con qué modelos se hizo la sesión.",
    "more": [
      "La carpeta es el directorio de trabajo; las ejecuciones del framework no tienen.",
      "Las fechas son las de la propia sesión; la última actualización se muestra también en relativo, como «hace 5 minutos».",
      "Los tokens son las unidades que cobran los modelos: entrada (lo que enviaste), salida (lo que respondieron) y caché; si la fuente no los da, pone «desconocido», nunca 0.",
      "El identificador es el que usa este visor para enlazar la sesión."
    ]
  },
  "session.live": {
    "title": "Seguimiento en vivo",
    "hint": "Si la pantalla recibe lo nuevo de la sesión sin que recargues.",
    "more": [
      "Empieza cuando has cargado la sesión hasta el final con «Cargar más».",
      "Entonces añade al final cada mensaje nuevo en cuanto aparece.",
      "Si se pierde la conexión, se reintenta sola."
    ]
  },
  "session.pause": {
    "title": "Pausar o reanudar el seguimiento",
    "hint": "Detiene o reanuda la llegada de mensajes nuevos.",
    "more": [
      "Al pausar se cierra la conexión en vivo y los mensajes nuevos no aparecen hasta reanudar.",
      "Al reanudar, continúa desde el último mensaje recibido, sin perder ninguno."
    ]
  },
  "session.retry": {
    "title": "Reintentar el seguimiento",
    "hint": "Vuelve a conectar el seguimiento en vivo.",
    "more": [
      "Aparece cuando el seguimiento terminó o falló.",
      "Continúa desde el último mensaje recibido."
    ]
  },
  "session.warnings": {
    "title": "Avisos de lectura",
    "hint": "Cosas que el visor no pudo leer bien de esta sesión.",
    "more": [
      "Por ejemplo, líneas dañadas o demasiado grandes que se omitieron, o un archivo que cambió y se volvió a leer.",
      "El resto de la sesión se muestra con normalidad.",
      "Si hay más de tres avisos, el desplegable empieza cerrado."
    ]
  },
  "session.items": {
    "title": "Contenido de la sesión",
    "hint": "La conversación, en orden, tal como la guardó la herramienta.",
    "more": [
      "Hay mensajes del usuario y del asistente, llamadas a herramientas con su resultado, avisos del sistema y subagentes.",
      "Todo se muestra como texto plano y los resultados largos empiezan plegados.",
      "Los mensajes nuevos llegan al final mientras el seguimiento en vivo está activo."
    ]
  },
  "session.reasoning": {
    "title": "Razonamiento omitido",
    "hint": "Marca dónde el modelo razonó, pero el visor no lo muestra.",
    "more": [
      "Los modelos pueden razonar antes de responder; ese texto interno se guarda en la sesión, pero este visor nunca lo enseña.",
      "Por eso solo ves el aviso, sin su contenido; no es un fallo de lectura."
    ]
  },
  "session.more": {
    "title": "Cargar más",
    "hint": "Trae la siguiente parte de la conversación.",
    "more": [
      "Las sesiones largas se leen por partes de 200 elementos para que abran rápido.",
      "Cuando llegas al final se activa el seguimiento en vivo."
    ]
  },
  "session.items_retry": {
    "title": "Reintentar la lectura",
    "hint": "Vuelve a intentar leer el contenido.",
    "more": [
      "Se reintenta desde donde falló.",
      "Si sigue fallando, comprueba que el archivo de la sesión sigue en su sitio y que el servidor está activo."
    ]
  },

  "open.claude": {
    "title": "Abrir en Claude",
    "hint": "Abre esta sesión en la app de escritorio de Claude.",
    "more": [
      "Lanza una consola nueva de Windows con «claude --desktop --resume» y el identificador de la sesión; antes te pide confirmar.",
      "La consola se abre en tu carpeta personal, nunca en la de la sesión, por seguridad.",
      "Claude muestra lo nuevo solo después de cerrar y volver a abrir la app.",
      "Si el lanzamiento está desactivado (opción gui_allow_launch) o no usas Windows, copia el comando y ejecútalo tú."
    ]
  },
  "open.codex": {
    "title": "Abrir en Codex",
    "hint": "Abre este hilo en la app de Codex mediante un enlace.",
    "more": [
      "El botón es un enlace que abre la app de Codex ya instalada; no lanza ningún programa desde aquí.",
      "Si hace falta, Codex pide confirmación antes de abrir la sesión.",
      "Mientras la app tiene el hilo abierto bloquea las escrituras, pero este visor solo lee y no le afecta."
    ]
  },
  "usage.section": {
    "title": "Uso",
    "hint": "Consulta el consumo medido de planes, modelos y llamadas del framework.",
    "more": [
      "Las cifras vienen de archivos locales y de la telemetría del framework.",
      "Cada fuente puede medir ventanas distintas o no tener datos disponibles."
    ]
  },
  "usage.plans": {
    "title": "Planes por aplicación",
    "hint": "Muestra el porcentaje y el reinicio solo cuando la fuente los conoce.",
    "more": [
      "Un porcentaje ausente significa que la fuente no permite calcularlo.",
      "Si la ventana ya se reinició, el uso actual queda desconocido y no se reemplaza por cero."
    ]
  },
  "usage.global": {
    "title": "Consumo global",
    "hint": "Agrupa tokens por aplicación, modelo, nivel y tipo de agente.",
    "more": [
      "Las filas reúnen los eventos que cada aplicación dejó disponibles.",
      "Un dato ausente se conserva como desconocido y no se cuenta como cero."
    ]
  },
  "usage.period": {
    "title": "Intervalo del consumo",
    "hint": "Cambia entre el día de hoy y los últimos siete días.",
    "more": [
      "Las fechas se muestran en la zona horaria de tu navegador.",
      "Las filas y los totales corresponden al intervalo seleccionado."
    ]
  },
  "usage.costs": {
    "title": "API y presupuesto",
    "hint": "Muestra costos estimados con su evidencia y separa los importes desconocidos.",
    "more": [
      "El costo se calcula con los tokens observados y los precios disponibles.",
      "No es un importe facturado; una llamada sin precio vigente queda como desconocida."
    ]
  },
  "usage.refresh": {
    "title": "Actualizar uso",
    "hint": "Vuelve a consultar los planes, los tokens y los costos.",
    "more": [
      "La pantalla conserva la última respuesta si una fuente falla.",
      "Las aplicaciones pueden actualizar sus archivos en momentos distintos."
    ]
  },
  "usage.view": {
    "title": "Secciones de uso",
    "hint": "Elige planes, consumo global o costos de la API.",
    "more": [
      "Cada sección muestra la información que su fuente local puede medir.",
      "Usa las flechas del teclado para recorrer estas pestañas."
    ]
  },
  "usage.plan_notes": {
    "title": "Notas de la fuente",
    "hint": "Aclaran qué datos pudo leer cada aplicación.",
    "more": [
      "Las notas describen límites o avisos de la medición local.",
      "Un dato ausente no significa que el consumo sea cero."
    ]
  },
  "usage.cost_reasons": {
    "title": "Importes desconocidos",
    "hint": "Explica por qué algunas llamadas no tienen costo calculado.",
    "more": [
      "Puede faltar el precio vigente, el conteo de tokens o tratarse de un paso humano.",
      "Estos registros se separan del subtotal con precio conocido."
    ]
  },
  "usage.cost_evidence": {
    "title": "Evidencia de precios",
    "hint": "Muestra el origen del precio usado en la estimación.",
    "more": [
      "La evidencia identifica el modelo, la moneda y la fuente del precio.",
      "El subtotal sigue siendo una estimación y no un importe facturado."
    ]
  },
  "evaluation.section": {
    "title": "Evaluación",
    "hint": "Consulta los conjuntos de evaluación y si su última ejecución empeoró.",
    "more": [
      "La vista solo lee ejecuciones ya guardadas; no ejecuta conjuntos ni fija líneas base.",
      "Los conjuntos se ejecutan y se fijan desde la línea de comandos o la API."
    ]
  },
  "evaluation.refresh": {
    "title": "Actualizar la evaluación",
    "hint": "Vuelve a leer los conjuntos o el conjunto abierto.",
    "more": [
      "La pantalla conserva la última respuesta si la lectura falla.",
      "Una ejecución nueva aparece al actualizar."
    ]
  },
  "evaluation.sets": {
    "title": "Conjuntos de evaluación",
    "hint": "Muestra la última puntuación de cada conjunto y su estado frente a la línea base.",
    "more": [
      "Mock significa que se ejecutó con respuestas simuladas; En vivo, con un modelo real.",
      "Regresión indica que la última ejecución empeoró respecto de la línea base; Sin línea base, que aún no hay con qué comparar.",
      "Abre un conjunto con su nombre para ver sus ejecuciones y la comparación."
    ]
  },
  "evaluation.runs": {
    "title": "Ejecuciones guardadas",
    "hint": "Lista las ejecuciones del conjunto, la más reciente primero.",
    "more": [
      "Cada fila muestra la versión del conjunto, la puntuación, quién la guardó y si es la línea base.",
      "Solo se listan las ejecuciones guardadas en la base de datos del sistema."
    ]
  },
  "evaluation.compare": {
    "title": "Comparación con la línea base",
    "hint": "Compara la última ejecución con la que se fijó como línea base.",
    "more": [
      "Un caso es regresión si pasaba y dejó de pasar, o si su puntuación bajó más que el umbral.",
      "Una regresión es un resultado de la comparación, no un error."
    ]
  },
  "evaluation.cases": {
    "title": "Casos comparados",
    "hint": "Clasifica cada caso como regresión, mejora, cambio o sin cambios.",
    "more": [
      "Cambió significa que difieren sus comprobaciones o su huella sin cruzar el umbral.",
      "Los casos que solo están en una de las dos ejecuciones se listan aparte y no cuentan como regresión."
    ]
  },
  "evaluation.fingerprint": {
    "title": "Cambios en la huella",
    "hint": "Indica qué difiere entre la línea base y la última ejecución.",
    "more": [
      "La huella recoge el agente o prompt, el flujo, los modelos con sus proveedores y el propio conjunto.",
      "Sirve para saber qué cambió cuando un caso empeora."
    ]
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
  "open.copilot": {
    "title": "Abrir en Copilot",
    "hint": "Abre esta sesión en la app de Copilot mediante un enlace.",
    "more": [
      "El botón es un enlace que abre la app de Copilot ya instalada; no lanza ningún programa desde aquí.",
      "Si hace falta, Copilot pide confirmación antes de abrir la sesión.",
      "Copilot muestra lo nuevo de la sesión solo después de cerrar y volver a abrir la app."
    ]
  },
  "open.copy": {
    "title": "Copiar comando",
    "hint": "Copia el comando para ejecutarlo tú en una terminal.",
    "more": [
      "Se usa cuando el visor no puede lanzar la app por sí mismo.",
      "Ejecútalo dentro de la carpeta que se indica junto al comando, si aparece."
    ]
  },
  "subagents.panel": {
    "title": "Subagentes de la sesión",
    "hint": "Los agentes auxiliares que trabajan o trabajaron para esta sesión.",
    "more": [
      "Reúne los subagentes de Claude, los hilos hijos de Codex, los subagentes de Copilot y los pasos de una ejecución del framework.",
      "De cada uno ves su nombre o descripción, el modelo, el estado, cuándo tuvo actividad y los tokens que usó.",
      "La lista se actualiza sola; si se pierde la conexión en vivo, se vuelve a leer cada diez segundos."
    ]
  },
  "subagents.refresh": {
    "title": "Actualizar subagentes",
    "hint": "Vuelve a leer la lista de subagentes ahora mismo.",
    "more": [
      "La lista ya se actualiza sola; el botón sirve para forzar la lectura sin esperar.",
      "No cambia nada en las apps: solo lee sus archivos."
    ]
  },
  "subagents.retry": {
    "title": "Reintentar la lectura de subagentes",
    "hint": "Vuelve a pedir la lista cuando falló.",
    "more": [
      "Este botón solo aparece si no se pudo leer la lista de subagentes.",
      "Si el fallo continúa, comprueba que el servidor local sigue en marcha y que la sesión está en una carpeta permitida."
    ]
  },
  "subagents.back": {
    "title": "Volver a la sesión principal",
    "hint": "Regresa a la conversación de la sesión que lanzó este subagente.",
    "more": [
      "Mientras ves la conversación de un subagente, la lista sigue mostrando a sus hermanos.",
      "Este enlace te devuelve a la conversación principal."
    ]
  },
  "subagents.tabs": {
    "title": "Subagentes activos y terminados",
    "hint": "Cambia entre los subagentes en marcha y los que ya acabaron.",
    "more": [
      "«Activos» son los que trabajan, ejecutan una prueba o llevan menos de una hora sin actividad.",
      "«Terminados» son los que acabaron, los que llevan más de una hora detenidos y los de estado desconocido.",
      "Con las flechas del teclado pasas de una pestaña a otra."
    ]
  },
  "subagents.item": {
    "title": "Abrir un subagente",
    "hint": "Pulsa uno para ver su conversación completa.",
    "more": [
      "La conversación se abre en esta misma pantalla y la lista sigue a la vista.",
      "Los pasos de una ejecución del framework abren la ejecución que los contiene.",
      "Los subagentes sin conversación propia no son enlaces: su actividad solo aparece como avisos dentro de la sesión."
    ]
  },
  "subagents.more": {
    "title": "Ver más subagentes",
    "hint": "Muestra el resto de la pestaña o vuelve a plegarla.",
    "more": [
      "La lista enseña primero los diez con actividad más reciente.",
      "El botón despliega los demás y, una vez abierta la lista, vuelve a dejar solo los diez primeros."
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
  "shell.menu": {
    "title": "Menú de sesiones",
    "hint": "Abre o cierra la lista de sesiones en pantallas estrechas.",
    "more": [
      "En pantallas estrechas la lista de sesiones se oculta para dejar sitio al contenido.",
      "Este botón la muestra como un cajón sobre la pantalla y vuelve a ocultarla.",
      "También se cierra con la tecla Esc o al elegir un elemento de la lista."
    ]
  },
  "shell.help_mode": {
    "title": "Modo ayuda",
    "hint": "Muestra u oculta los «?» y las pistas de cada control.",
    "more": [
      "Apagado, solo se ven los «?» de los títulos y de las aprobaciones; la ayuda sigue disponible para los lectores de pantalla.",
      "También se cambia con la tecla F1.",
      "Este navegador recuerda el modo que dejaste."
    ]
  },
  "shell.panel": {
    "title": "Panel de contexto",
    "hint": "Muestra u oculta el panel de la derecha.",
    "more": [
      "El panel reúne los cambios, los subagentes, los tokens y las fuentes de la sesión abierta.",
      "Este navegador recuerda si lo dejaste abierto o cerrado.",
      "En pantallas estrechas aparece debajo del contenido."
    ]
  },
  "shell.new_session": {
    "title": "Nueva sesión",
    "hint": "Empieza un trabajo nuevo con un chat guiado.",
    "more": [
      "Describes lo que necesitas o eliges una opción, y el chat te pregunta las entradas del workflow de una en una.",
      "No se ejecuta nada hasta que pulsas «Lanzar» en el resumen final.",
      "Si prefieres el formulario completo, lo abres desde el propio chat con «Ver como formulario»."
    ]
  },
  "chat.welcome": {
    "title": "Chat de nueva sesión",
    "hint": "Aquí empiezas un trabajo: escribes lo que necesitas o eliges una opción.",
    "more": [
      "El chat propone workflows, te pregunta las entradas una a una y al final muestra un resumen.",
      "Un modelo puede interpretar tu texto y proponer un workflow; si es de nube, el chat pide confirmación antes de enviarlo.",
      "Si no lo envías, el chat compara las palabras con los nombres y descripciones de los workflows.",
      "La ejecución solo se crea cuando pulsas «Lanzar»."
    ]
  },
  "chat.starters": {
    "title": "Opciones de inicio",
    "hint": "Atajos para empezar sin escribir.",
    "more": [
      "«Trabajar en un workflow» muestra los workflows disponibles.",
      "«Continuar una ejecución» lista las que puedes retomar.",
      "«Ayuda» explica cómo funciona el chat y sus comandos."
    ]
  },
  "chat.composer": {
    "title": "Caja de escritura",
    "hint": "Escribe aquí tu petición o tu respuesta; Intro la envía.",
    "more": [
      "Intro envía el mensaje y Mayús+Intro añade una línea nueva.",
      "Puedes escribir los comandos /workflow, /continuar y /ayuda.",
      "Durante las preguntas, lo que escribes es la respuesta a la entrada que se muestra."
    ]
  },
  "chat.send": {
    "title": "Enviar mensaje",
    "hint": "Envía lo que escribiste en la caja.",
    "more": [
      "Hace lo mismo que pulsar Intro dentro de la caja.",
      "En una pregunta opcional, enviar la caja vacía equivale a omitirla."
    ]
  },
  "chat.cloud_confirm": {
    "title": "Enviar a la nube",
    "hint": "Permite enviar este texto al modelo de nube indicado para proponer un workflow.",
    "more": [
      "El texto y el catálogo de workflows se envían al modelo que aparece en la pregunta.",
      "Si eliges «No enviar», el chat compara las palabras sin llamar al modelo.",
      "Cada llamada cuenta en el uso y respeta los topes de presupuesto."
    ]
  },
  "chat.skip": {
    "title": "Omitir pregunta",
    "hint": "Deja vacía esta entrada opcional y pasa a la siguiente.",
    "more": [
      "Solo aparece en las entradas opcionales.",
      "Su hueco queda en blanco en el prompt.",
      "Con «Editar» en el resumen puedes volver a rellenarla."
    ]
  },
  "chat.back": {
    "title": "Pregunta anterior",
    "hint": "Vuelve a la pregunta anterior sin perder lo respondido.",
    "more": [
      "Tu respuesta anterior aparece en la caja para que la corrijas.",
      "Volver atrás no ejecuta nada."
    ]
  },
  "chat.restart": {
    "title": "Empezar de nuevo",
    "hint": "Descarta esta conversación y vuelve al inicio.",
    "more": [
      "Se borran las respuestas dadas hasta ahora.",
      "No afecta a ninguna ejecución que ya hayas creado."
    ]
  },
  "chat.form": {
    "title": "Ver como formulario",
    "hint": "Pasa al formulario completo con lo que ya respondiste.",
    "more": [
      "El formulario tiene los mismos campos y además los ajustes avanzados.",
      "Lo que respondiste en el chat aparece ya escrito en él.",
      "Si vuelves al chat, los cambios hechos en el formulario no se copian de vuelta."
    ]
  },
  "chat.suggestion": {
    "title": "Elegir un workflow",
    "hint": "Pulsa una tarjeta para trabajar con ese workflow.",
    "more": [
      "Cada tarjeta muestra el nombre, la descripción y el origen del workflow.",
      "Al elegir uno empiezan las preguntas; todavía no se ejecuta nada.",
      "Si ninguno encaja, escribe otras palabras o usa «Ver todos los workflows»."
    ]
  },
  "chat.all": {
    "title": "Ver todos los workflows",
    "hint": "Muestra la lista completa, sin filtrar por tu texto.",
    "more": [
      "Úsalo cuando ninguna propuesta encaje.",
      "Las tarjetas son las mismas, ordenadas por origen y nombre."
    ]
  },
  "chat.choice": {
    "title": "Elegir una opción",
    "hint": "Pulsa la opción que quieres como respuesta.",
    "more": [
      "Esta entrada solo admite las opciones mostradas.",
      "También puedes escribir el nombre de la opción en la caja."
    ]
  },
  "chat.runs": {
    "title": "Ejecuciones para continuar",
    "hint": "Pulsa una para abrir su hilo y seguir donde quedó.",
    "more": [
      "Primero aparecen las que esperan una acción tuya o se interrumpieron.",
      "Si no hay ninguna, se muestran las más recientes.",
      "Abrir el hilo no modifica la ejecución."
    ]
  },
  "chat.question": {
    "title": "Pregunta del chat",
    "hint": "Cada pregunta corresponde a una entrada del workflow.",
    "more": [
      "Ves su nombre, para qué sirve y un ejemplo.",
      "Responde en la caja de abajo.",
      "Puedes volver a la pregunta anterior y, si la entrada es opcional, omitirla."
    ]
  },
  "chat.confirm": {
    "title": "Resumen antes de lanzar",
    "hint": "Revisa tus respuestas y el prompt antes de crear la ejecución.",
    "more": [
      "La vista previa es el texto que recibirá cada paso con tus respuestas.",
      "No se crea ninguna ejecución hasta que pulses «Lanzar».",
      "Con «Editar» vuelves a las preguntas y con «Ver como formulario» pasas al formulario."
    ]
  },
  "chat.routing": {
    "title": "Ajustes de ejecución",
    "hint": "Configura el modo y las preferencias de cada paso antes de lanzar.",
    "more": [
      "Puedes elegir por paso el modo supervisado, automático o único, activar el perfil ECO y ajustar el nivel, modelo y esfuerzo.",
      "Estas preferencias se guardan al crear la ejecución y se validan con el catálogo ECO, las reglas de privacidad, los límites de nivel y el presupuesto.",
      "Los chips muestran preferencias, no garantizan qué modelo se usará efectivamente; la vista previa solo muestra el prompt y no ejecuta nada."
    ]
  },
  "chat.session_mode": {
    "title": "Tipo de sesión",
    "hint": "Elige Orquestador o Sesión única antes de empezar.",
    "more": [
      "Orquestador conserva las preguntas del workflow, el equipo y el plan actuales.",
      "Sesión única permite elegir un modelo del paquete general o un ejecutor conectado.",
      "Puedes volver a elegir el modo antes de lanzar."
    ]
  },
  "chat.connect_other": {
    "title": "Conectar otro",
    "hint": "Abre «Apps y agentes» para ver qué le falta a cada app o modelo.",
    "more": [
      "Allí cada app y cada modelo muestra solo el paso que le falta: instalarlo, iniciar sesión, configurar la clave o habilitarlo.",
      "Este enlace solo cambia de pantalla: no instala, no inicia sesión y no habilita nada."
    ]
  },
  "chat.single_model": {
    "title": "Modelo para la tarea",
    "hint": "Elige un modelo admitido y conectado.",
    "more": [
      "Solo aparecen modelos del catálogo del paquete general cuyo proveedor está conectado.",
      "Si un proveedor no tiene un modelo admitido, se muestra el motivo.",
      "El servidor valida de nuevo la elección al revisar y lanzar."
    ]
  },
  "chat.single_task": {
    "title": "Tarea de sesión única",
    "hint": "Escribe una tarea de texto libre y revísala antes de lanzar.",
    "more": [
      "La tarea pasa al único paso del workflow ask_once.",
      "Revisar muestra el prompt y comprueba el modelo sin ejecutar nada.",
      "Lanzar crea la ejecución y abre su hilo."
    ]
  },
  "chat.model_picker": {
    "title": "Modelos y ejecutores conectados",
    "hint": "Elige una opción comprobada para la próxima sesión única.",
    "more": [
      "La lista muestra cuándo se comprobó cada conexión y la cuota de cada ejecutor.",
      "Esta elección no lanza nada ni cambia el enrutamiento del workflow actual."
    ]
  },
  "chat.model_choice": {
    "title": "Elegir modelo o ejecutor",
    "hint": "Solo se pueden elegir opciones habilitadas y conectadas.",
    "more": [
      "Las opciones sin conexión aparecen plegadas con el motivo y el paso pendiente.",
      "Antigravity usa tu suscripción y pide aprobación en cada lanzamiento."
    ]
  },
  "chat.model_disconnected": {
    "title": "Opciones no conectadas",
    "hint": "Muestra las opciones que todavía no se pueden elegir.",
    "more": [
      "Cada opción indica el paso pendiente o el motivo que la bloquea.",
      "Vuelve a comprobarla desde Apps y agentes después de completar ese paso."
    ]
  },
  "chat.model_retry": {
    "title": "Reintentar conexiones",
    "hint": "Vuelve a leer el estado de conexión que guarda el servidor.",
    "more": [
      "No inicia ninguna sesión ni ejecuta un modelo.",
      "Si sigue fallando, comprueba que el servidor local está disponible."
    ]
  },
  "chat.launch": {
    "title": "Lanzar",
    "hint": "Crea la ejecución con estas respuestas.",
    "more": [
      "Es el único botón del chat que ejecuta algo.",
      "Si el servidor no está en modo Mock, antes debes confirmar que puede usar modelos reales.",
      "Al crearla, el chat sigue el avance de la ejecución."
    ]
  },
  "chat.edit": {
    "title": "Editar respuestas",
    "hint": "Vuelve a las preguntas con tus respuestas ya escritas.",
    "more": [
      "Cada pregunta aparece con tu respuesta anterior en la caja.",
      "Pulsa Intro para conservarla o escribe otra."
    ]
  },
  "chat.retry": {
    "title": "Reintentar la lista de workflows",
    "hint": "Vuelve a pedir la lista cuando falló.",
    "more": [
      "Este botón solo aparece si no se pudo leer la lista de workflows.",
      "Si el fallo continúa, abre el formulario con «Ver como formulario»."
    ]
  },
  "thread.section": {
    "title": "Hilo de la ejecución",
    "hint": "Muestra los pasos y las decisiones de esta ejecución.",
    "more": ["El hilo se actualiza cada pocos segundos mientras esta vista está abierta.", "Las decisiones aparecen en la parada actual."]
  },
  "thread.refresh": {
    "title": "Actualizar hilo",
    "hint": "Vuelve a leer todos los mensajes de esta ejecución.",
    "more": ["Úsalo si quieres comprobar el estado ahora.", "No cambia la ejecución."]
  },
  "thread.detail": {
    "title": "Ver detalle de la ejecución",
    "hint": "Abre el detalle completo en Ejecuciones.",
    "more": ["Allí puedes revisar resultados y acciones que este hilo no muestra.", "El enlace conserva la ejecución elegida."]
  },
  "thread.related": {
    "title": "Abrir elemento relacionado",
    "hint": "Abre el plan o equipo vinculado a la ejecución.",
    "more": ["El enlace aparece cuando la ejecución pertenece a una tarea o usa un equipo.", "Abrirlo no modifica la ejecución."]
  },
  "thread.approve": {
    "title": "Aprobar la parada",
    "hint": "Confirma el resultado o la llamada pendiente.",
    "more": ["Lee el detalle antes de decidir.", "La aprobación se envía solo después de confirmarla."]
  },
  "thread.changes": {
    "title": "Pedir cambios",
    "hint": "Devuelve el paso con indicaciones o comentarios.",
    "more": ["Escribe una indicación o añade un comentario.", "Confirma antes de enviarlos."]
  },
  "thread.reject": {
    "title": "Rechazar la ejecución",
    "hint": "Termina esta ejecución después de confirmar.",
    "more": ["Puedes escribir un motivo.", "Rechazar no se puede deshacer."]
  },
  "thread.feedback": {
    "title": "Indicación de la decisión",
    "hint": "Escribe qué debe cambiar o por qué rechazas.",
    "more": ["Para pedir cambios necesitas una indicación o un comentario.", "El motivo del rechazo es opcional."]
  },
  "thread.comment": {
    "title": "Comentario de revisión",
    "hint": "Señala una parte, el tipo de cambio y su texto.",
    "more": ["Puedes añadir varios comentarios.", "La cita de la salida es opcional."]
  },
  "thread.confirm": {
    "title": "Confirmar decisión",
    "hint": "Envía la decisión o vuelve sin cambiar la ejecución.",
    "more": ["Comprueba el resultado y las indicaciones antes de enviar.", "El hilo se vuelve a leer tras la acción."]
  },
  "thread.output": {
    "title": "Salida para revisar",
    "hint": "Es la salida guardada del intento que espera tu decisión.",
    "more": ["Los campos aparecen como lista y las listas de objetos como tablas.", "El texto y el JSON completo se muestran como texto plano."]
  },
  "thread.json": {
    "title": "JSON completo",
    "hint": "Abre la salida original con todos sus campos.",
    "more": ["Está plegada para que puedas leer primero el resumen.", "No interpreta Markdown ni HTML."]
  },
  "thread.previous": {
    "title": "Ver pasos anteriores",
    "hint": "Carga las salidas guardadas de los pasos anteriores de esta ejecución.",
    "more": ["Se leen solo cuando pulsas el botón.", "Cada intento se puede abrir por separado."]
  },
  "thread.calls": {
    "title": "Llamadas a modelo",
    "hint": "Muestra las llamadas usadas y el máximo de esta ejecución.",
    "more": ["El número ayuda a decidir si pedir cambios.", "No cambia el presupuesto del workflow."]
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
  "shell.search": {
    "title": "Buscar en la lista",
    "hint": "Filtra la lista de la izquierda mientras escribes.",
    "more": [
      "Busca en el título, el proyecto, la aplicación y el estado de cada elemento.",
      "No distingue mayúsculas ni acentos, y pide que aparezcan todas las palabras escritas.",
      "Solo filtra lo que ya está en la lista: no busca dentro de las conversaciones."
    ]
  },
  "shell.activities": {
    "title": "Actividades",
    "hint": "Muestra en la lista las actividades abiertas de tus proyectos, en lugar de las sesiones y ejecuciones.",
    "more": [
      "Cada actividad lleva su estado y quién trabaja en ella; al elegirla se abre el tablero de su proyecto.",
      "Las actividades no cuentan en el avance del proyecto.",
      "Vuelve a pulsar el botón para ver otra vez las sesiones y ejecuciones."
    ]
  },
  "panel.changes": {
    "title": "Cambios",
    "hint": "Archivos que la sesión o la ejecución abierta ha modificado.",
    "more": [
      "En una sesión salen de las herramientas que escriben o editan archivos; en una ejecución, de los archivos que listan sus resultados.",
      "Cada archivo muestra cuántas veces se tocó y, si la fuente lo cuenta, las líneas añadidas (+) y quitadas (−).",
      "Muchas apps no registran esas líneas: en ese caso el recuento no se inventa y solo se ve el número de ediciones.",
      "Solo se muestran rutas, nunca el contenido de los mensajes."
    ]
  },
  "panel.tokens": {
    "title": "Tokens",
    "hint": "Cuánto texto procesaron los modelos y cuánto cuesta, por estimación.",
    "more": [
      "Un token es un trozo de texto que el modelo lee o escribe; cuantos más hay, más cuesta la sesión.",
      "El orquestador es la sesión abierta; los trabajadores son sus subagentes o los pasos de una ejecución.",
      "Un dato que no se conoce se escribe «desconocido», nunca 0; el costo solo se estima con un precio vigente del catálogo.",
      "Las apps no informan precios, así que el costo de sus sesiones queda desconocido."
    ]
  },
  "panel.sources": {
    "title": "Fuentes",
    "hint": "Los archivos, las páginas y las referencias que usó la sesión o la ejecución.",
    "more": [
      "En una sesión: su carpeta de trabajo, los archivos y carpetas que consultó, las páginas web que abrió y cuántas búsquedas y adjuntos tuvo.",
      "En una ejecución: el workflow, los prompts y los contratos de sus pasos y los nombres de sus entradas, nunca sus valores.",
      "Las direcciones web se muestran sin lo que sigue a «?», porque ahí pueden ir claves."
    ]
  },
  "panel.retry": {
    "title": "Reintentar la lectura",
    "hint": "Vuelve a pedir los datos de este apartado cuando falló.",
    "more": [
      "Este botón solo aparece si no se pudieron leer los datos del apartado.",
      "Si el fallo continúa, comprueba que el servidor local sigue en marcha."
    ]
  },
  "panel.more": {
    "title": "Ver más o menos",
    "hint": "Muestra el resto de la lista o la vuelve a plegar.",
    "more": [
      "La lista enseña primero los ocho elementos más recientes.",
      "El botón despliega los demás y, una vez abierta la lista, vuelve a dejar solo los ocho primeros."
    ]
  },
  "panel.by_model": {
    "title": "Tokens por modelo",
    "hint": "Reparte los tokens entre los modelos que trabajaron.",
    "more": [
      "Agrupa a los trabajadores por modelo y nivel, con las llamadas o subagentes que tuvo cada uno.",
      "Los que no informaron tokens se cuentan aparte, sin sumarlos como cero."
    ]
  },
  "panel.cost_evidence": {
    "title": "Evidencia de precio",
    "hint": "El precio por token con el que se calculó el costo.",
    "more": [
      "Para cada modelo se ve el precio de entrada y de salida, su fuente, desde y hasta cuándo vale y de dónde viene.",
      "Si una llamada no tiene un precio vigente, su costo queda desconocido y no se suma.",
      "El costo es una estimación: tokens por precio del catálogo, no un importe facturado."
    ]
  },
  "pending.toggle": {
    "title": "Pendientes",
    "hint": "Muestra lo que espera una decisión o una revisión.",
    "more": ["La lista reúne ejecuciones, propuestas y tareas que esperan a una persona.",
      "Se actualiza automáticamente mientras la pestaña está abierta."]
  },
  "pending.open": {
    "title": "Abrir pendiente",
    "hint": "Abre la vista relacionada con este elemento.",
    "more": ["El enlace solo cambia de vista.", "No aprueba, rechaza ni inicia ningún trabajo."]
  },
  "pending.refresh": {
    "title": "Actualizar pendientes",
    "hint": "Vuelve a consultar la lista en el servidor local.",
    "more": ["La consulta es de solo lectura.", "Usa el token local de la GUI."]
  },
  "pending.notifications": {
    "title": "Avisos del navegador",
    "hint": "Activa o desactiva los avisos de pendientes nuevos en este navegador.",
    "more": ["El permiso solo se solicita al pulsar este botón.", "Los avisos son opcionales y solo se muestran si el navegador concede permiso."]
  },
  "pending.close": {
    "title": "Cerrar pendientes",
    "hint": "Oculta la lista sin cambiar ningún pendiente.",
    "more": ["Ningún pendiente cambia al cerrarla.", "Vuelve a abrirla desde el botón Pendientes de la barra superior."]
  },
  "inbox.section": {
    "title": "Mensaje a la ejecución", "hint": "Escribe una instrucción para el siguiente paso de trabajo.",
    "more": ["La bandeja solo funciona cuando la política de disponibilidad está encendida.", "El mensaje se lee en una frontera de trabajo y nunca aprueba una decisión."]
  },
  "inbox.kind": {
    "title": "Acción del mensaje", "hint": "Elige cancelar, interrumpir, ajustar o dejar una nota.",
    "more": ["Cancelar detiene la ejecución cuando el trabajador lea el mensaje.", "Interrumpir y ajustar esperan tu decisión; una nota deja constancia y permite continuar."]
  },
  "inbox.text": {
    "title": "Texto del mensaje", "hint": "Explica tu ajuste o deja una nota.",
    "more": ["El texto es obligatorio para ajustar y para una nota.", "La bandeja admite hasta 1200 bytes de texto."]
  },
  "inbox.send": {
    "title": "Enviar mensaje", "hint": "Guarda el mensaje para que lo lea el trabajador.",
    "more": ["Cancelar, interrumpir y ajustar requieren una confirmación adicional.", "El botón queda desactivado si la política está apagada o la ejecución terminó."]
  },
  "inbox.confirm": {
    "title": "Confirmar mensaje", "hint": "Confirma la instrucción que vas a guardar.",
    "more": ["El mensaje se guarda con un identificador propio para evitar duplicados.", "Se aplicará cuando el trabajador llegue a la siguiente frontera de trabajo."]
  },
  "inbox.back": {
    "title": "Volver al mensaje", "hint": "Cierra la confirmación y conserva el texto.",
    "more": ["Puedes cambiar la acción o el texto antes de enviarlo.", "La ejecución sigue en su estado actual."]
  },
  "pending.background": {
    "title": "En fondo", "hint": "Muestra ejecuciones activas y en cola.",
    "more": ["La lista se actualiza junto con Pendientes.", "Cada enlace abre el hilo de la ejecución correspondiente."]
  },
  "pending.cancel": {
    "title": "Pedir cancelación", "hint": "Prepara un mensaje para cancelar esta ejecución.",
    "more": ["Antes de guardarlo se pide confirmación.", "La acción solo está disponible con la política de disponibilidad encendida."]
  },
  "pending.cancel_confirm": {
    "title": "Confirmar cancelación", "hint": "Guarda el mensaje de cancelación.",
    "more": ["La ejecución se detendrá cuando el trabajador lea el mensaje.", "Esta acción no aprueba ninguna revisión pendiente."]
  },
  "pending.cancel_back": {
    "title": "Volver a En fondo", "hint": "Cierra la confirmación sin enviar.",
    "more": ["La ejecución sigue en su estado actual.", "Puedes abrirla desde su enlace para ver el hilo."]
  },
  "human.tool_preview": {
    "title": "Vista previa de file.write",
    "hint": "Muestra el contenido que se propone escribir antes de aprobar la llamada.",
    "more": [
      "Si el archivo existe, también compara el contenido actual con el propuesto y avisa si se quitarán líneas.",
      "La vista usa los argumentos reconstruidos del workflow y solo los muestra si coinciden con la huella de la aprobación pendiente.",
      "Si no coinciden o la ruta no cumple los límites, explica por qué no puede mostrar el contenido."
    ]
  },
  "chat.executor_launch": {
    "title": "Lanzar con un ejecutor",
    "hint": "Revisa ejecutor, modelo, esfuerzo y cuota antes de aprobar.",
    "more": [
      "La tarea y la selección no inician nada; primero se muestra una confirmación con los ajustes y la cuota restante.",
      "Solo «Aprobar y lanzar» envía la confirmación explícita al servidor.",
      "Antigravity muestra su aviso y respeta el interruptor, el uso de créditos desactivado y sus pisos de cuota.",
      "El seguimiento consulta el estado de la tarea y enlaza su sesión en Sesiones."
    ]
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
  "chat.limit_notice": {
    "title": "Límite alcanzado",
    "hint": "Aviso cuando un modelo o ejecutor agota su cuota o ritmo.",
    "more": [
      "Muestra «vuelve a las HH:MM» en tu hora local si hay dato, o «sin hora conocida».",
      "Nunca cambia de modelo ni de ejecutor por su cuenta en un mensaje nuevo.",
      "Las sugerencias disponibles aparecen como botones para que elijas tú."
    ]
  },
  "chat.limit_suggestion": {
    "title": "Sugerencia alternativa",
    "hint": "Cambia la selección a otra opción disponible sin enviar nada.",
    "more": [
      "Conserva el texto que ya escribiste en la caja.",
      "Solo cambia el modelo o ejecutor elegido; enviar o lanzar sigue siendo tuyo."
    ]
  },
  "chat.connection_checks": {
    "title": "Estado de Gemini y Copilot",
    "hint": "Gemini «Sin comprobar» se prueba al enviar; Copilot usa su caché del mes.",
    "more": [
      "«Sin comprobar» significa que Gemini tiene clave y es elegible, pero aún no respondió en esta sesión.",
      "Copilot solo es elegible cuando su caché local del mes está vigente; sin caché válida sigue sin comprobarse.",
      "Puedes revisar el detalle de cada conexión en «Apps y agentes»."
    ]
  },
  "run.limit_retry": {
    "title": "Reintentar tras límite",
    "hint": "Abre «Nueva sesión» para volver a lanzar cuando se reinicie la cuota.",
    "more": [
      "Cuando una ejecución falla por cuota, muestra «Límite alcanzado» con la hora local o «sin hora conocida».",
      "Pulsar «Reintentar» abre una nueva sesión sin relanzar nada de forma automática."
    ]
  }
};

// One-line hint of each execution mode: the hint of "create.mode" changes with the mode that is selected.
// Keep the keys equal to the modes of the form (auto, local, cloud, human_guided). Also strict JSON.
export const MODE_HINTS = {
  "auto": "Automático: cada paso usa el modelo que le asigna el workflow.",
  "local": "Modelo local: todo se ejecuta en tu equipo, sin enviar nada a la nube.",
  "cloud": "Modelo en la nube: usa un proveedor externo; puede costar dinero.",
  "human_guided": "Guiado por una persona: copias el prompt y pegas la respuesta, sin llamar a ningún modelo."
};

const KIND_SENTENCE = {
  long_text: "Admite texto largo, de varias líneas.",
  choice: "Se elige una de las opciones de la lista.",
  text: "Admite una línea de texto.",
};

// The entry of "create.mode" with the hint of the selected mode.
export function modeHelpEntry(mode) {
  return { ...HELP["create.mode"], hint: MODE_HINTS[mode] || HELP["create.mode"].hint };
}

// The entry of one declared or derived workflow input (the server sends its name, kind, example and use).
// The visible hint is the declared description when there is one; otherwise it says if the field is mandatory.
export function fieldHelpEntry(item) {
  const required = Boolean(item.required);
  const more = [];
  more.push(item.source === "declared" ? (KIND_SENTENCE[item.type] || KIND_SENTENCE.text) : "Admite texto libre.");
  if (required) more.push("Es obligatoria: sin ella no se puede crear la ejecución.");
  else if (item.skipped_only) more.push("Es opcional porque solo la usan pasos que no se ejecutan con esta configuración.");
  else more.push("Es opcional: si la dejas vacía, su hueco queda en blanco en el prompt.");
  if (item.example && item.type !== "choice") {
    const example = String(item.example).replace(/\s+/g, " ").trim().slice(0, 100).replace(/[.\s]+$/, "");
    if (example) more.push("Ejemplo: " + example + ".");
  }
  return {
    title: item.label || item.name,
    hint: required ? "Obligatoria: el workflow no puede arrancar sin ella." : "Opcional: puedes dejarla vacía.",
    more,
  };
}

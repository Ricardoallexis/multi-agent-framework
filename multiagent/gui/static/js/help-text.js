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

  "create.back": {
    "title": "Volver",
    "hint": "Cierra el formulario sin crear nada.",
    "more": [
      "Vuelves a la lista de ejecuciones.",
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
    "hint": "Lo que produjo cada paso, intento por intento.",
    "more": [
      "Cada desplegable es un intento de un paso y dice si está aprobado o sin aprobar.",
      "El contenido se muestra tal cual lo guardó el framework, en formato JSON.",
      "Cuando la ejecución espera tu revisión, se abre solo el resultado del paso que revisas."
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
      "Escribe qué debe cambiar: el texto es obligatorio y se entrega al modelo junto con el prompt del paso.",
      "Sirve cuando el resultado va bien encaminado pero hay que corregir algo concreto.",
      "Cuenta como otra llamada al modelo y desaparece si se agotó el presupuesto."
    ]
  },
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
  "session.children": {
    "title": "Subagentes",
    "hint": "Sesiones auxiliares que lanzó esta sesión.",
    "more": [
      "Cada una tiene su propia conversación, que abres desde su enlace.",
      "Si no aparece ninguna, esos subagentes se ven solo como avisos «Subagente» dentro de la conversación."
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

// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Help texts of the lists: sessions and the session screen, usage, evaluation, the «Contexto» panel, subagents,
// «Pendientes», «Paquetes» and «Inicio».
// One part of the help catalog: help-text.js joins every part into HELP, so a view's texts live in one file.
// Strict JSON under the rules of help-text.js: { title, hint, more }, keys as the views pass them to <Help topic=...>.

export const HELP = {
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
    "hint": "Abre la pantalla de lo que espera una decisión o una revisión.",
    "more": ["El número cuenta ejecuciones, propuestas y tareas que esperan a una persona.",
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
  "pending.tabs": {
    "title": "Secciones de Pendientes",
    "hint": "«Todo» junta la lista; cada pestaña abre una vista de lo que espera una decisión.",
    "more": ["Las flechas, Inicio y Fin cambian de pestaña, y la dirección de la página dice cuál está abierta.",
      "El número junto a una pestaña cuenta lo que espera en ella; las alertas de documentos no tienen número."]
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

  "packages.install_zip": {
    "title": "Instalar un paquete",
    "hint": "Elige un archivo .zip con el paquete. En modo avanzado también puedes indicar una carpeta.",
    "more": ["El archivo se revisa antes de instalarse: se rechaza si trae rutas que se salen de la carpeta, enlaces o demasiados archivos.", "Admite hasta 25 MB, y 50 MB o 2000 archivos al descomprimirse.", "Un paquete que trae código solo se instala en modo avanzado, y su primera carga pide tu aprobación."]
  },
  "packages.example": {
    "title": "Paquete de ejemplo",
    "hint": "Registra el paquete de ejemplo que trae el producto, sin copiarlo.",
    "more": ["Sirve para probar el producto con un equipo ya armado. Solo se ofrece cuando el producto trae sus carpetas de ejemplo.", "Al quitarlo no se borra nada de la carpeta del producto."]
  },
  "packages.trust_functions": {
    "title": "Confiar en las funciones del paquete",
    "hint": "Permite que el código de funciones de este paquete se ejecute en tu equipo.",
    "more": ["Solo aplica a paquetes con funciones propias, y es una decisión de modo avanzado.", "La confianza queda atada a esta copia: si el paquete cambia o se reinstala, hay que confiar de nuevo.", "Si dudas, déjala apagada: el paquete se instala igual y sus funciones no se cargan."]
  },
  "packages.remove": {
    "title": "Quitar un paquete",
    "hint": "Lo saca de la lista. Sus datos y sus ejecuciones se conservan.",
    "more": ["Quitar no borra la copia instalada ni lo que guardó el paquete.", "No se puede quitar mientras tenga ejecuciones en curso.", "Puedes instalar otro paquete con el mismo nombre cuando quieras."]
  },
  "packages.degraded": {
    "title": "Paquete que cambió o falta",
    "hint": "Su copia ya no coincide con la que se instaló, y no se carga.",
    "more": ["Se vuelve a cargar al reinstalarlo desde un archivo .zip, que reemplaza la copia por una versión nueva.", "Si lo prefieres, quítalo de la lista: sus datos se conservan."]
  },

  "start.steps": {
    "title": "Pasos de Inicio",
    "hint": "Solo se muestran los pasos que faltan, en orden. Cada uno trae su botón.",
    "more": ["El producto comprueba cada paso por lo que hay de verdad en tu equipo; no hay nada que marcar a mano.", "Un paso hecho no vuelve a aparecer. «Conectar» y «Activar» te llevan a «Apps y agentes»: aquí no se activa nada.", "Esta lista se actualiza sola cada pocos segundos."]
  },
  "start.times": {
    "title": "Tiempos de Inicio",
    "hint": "La hora de cada paso hecho y los minutos desde el inicio.",
    "more": ["«Copiar» pone este texto tal cual en el portapapeles. No copia ninguna configuración.", "Quien observa la prueba descuenta las descargas con su propio cronómetro."]
  },
  "start.real_models": {
    "title": "Pasar a modelos reales",
    "hint": "Cambia el ajuste de modelos a reales. Se aplica al abrir Iniciar de nuevo.",
    "more": ["Los modelos reales pueden costar dinero o usar tu cuota. Antes de cambiar, esta pantalla te pide confirmar.", "Se ofrece cuando ya probaste en Mock y ves el documento en el hilo.", "Puedes volver a Mock aquí mismo cuando quieras."]
  },
  "start.office_example": {
    "title": "Instalar la oficina de ejemplo",
    "hint": "Solo se ofrece en una copia del proyecto.",
    "more": ["La oficina se copia cuando pulsas el botón; no se instala al abrir el programa.", "Sus equipos quedan propuestos y no se aprueba ninguno."]
  },
  "start.office_import": {
    "title": "Importar una oficina",
    "hint": "Abre «Oficinas» para importar una oficina propia.",
    "more": ["En una copia instalada del framework, importa una oficina propia desde un archivo.", "Sus equipos quedan propuestos hasta que tú los apruebes."]
  }
};

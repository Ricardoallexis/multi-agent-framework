// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Help texts of the runs: list, detail, counters, human actions, external output and the run thread.
// One part of the help catalog: help-text.js joins every part into HELP, so a view's texts live in one file.
// Strict JSON under the rules of help-text.js: { title, hint, more }, keys as the views pass them to <Help topic=...>.

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
  "thread.document": {
    "title": "Documento",
    "hint": "Es el resultado de esta ejecución escrito como un documento en Markdown.",
    "more": ["Se muestra como texto plano, sin dibujar el formato.", "Aparece cuando la ejecución terminó y tiene un resultado."]
  },
  "thread.document_save": {
    "title": "Guardar como Markdown",
    "hint": "Descarga el documento como un archivo .md desde tu navegador.",
    "more": ["El archivo lo arma tu navegador; el servidor no escribe nada en tu equipo.", "El nombre lleva el título y el principio del id de la ejecución."]
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

  "human.tool_preview": {
    "title": "Vista previa de file.write",
    "hint": "Muestra el contenido que se propone escribir antes de aprobar la llamada.",
    "more": [
      "Si el archivo existe, también compara el contenido actual con el propuesto y avisa si se quitarán líneas.",
      "La vista usa los argumentos reconstruidos del workflow y solo los muestra si coinciden con la huella de la aprobación pendiente.",
      "Si no coinciden o la ruta no cumple los límites, explica por qué no puede mostrar el contenido."
    ]
  },

  "run.limit_retry": {
    "title": "Reintentar tras límite",
    "hint": "Abre «Nueva sesión» para volver a lanzar cuando se reinicie la cuota.",
    "more": [
      "Cuando una ejecución falla por cuota, muestra «Límite alcanzado» con la hora local o «sin hora conocida».",
      "Pulsar «Reintentar» abre una nueva sesión sin relanzar nada de forma automática."
    ]
  },
  "run.tabs": {
    "title": "Secciones de la ejecución",
    "hint": "Cambia entre pasos, resultados y detalles de esta ejecución.",
    "more": [
      "«Pasos» muestra el avance y los contadores; «Resultados» reúne las salidas por paso e intento.",
      "«Detalles» guarda el historial humano, las entradas y los eventos técnicos."
    ]
  }
};

# Local project tracker

These PowerShell sources implement the local `Seguimiento.cmd` project tracker
and its cloud-assignment workflow. They are versioned separately from the
operator's private channel data.

The normal launcher lives beside the private channel and invokes the versioned
scripts in this directory. It passes the private channel path explicitly. The
channel contains task cards, histories, assignment JSON, package reservations,
ideas, generated HTML, and loopback launchers; none of that data belongs in
this directory or in version control.

The entry points that consume private state require an explicit path:

- `ver-seguimiento.ps1 -ChannelPath <channel>\sistema -RepoPath <repository>`
- `preparar-paquete-cloud.ps1 -ChannelPath <channel>\sistema -RepoPath <repository>`
- `importar-paquete-nube.ps1 -ChannelPath <channel>\sistema -RepoPath <repository>`
- `gestionar-asignaciones-cloud.ps1 -SystemRoot <channel>\sistema`

The tools do not upload packages or claim live provider activity. Upload and
post-preparation state changes remain explicit operator actions.

## Trabajadores de varias aplicaciones

Quien orquesta y quien trabaja pueden ser de aplicaciones distintas (regla 33 del canal). La pestaña «Agentes» muestra cada trabajador delegado y activo en la tarjeta de SU aplicación («trabajando para <orquestador> en <tarea>», con su modelo, su actividad real y sus tokens por ventana), además de su línea en las delegaciones del orquestador. Una tarjeta de aplicación no dice «sin actividad» mientras tenga trabajadores activos; un trabajador «probablemente detenido» se lista pero no cuenta como activo.

La aplicación sale de la referencia del trabajador (`agent-` Claude, `copilot:` Copilot, `codex:` Codex, `agy:` Antigravity) y, sin referencia, del nombre del trabajador («Copilot gpt-6-luna»). Sólo se leen horas, estado y nombres de modelo de los archivos de actividad, nunca su contenido.

Hay UNA tabla de aplicaciones, `$script:WorkerApps` en `ver-seguimiento.ps1`. Agregar una aplicación es agregar una entrada, sin casos especiales por agente: `Key`, `Application` (nombre de su tarjeta), `RefPattern` y `RefId` (su referencia), `BarePattern`, `NamePattern`, `ShowAppPrefix`, `TailBytes`, `FindFile` (dónde está su archivo de actividad), `ReadModel` (cómo se lee su modelo) y `PendingTest` (cómo se detecta una prueba en ejecución). Las pruebas están en `tests/SEG-T17/`.

Antigravity (`agy`, GEM-T04): su referencia es `agy:<conversation_id>` (UUID) y sus trabajadores se llaman `Antigravity-<familia>`. Su actividad sale de la hora de modificación de `conversations\<id>.db` bajo `-AgyRoot` (por omisión `~\.gemini\antigravity-cli`) o, si no está, del json que el orquestador guarda en `orquestacion\herramientas\agy-<n>.json` (sólo se lee su `conversation_id`). El modelo no consta en esas fuentes: el trabajador conserva su nombre registrado, que ya lo lleva. Falta el lector de sesiones de GEM-T04 (multiagent/sessions/).

## Etapas y tareas congeladas

El estado `congelada` (ALCANCE.md) se reconoce en fichas de tarea y de etapa. Una tarea congelada se muestra con su propia insignia y no cuenta como pendiente, disponible ni en el total de su etapa; una etapa congelada sale en una sección aparte («Etapas congeladas»), plegada, sin tarjeta de resumen, sin pendientes para ti y sin aviso.

## Actividades fuera de tareas

El trabajo que no cuelga de una ficha (auditorías, propuestas, investigaciones, revisiones, integraciones, mantenimiento, sesiones programadas) se registra con `registrar-delegacion.ps1 -Actividad "<título>" -Tipo <tipo> [-Pedido "<cita del usuario>"]` en lugar de `-Tarea`, con los mismos campos de trabajador (`-Trabajador`, `-Nivel`, `-Esfuerzo`, `-Etiqueta`, `-Referencia`, `-Pieza`); se cierra con `-Devuelta -Resultado`. El registro queda en `<canal>\orquestacion\ACTIVIDADES\ejecuciones.jsonl` (una línea por evento: `opened`, `delegated`, `returned`, `closed`) y en el índice legible `ACTIVIDADES.md`; cada actividad tiene un id estable (`ACT-AAAAMMDD-NN`).

La pestaña «Agentes» muestra arriba la sección «Fuera de tareas»: cada actividad en curso con su tipo, título y orquestador, y cada trabajador con su aplicación, su modelo, su actividad real (Trabajando, Ejecutando prueba, Sin actividad reciente, Probablemente detenido) y sus tokens si constan (`usage_tokens`, un total o `{ total, input, output }`). Las terminadas quedan en una lista plegada. Cada trabajador activo figura además en la tarjeta de SU aplicación («trabajando para <orquestador> en <actividad>») y cuenta como activo; uno «probablemente detenido» se lista pero no cuenta. Si el registro existe pero no se puede leer, la sección lo dice en lugar de mostrar una lista vacía. Sólo se leen horas, estado y nombres de modelo de los archivos de actividad, nunca su contenido. El registro es también una fuente que vigila el visor (`Get-Stamp`). Las pruebas están en `tests/SEG-T18/`.

## En espera de activación (regla 36)

Quien solo espera algo externo (una ventana de integración, una dependencia, una aprobación, el reinicio de su límite) deja su tarea `pendiente` con «esperando: ...» y termina su corrida; `esperar-condicion.ps1` (sin modelo de IA) vigila la condición y lo relanza. Ese vigilante guarda un archivo de estado por espera en `<canal>\orquestacion\esperas\activas\<id>.json` (lo mueve a `terminadas` al cumplirse o vencer): `id`, `motivo`, `tarea`, `agente`, `estado` (`vigilando`, `error`, `cumplida`, `vencida`), `condiciones` (cada una con `tipo`, `valor`, `cumplida`, `met_at`, `avance`, `estimado`, `base`), `avance` / `estimado` / `base` globales, `met_at`, `vence`, `inicio`, `ultima_comprobacion`, `proxima_comprobacion` y `pid`.

Donde una tarea o un agente diría «sin actividad» y hay un vigilante vivo para esa tarea (`tarea`) o ese agente (`agente`), o la ficha está `pendiente` con «esperando: ...» en «Qué falta», el visor muestra la etiqueta «En espera de activación» con el motivo, la condición (tipo y valor; el valor de `entrada` y `archivo` no se muestra), a quién reactivará, desde cuándo, la última y la próxima revisión, y el avance. Sale en la fila de la tarea (pestaña Tareas), en el agente y en la tarjeta de su aplicación (pestaña Agentes, regla 33: «1 en espera de activación»). Un agente que trabaja en una tarea `en curso` conserva su estado y solo suma el bloque de espera. En «Fuera de tareas» hay una lista «Vigilantes»: los activos, los que tienen un problema (detenido: su proceso ya no existe; perdido: su latido es más viejo que 3 intervalos; con error: su última comprobación falló y reintenta) y los terminados, plegados. Un vigilante con problema no etiqueta a ninguna tarea porque no promete reactivar a nadie.

El avance se muestra como «NN % · hacia las HH:MM · <base>» o como «sin estimación»: nunca un porcentaje sin su base, nunca 100 % sin `met_at` (el momento en que se vio cumplida la condición), y el tiempo máximo del vigilante (`vence`) va aparte, no es avance. Con varias condiciones, si alguna pendiente no tiene estimación no hay estimación global; si todas la tienen, vale la de la más lenta. Cómo calcula el vigilante cada estimación: `hora` es exacta (con la confianza del restablecimiento si viene de un límite); `reporte` no da porcentaje hasta que la integración empezó, y luego es aproximada (mediana de las últimas 5 rondas leídas en `RESUMEN.md`, con su banda habitual en cuartiles, tope 95 % y, pasado el percentil 90, «tarda más de lo habitual»); `estado`, `commit`, `entrada`, `proceso` y `archivo` dependen de que un agente actúe y quedan sin estimación. Solo se muestran textos registrados, horas, estados y números: nunca el mensaje de un error, el script de la acción, la ruta del registro ni lo que una condición encontró. Mientras haya un vigilante vivo la página se regenera además cada minuto, para que un vigilante que dejó de dar señales pase a «perdido». Las pruebas están en `tests/SEG-T19/`.

## Lector opcional de la API core

El visor conserva Markdown como vista canónica y predeterminada. `-LeerApi` agrega una pestaña de solo lectura que obtiene grupos (`GET /api/v1/task-groups`), tareas (`GET /api/v1/tasks`) y eventos (`GET /api/v1/team-events`) desde `-ApiBaseUrl` (predeterminado `http://127.0.0.1:8000`). `-ApiTimeoutSeconds` fija el límite por petición. Ejemplo:

```powershell
pwsh -File .\tools\seguimiento\ver-seguimiento.ps1 -ChannelPath <canal>\sistema -RepoPath <repositorio> -NoAbrir -LeerApi -ApiBaseUrl http://127.0.0.1:8000
```

La pestaña valida los campos requeridos y comprueba los conteos de cada grupo contra la lista de tareas API. Errores HTTP, timeout, JSON o esquema se muestran como desconocidos/no disponibles; nunca como un tablero vacío exitoso. Sólo un conjunto vacío válido de grupos, tareas y eventos se rotula como vacío confirmado por API. Los estados se muestran con los nombres literales del core; `accepted` no se traduce a `integrada` o `probada`. Tokens y costo que la API no entrega quedan desconocidos. La evidencia Git y los reportes de integración siguen apareciendo por separado desde el checkout/canal local. El lector sólo hace GET y no usa la CLI privada ni lee SQLite.

Las fixtures y pruebas de consumidor dirigidas están en `tests/ORQ-T40/`.

## Esperas visibles (P-615)

Una ficha de tarea puede llevar la fila `| Espera | <quién> — <qué> (desde AAAA-MM-DD HH:mm, #<entrada>) |` (la escribe `marcar-espera.ps1` del canal; el Estado de la ficha no cambia). «Quién» es `usuario`, `agente <Nombre>`, `tarea <ID>`, `cuota (reinicia <hora>)` o `ventana de integración`; sin la fila, o con `—`, la tarea está trabajando. El visor muestra:

- **Insignia en la ficha** (pestaña Tareas): «espera tu decisión», resaltada, cuando se espera al usuario; «espera a X», en gris, para cualquier otra espera. Debajo van la pregunta, quién la hizo, desde cuándo y la entrada del canal. Una ficha `terminada` o `integrada` con la fila olvidada no muestra insignia.
- **Panel «Esperan tu decisión»**, siempre visible sobre las pestañas, con un contador (también en el título de la página) de las esperas del usuario sin resolver: tarea, pregunta, quién pregunta, desde cuándo y número de entrada, la más antigua primero. Sin esperas muestra «Esperan tu decisión: 0».
- **«resuelta, falta quitarla»**: una espera cuenta como resuelta cuando una entrada POSTERIOR del canal (`LOG.md`: número mayor que `#<entrada>` de la fila) de tipo `decisión` o `respuesta` cita el ID de la tarea en su título o en una línea `- tarea:`. El visor no borra nada: sigue listándola, marcada, hasta que quien tiene la tarea ejecute `marcar-espera.ps1 -Quitar`; la resuelta ya no suma al contador. Sin `LOG.md` ninguna espera se da por resuelta.
- **«sin actividad»**: una ficha `en curso`, sin fila Espera y sin vigilante vivo (regla 36), cuya última fila del Historial es más vieja que `-SinActividadHoras` (6 por defecto) horas. Es una pista de que se detuvo sin avisar, no un estado de la ficha. Mientras haya fichas `en curso` sin espera, la página se regenera también cada 15 minutos para que la etiqueta aparezca sin que cambie ningún archivo.

Las pruebas están en `tests/FLU-T06/` (fichas y `LOG.md` sintéticos; la parte de `marcar-espera.ps1` se prueba en el canal, en `validacion-esperas`).

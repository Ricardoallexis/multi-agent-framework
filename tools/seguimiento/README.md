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

La aplicación sale de la referencia del trabajador (`agent-` Claude, `copilot:` Copilot, `codex:` Codex) y, sin referencia, del nombre del trabajador («Copilot gpt-6-luna»). Sólo se leen horas, estado y nombres de modelo de los archivos de actividad, nunca su contenido.

Hay UNA tabla de aplicaciones, `$script:WorkerApps` en `ver-seguimiento.ps1`. Agregar una aplicación es agregar una entrada, sin casos especiales por agente: `Key`, `Application` (nombre de su tarjeta), `RefPattern` y `RefId` (su referencia), `BarePattern`, `NamePattern`, `ShowAppPrefix`, `TailBytes`, `FindFile` (dónde está su archivo de actividad), `ReadModel` (cómo se lee su modelo) y `PendingTest` (cómo se detecta una prueba en ejecución). Las pruebas están en `tests/SEG-T17/`.

## Actividades fuera de tareas

El trabajo que no cuelga de una ficha (auditorías, propuestas, investigaciones, revisiones, integraciones, mantenimiento, sesiones programadas) se registra con `registrar-delegacion.ps1 -Actividad "<título>" -Tipo <tipo> [-Pedido "<cita del usuario>"]` en lugar de `-Tarea`, con los mismos campos de trabajador (`-Trabajador`, `-Nivel`, `-Esfuerzo`, `-Etiqueta`, `-Referencia`, `-Pieza`); se cierra con `-Devuelta -Resultado`. El registro queda en `<canal>\orquestacion\ACTIVIDADES\ejecuciones.jsonl` (una línea por evento: `opened`, `delegated`, `returned`, `closed`) y en el índice legible `ACTIVIDADES.md`; cada actividad tiene un id estable (`ACT-AAAAMMDD-NN`).

La pestaña «Agentes» muestra arriba la sección «Fuera de tareas»: cada actividad en curso con su tipo, título y orquestador, y cada trabajador con su aplicación, su modelo, su actividad real (Trabajando, Ejecutando prueba, Sin actividad reciente, Probablemente detenido) y sus tokens si constan (`usage_tokens`, un total o `{ total, input, output }`). Las terminadas quedan en una lista plegada. Cada trabajador activo figura además en la tarjeta de SU aplicación («trabajando para <orquestador> en <actividad>») y cuenta como activo; uno «probablemente detenido» se lista pero no cuenta. Si el registro existe pero no se puede leer, la sección lo dice en lugar de mostrar una lista vacía. Sólo se leen horas, estado y nombres de modelo de los archivos de actividad, nunca su contenido. El registro es también una fuente que vigila el visor (`Get-Stamp`). Las pruebas están en `tests/SEG-T18/`.

## Lector opcional de la API core

El visor conserva Markdown como vista canónica y predeterminada. `-LeerApi` agrega una pestaña de solo lectura que obtiene grupos (`GET /api/v1/task-groups`), tareas (`GET /api/v1/tasks`) y eventos (`GET /api/v1/team-events`) desde `-ApiBaseUrl` (predeterminado `http://127.0.0.1:8000`). `-ApiTimeoutSeconds` fija el límite por petición. Ejemplo:

```powershell
pwsh -File .\tools\seguimiento\ver-seguimiento.ps1 -ChannelPath <canal>\sistema -RepoPath <repositorio> -NoAbrir -LeerApi -ApiBaseUrl http://127.0.0.1:8000
```

La pestaña valida los campos requeridos y comprueba los conteos de cada grupo contra la lista de tareas API. Errores HTTP, timeout, JSON o esquema se muestran como desconocidos/no disponibles; nunca como un tablero vacío exitoso. Sólo un conjunto vacío válido de grupos, tareas y eventos se rotula como vacío confirmado por API. Los estados se muestran con los nombres literales del core; `accepted` no se traduce a `integrada` o `probada`. Tokens y costo que la API no entrega quedan desconocidos. La evidencia Git y los reportes de integración siguen apareciendo por separado desde el checkout/canal local. El lector sólo hace GET y no usa la CLI privada ni lee SQLite.

Las fixtures y pruebas de consumidor dirigidas están en `tests/ORQ-T40/`.

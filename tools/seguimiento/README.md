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

## Lector opcional de la API core

El visor conserva Markdown como vista canónica y predeterminada. `-LeerApi` agrega una pestaña de solo lectura que obtiene grupos (`GET /api/v1/task-groups`), tareas (`GET /api/v1/tasks`) y eventos (`GET /api/v1/team-events`) desde `-ApiBaseUrl` (predeterminado `http://127.0.0.1:8000`). `-ApiTimeoutSeconds` fija el límite por petición. Ejemplo:

```powershell
pwsh -File .\tools\seguimiento\ver-seguimiento.ps1 -ChannelPath <canal>\sistema -RepoPath <repositorio> -NoAbrir -LeerApi -ApiBaseUrl http://127.0.0.1:8000
```

La pestaña valida los campos requeridos y comprueba los conteos de cada grupo contra la lista de tareas API. Errores HTTP, timeout, JSON o esquema se muestran como desconocidos/no disponibles; nunca como un tablero vacío exitoso. Sólo un conjunto vacío válido de grupos, tareas y eventos se rotula como vacío confirmado por API. Los estados se muestran con los nombres literales del core; `accepted` no se traduce a `integrada` o `probada`. Tokens y costo que la API no entrega quedan desconocidos. La evidencia Git y los reportes de integración siguen apareciendo por separado desde el checkout/canal local. El lector sólo hace GET y no usa la CLI privada ni lee SQLite.

Las fixtures y pruebas de consumidor dirigidas están en `tests/ORQ-T40/`.

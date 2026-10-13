# FLU-T06 (P-615): esperas visibles. A task card may carry the row «Espera» (marcar-espera.ps1 writes it; Estado does not change):
#   | Espera | <quién> — <qué> (desde AAAA-MM-DD HH:mm, #<entrada>) |
# Seguimiento shows (a) a badge on the card: «espera tu decisión» (highlighted) for usuario, «espera a X» (grey) otherwise; (b) the panel
# «Esperan tu decisión» above the tabs, with a counter (also in the page title) that counts the waits for the user not yet resolved; (c) «resuelta,
# falta quitarla» when a LATER channel entry of type decisión or respuesta cites the task in its title or in a «- tarea:» line; (d) «sin actividad» on a
# card en curso without Espera whose newest Historial row is older than -SinActividadHoras (default 6).
# Synthetic cards and a synthetic LOG.md in a temp folder only; the real channel and the real agent folders are never read.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path
$viewer = Join-Path $root 'tools\seguimiento\ver-seguimiento.ps1'
$temp = Join-Path ([IO.Path]::GetTempPath()) ('flu-t06-' + [guid]::NewGuid().ToString('N'))
$repo = Join-Path $temp 'repo'
$channel = Join-Path $temp 'channel'
$subRoot = Join-Path $temp 'projects'
$copilotRoot = Join-Path $temp 'copilot'
$codexRoot = Join-Path $temp 'codex'
$now = [datetime]'2026-10-13T12:00:00'
$failed = $false

function Assert-True([bool]$condition, [string]$message) { if (-not $condition) { throw "FAIL: $message" } }
function Write-Text([string]$path, [string]$text) { [IO.File]::WriteAllText($path, $text, [Text.UTF8Encoding]::new($false)) }

# A card of the stage AAA: the Espera row only when $espera is given; the Historial has the creation row plus $last (the newest row).
function Write-Card([string]$code, [string]$state, [string]$owner, [string]$espera = '', [string]$last = '2026-10-13 11:00', [string]$lastAction = 'avance', [string]$lastAgent = '') {
    if (-not $lastAgent) { $lastAgent = $owner }
    $lines = @("# AAA-$code · Tarea de prueba $code", '', '| Campo | Valor |', '| --- | --- |', "| Estado | $state |", "| Agente | $owner |", '| Depende de | - |', '| Parche | ninguno |')
    if ($espera) { $lines += "| Espera | $espera |" }
    $lines += @('', '## Historial', '', '| Fecha | Agente | Acción | Detalle |', '| --- | --- | --- | --- |', "| 2026-10-12 09:00 | $owner | tomada | x |", "| $last | $lastAgent | $lastAction | y |")
    Write-Text (Join-Path $channel "tareas\AAA\$code-x.md") (($lines -join "`n") + "`n")
}
function Write-Entry([int]$number, [string]$date, [string]$type, [string]$title, [string]$taskLine = '', [string]$author = 'Claude-Opus') {
    $text = "### $number · $date · $author · $type ($title)`n- autorización: no requerida`n- para: Todos`n"
    if ($taskLine) { $text += "- tarea: $taskLine`n" }
    $text + "- pide a: Todos - leer`n"
}
function Run-Viewer([hashtable]$extra = @{}) {
    & $viewer -ChannelPath $channel -RepoPath $repo -NoAbrir -SubagentRoots $subRoot -CopilotRoot $copilotRoot -CodexRoot $codexRoot -NowOverride $now @extra | Out-Null
    [System.Net.WebUtility]::HtmlDecode([IO.File]::ReadAllText((Join-Path $channel 'SEGUIMIENTO.html')))
}
# The whole row of a task in the Tareas tab, and its state cell (second cell).
function Get-Row([string]$html, [string]$code) {
    $row = [regex]::Match($html, "(?s)<tr><td><a href=""[^""]*"">AAA-$code ·.*?</tr>")
    Assert-True $row.Success "row of $code in the Tareas tab"
    $row.Value
}
function Get-StateCell([string]$html, [string]$code) {
    $cell = [regex]::Match((Get-Row $html $code), '(?s)</td><td>(.*?)</td><td>')
    Assert-True $cell.Success "state cell of $code"
    $cell.Groups[1].Value
}
function Get-Panel([string]$html) {
    $panel = [regex]::Match($html, '(?s)<section class="espera-panel" id="esperan-tu-decision".*?</section>')
    Assert-True $panel.Success 'the panel «Esperan tu decisión» is present'
    $panel.Value
}
function Get-Counter([string]$html) {
    $m = [regex]::Match((Get-Panel $html), '<span class="espera-count"[^>]*>(\d+)</span>')
    Assert-True $m.Success 'the panel has a counter'
    [int]$m.Groups[1].Value
}
# The panel's item of a task (one <li>).
function Get-PanelItem([string]$html, [string]$code) {
    $item = [regex]::Match((Get-Panel $html), "(?s)<li class=""espera-item[^""]*"">(?:(?!</li>).)*AAA-$code ·.*?</li>")
    Assert-True $item.Success "the panel lists AAA-$code"
    $item.Value
}
function Assert-NotInPanel([string]$html, [string]$code, [string]$why) {
    Assert-True ((Get-Panel $html) -notmatch "AAA-$code ·") "AAA-$code is not in the panel ($why)"
}

try {
    New-Item -ItemType Directory -Force $repo, $subRoot, $copilotRoot, $codexRoot, (Join-Path $channel 'tareas\AAA'), (Join-Path $channel 'parches\pendientes'), (Join-Path $channel 'pruebas\integracion') | Out-Null
    Write-Text (Join-Path $channel 'RESUMEN.md') '# Resumen'
    Write-Text (Join-Path $channel 'ROADMAP_VISTA.md') '# Roadmap'
    Write-Text (Join-Path $channel 'AGENTES.md') "| Agente | Modelo | Aplicación | Estado | Último | Cursor | Sesión |`n|---|---|---|---|---|---|---|`n| Claude-Opus | Opus | Claude | ACTIVO | x | c | s1 |`n| Codex | gpt | Codex | ACTIVO | x | c | s2 |`n"
    git -C $repo init -q | Out-Null
    Write-Text (Join-Path $channel 'tareas\AAA\ETAPA.md') "# AAA · Etapa de prueba`n`n| Campo | Valor |`n|---|---|`n| Autorizada | sí |`n| Estado | abierta |`n`n## Historial de decisiones`n`n- 2026-10-12 · Claude-Opus · Se abre la etapa`n"

    # --- The channel log: the question entries and the later decisions / answers that resolve (or not) the waits ---
    $log = @('# Registro', '')
    $log += Write-Entry 605 '2026-10-12' 'decisión' 'AAA-T04 se replanifica'   # EARLIER than the question of AAA-T04 (#620): never resolves it
    $log += Write-Entry 610 '2026-10-13' 'pregunta' 'AAA-T01 espera tu decisión' 'AAA-T01'
    $log += Write-Entry 612 '2026-10-13' 'pregunta' 'AAA-T02 y AAA-T03 esperan tu decisión' 'AAA-T02, AAA-T03'
    $log += Write-Entry 615 '2026-10-13' 'decisión' 'Se aprueba el diseño B de AAA-T02'
    $log += Write-Entry 616 '2026-10-13' 'respuesta' 'Respuesta del usuario' 'AAA-T09, AAA-T03'
    $log += Write-Entry 617 '2026-10-13' 'decisión' 'Otra decisión que cita AAA-T011, no la tarea T01'
    $log += Write-Entry 618 '2026-10-13' 'decision' 'AAA-T14 aprobada'
    $log += Write-Entry 619 '2026-10-13' 'entrega' 'AAA-T04 entregada (no es decisión ni respuesta)' 'AAA-T04'
    $log += Write-Entry 620 '2026-10-13' 'pregunta' 'AAA-T04 espera tu decisión' 'AAA-T04'
    $log += Write-Entry 621 '2026-10-13' 'aclaración' 'AAA-T04 aclarada (tampoco resuelve)' 'AAA-T04'
    Write-Text (Join-Path $channel 'LOG.md') (($log -join "`n") + "`n")

    # --- The cards ---
    Write-Card 'T01' 'en curso' 'Claude-Opus' 'usuario — ¿Aprobamos el diseño A o el B? (desde 2026-10-13 08:00, #610)' '2026-10-13 08:05' 'espera'
    Write-Card 'T02' 'en curso' 'Claude-Opus' 'usuario — ¿Cuál diseño? (desde 2026-10-13 09:00, #612)' '2026-10-13 09:05' 'espera'
    Write-Card 'T03' 'en curso' 'Codex' 'usuario — ¿Publicamos? (desde 2026-10-13 09:10, #612)' '2026-10-13 09:15' 'espera'
    Write-Card 'T04' 'en curso' 'Claude-Opus' 'usuario — ¿Seguimos con el plan nuevo? (desde 2026-10-13 10:00, #620)' '2026-10-13 10:05' 'espera'
    Write-Card 'T05' 'en curso' 'Claude-Opus' 'agente Codex — revisión de la entrega (desde 2026-10-13 09:00, #611)' '2026-10-13 09:05' 'espera'
    Write-Card 'T06' 'en curso' 'Claude-Opus' 'tarea AAA-T01 — necesita su API (desde 2026-10-13 09:00, #611)' '2026-10-13 09:05' 'espera'
    Write-Card 'T07' 'en curso' 'Claude-Opus' 'cuota (reinicia 18:30) — límite semanal agotado (desde 2026-10-13 09:00, #611)' '2026-10-13 09:05' 'espera'
    Write-Card 'T08' 'en curso' 'Claude-Opus' 'ventana de integración — espera el commit de la ronda (desde 2026-10-13 09:00, #611)' '2026-10-13 09:05' 'espera'
    Write-Card 'T09' 'en curso' 'Claude-Opus' '' '2026-10-13 04:00'          # 8 h without a row: sin actividad
    Write-Card 'T10' 'en curso' 'Claude-Opus' '' '2026-10-13 08:00'          # 4 h: working
    Write-Card 'T11' 'en curso' 'Claude-Opus' '' '2026-10-13 06:00'          # exactly 6 h: not older than 6 h
    Write-Card 'T12' 'terminada' 'Claude-Opus' 'usuario — esperanza vieja que nadie quitó (desde 2026-10-13 03:00, #600)' '2026-10-13 03:05'
    Write-Card 'T13' 'en curso' 'Claude-Opus' '—' '2026-10-13 11:30'         # «—» = working
    Write-Card 'T14' 'en curso' 'Claude-Opus' 'usuario — pregunta sin número de entrada (desde 2026-10-13 07:00)' '2026-10-13 07:05' 'espera'
    Write-Card 'T15' 'pendiente' 'Codex' 'usuario — ¿Autorizas la etapa? <b>negrita</b> (desde 2026-10-12 18:00, #590)' '2026-10-12 18:05' 'espera'
    Write-Card 'T16' 'en curso' 'Claude-Opus' 'esto no sigue el formato' '2026-10-13 11:00'
    Write-Card 'T17' 'disponible' '—' '' '2026-10-12 09:00'                   # not en curso: never «sin actividad»
    Write-Card 'T18' 'en curso' 'Claude-Opus' 'usuario — pregunta de esta tarea (desde 2026-10-13 08:30, #613)' '2026-10-13 08:35' 'espera'
    Write-Card 'T011' 'en curso' 'Claude-Opus' '' '2026-10-13 11:00'

    $html = Run-Viewer
    if ($env:SEG_KEEP) { Copy-Item -LiteralPath (Join-Path $channel 'SEGUIMIENTO.html') -Destination (Join-Path $temp 'SEGUIMIENTO-completo.html') }   # for looking at the page

    # --- The panel and its counter ---
    $panel = Get-Panel $html
    $unresolved = @('T01', 'T04', 'T15', 'T18')   # T02, T03 (decisión / respuesta later), T14 (same day, no entry number) are resolved
    Assert-True ((Get-Counter $html) -eq $unresolved.Count) "the counter counts the unresolved waits for the user ($($unresolved.Count)), got $(Get-Counter $html)"
    Assert-True ($panel -match '<h2>Esperan tu decisión ') 'the panel title is «Esperan tu decisión»'
    Assert-True ($html -match '<title>\(4\) Seguimiento del proyecto</title>') 'the counter is also in the page title'
    Assert-True ($html.IndexOf('id="esperan-tu-decision"') -lt $html.IndexOf('<nav>')) 'the panel is above the tabs (always visible)'
    $item = Get-PanelItem $html 'T01'
    Assert-True ($item -match '¿Aprobamos el diseño A o el B\?' -and $item -match 'pregunta: Claude-Opus' -and $item -match 'desde 2026-10-13 08:00 \(hace 4 horas\)' -and $item -match 'entrada #610') 'T01 in the panel: question, who asks, since when and the entry'
    Assert-True ($item -match 'state s-espera-usuario">espera tu decisión' -and $item -notmatch 'resuelta') 'T01 in the panel is highlighted and not resolved'
    $item = Get-PanelItem $html 'T04'
    Assert-True ($item -notmatch 'resuelta' -and $item -match 'entrada #620') 'T04: an EARLIER decision (#605) and later entregas / aclaraciones (#619, #621) do not resolve it'
    $item = Get-PanelItem $html 'T15'
    Assert-True ($item -match 'pregunta: Codex' -and $item -match 'desde 2026-10-12 18:00 \(hace 18 horas\)' -and $item -match 'entrada #590') 'T15 (pendiente) is listed with its asker, since when and entry'
    $raw = [IO.File]::ReadAllText((Join-Path $channel 'SEGUIMIENTO.html'))
    Assert-True ($raw -match '&lt;b&gt;negrita&lt;/b&gt;' -and $raw -notmatch '<b>negrita</b>') 'the question text is HTML-encoded'
    Assert-True ((Get-PanelItem $html 'T18') -match 'entrada #613') 'T18 is listed'
    Assert-True (($panel.IndexOf('AAA-T15 ·') -lt $panel.IndexOf('AAA-T01 ·')) -and ($panel.IndexOf('AAA-T01 ·') -lt $panel.IndexOf('AAA-T18 ·')) -and ($panel.IndexOf('AAA-T18 ·') -lt $panel.IndexOf('AAA-T04 ·'))) 'the unresolved waits are ordered by since (T15 08:00 the day before, T01 08:00, T18 08:30, T04 10:00)'
    foreach ($code in 'T02', 'T03', 'T14') {
        $item = Get-PanelItem $html $code
        Assert-True ($item -match 'resuelta, falta quitarla' -and $item -notmatch 's-espera-usuario') "$code in the panel is resolved and no longer highlighted"
        Assert-True ($panel.IndexOf("AAA-$code ·") -gt $panel.IndexOf('AAA-T04 ·')) "$code (resolved) is listed after every unresolved wait"
    }
    Assert-True ((Get-PanelItem $html 'T02') -match 'la decisión #615 cita la tarea' -and (Get-PanelItem $html 'T03') -match 'la respuesta #616 cita la tarea') 'the resolving entry is named (decisión #615 via the title, respuesta #616 via «- tarea:»)'
    Assert-True ((Get-PanelItem $html 'T14') -match 'la decisión #618 cita la tarea') 'a wait without entry number is resolved by a decisión of the same day or later (type «decision» without accent too)'
    foreach ($code in 'T05', 'T06', 'T07', 'T08', 'T09', 'T10', 'T11', 'T12', 'T13', 'T16', 'T17') { Assert-NotInPanel $html $code 'not a wait for the user' }
    Assert-True ($panel -notmatch 'AAA-T011 ·') 'T011 is not confused with T01'

    # --- Badges in the card (the Estado never changes) ---
    $cell = Get-StateCell $html 'T01'
    Assert-True ($cell -match '^<span class="state s-en-curso">en curso</span>') 'T01 keeps its Estado: en curso'
    Assert-True ($cell -match '<span class="state s-espera-usuario">espera tu decisión</span>' -and $cell -match '¿Aprobamos el diseño A o el B\?' -and $cell -match 'desde 2026-10-13 08:00 \(hace 4 horas\) · entrada #610') 'T01: «espera tu decisión» badge with the question and since when'
    $cell = Get-StateCell $html 'T15'
    Assert-True ($cell -match '^<span class="state s-pendiente">pendiente</span>' -and $cell -match 's-espera-usuario') 'T15 (pendiente) keeps its Estado and shows the badge'
    foreach ($case in @(@('T05', 'espera a Codex'), @('T06', 'espera a AAA-T01'), @('T07', 'espera a la cuota \(reinicia 18:30\)'), @('T08', 'espera a la ventana de integración'))) {
        $cell = Get-StateCell $html $case[0]
        Assert-True ($cell -match "<span class=""state s-espera-otro"">$($case[1])</span>") "$($case[0]): grey badge «$($case[1])»"
        Assert-True ($cell -notmatch 's-espera-usuario' -and $cell -notmatch 'espera tu decisión') "$($case[0]): not highlighted as a wait for the user"
        Assert-True ($cell -match '^<span class="state s-en-curso">en curso</span>') "$($case[0]) keeps its Estado: en curso"
    }
    foreach ($code in 'T02', 'T03', 'T14') {
        $cell = Get-StateCell $html $code
        Assert-True ($cell -match '<span class="state s-espera-resuelta">resuelta, falta quitarla</span>' -and $cell -match 'marcar-espera\.ps1 -Quitar') "$code`: resolved, still to be cleared"
        Assert-True ($cell -notmatch 's-espera-usuario') "$code`: a resolved wait is not highlighted"
    }
    Assert-True ((Get-StateCell $html 'T01') -notmatch 'resuelta') 'T01 is not resolved by an entry that cites AAA-T011'
    $cell = Get-StateCell $html 'T12'
    Assert-True ($cell -match 'terminada' -and $cell -notmatch 'espera') 'a terminada card with a stale Espera shows no badge'
    foreach ($code in 'T09', 'T10', 'T11', 'T13', 'T17') { Assert-True ((Get-StateCell $html $code) -notmatch 's-espera') "$code has no Espera badge" }
    $cell = Get-StateCell $html 'T16'
    Assert-True ($cell -match '<span class="state s-espera-otro">espera a algo externo</span>' -and $cell -match 'esto no sigue el formato') 'an Espera that does not follow the format is shown as is, without failing'

    # --- «sin actividad» ---
    $cell = Get-StateCell $html 'T09'
    Assert-True ($cell -match '<span class="state s-sin-actividad"[^>]*>sin actividad</span>' -and $cell -match 'Sin avance hace 8 horas' -and $cell -match 'última fila 2026-10-13 04:00') 'T09: en curso, newest Historial row 8 h old: sin actividad'
    Assert-True ($cell -match '^<span class="state s-en-curso">en curso</span>') 'T09 keeps its Estado: en curso'
    foreach ($code in 'T10', 'T11', 'T13', 'T011') { Assert-True ((Get-StateCell $html $code) -notmatch 'sin actividad') "$code`: not older than 6 h: no «sin actividad»" }
    foreach ($code in 'T01', 'T05', 'T16') { Assert-True ((Get-StateCell $html $code) -notmatch 'sin actividad') "$code`: it has an Espera: no «sin actividad»" }
    Assert-True ((Get-StateCell $html 'T17') -notmatch 'sin actividad' -and (Get-StateCell $html 'T15') -notmatch 'sin actividad') 'a card that is not en curso never reads «sin actividad»'
    $html2 = Run-Viewer @{ SinActividadHoras = 3 }
    Assert-True ((Get-StateCell $html2 'T10') -match 'sin actividad' -and (Get-StateCell $html2 'T11') -match 'sin actividad' -and (Get-StateCell $html2 'T10') -match 'Sin avance hace 4 horas') '-SinActividadHoras 3: T10 (4 h) and T11 (6 h) read sin actividad'
    $html3 = Run-Viewer @{ SinActividadHoras = 12 }
    Assert-True ((Get-StateCell $html3 'T09') -notmatch 'sin actividad') '-SinActividadHoras 12: T09 (8 h) is working'

    # --- Both themes and narrow screens ---
    Assert-True ($html -match '(?s):root \{[^}]*--ask-bg:#fff0d6[^}]*--ask-line:#e08a00[^}]*--grey-bg:#eceef2') 'light theme: variables of the panel and the grey badge'
    Assert-True ($html -match '(?s)prefers-color-scheme: dark\) \{\s*:root \{[^}]*--ask-bg:#3a2a0d[^}]*--ask-line:#f0a830[^}]*--grey-bg:#262b34') 'dark theme: the same variables redefined'
    Assert-True ($html -match '(?s)@media \(max-width: 600px\) \{[^}]*\.seg-esperas[^}]*\}[^}]*\.espera-panel') 'narrow screens: the panel gets tighter padding'
    Assert-True ($html -match '\.s-espera-usuario \{[^}]*white-space:normal' -and $html -match '\.s-espera-resuelta \{[^}]*white-space:normal') 'the long badges wrap on narrow screens'
    Assert-True ($html -match '<meta name="viewport" content="width=device-width, initial-scale=1">') 'the viewport meta is still there'

    # --- Nothing waits: the panel says 0 and the title has no counter ---
    Get-ChildItem (Join-Path $channel 'tareas\AAA') -Filter 'T*.md' | Remove-Item
    Write-Card 'T01' 'en curso' 'Claude-Opus' '' '2026-10-13 11:00'
    Write-Card 'T02' 'en curso' 'Claude-Opus' 'agente Codex — revisión (desde 2026-10-13 09:00, #611)' '2026-10-13 09:05' 'espera'
    $html = Run-Viewer
    Assert-True ($html -match '<p class="espera-none" role="status">Esperan tu decisión: <strong>0</strong></p>' -and $html -notmatch 'id="esperan-tu-decision"') 'no wait for the user: the panel is a quiet «0»'
    Assert-True ($html -match '<title>Seguimiento del proyecto</title>') 'no wait for the user: the title has no counter'
    Assert-True ((Get-StateCell $html 'T02') -match 'espera a Codex') 'a wait for another agent still shows its badge'

    # --- Only resolved waits: the counter is 0 but the panel lists what is left to clear ---
    Write-Card 'T01' 'en curso' 'Claude-Opus' 'usuario — ¿Aprobamos? (desde 2026-10-13 08:00, #610)' '2026-10-13 08:05' 'espera'
    Write-Text (Join-Path $channel 'LOG.md') ((Get-Content -LiteralPath (Join-Path $channel 'LOG.md') -Raw).TrimEnd() + "`n`n" + (Write-Entry 622 '2026-10-13' 'decisión' 'AAA-T01 aprobada con el diseño A'))
    $html = Run-Viewer
    Assert-True ((Get-Counter $html) -eq 0 -and (Get-Panel $html) -match 'Ninguna espera tu decisión' -and (Get-PanelItem $html 'T01') -match 'resuelta, falta quitarla' -and $html -notmatch '<title>\(') 'only resolved waits: counter 0, the resolved one still listed, no counter in the title'

    # --- A missing LOG.md never breaks the page: the waits stay unresolved ---
    Write-Card 'T01' 'en curso' 'Claude-Opus' 'usuario — ¿Aprobamos? (desde 2026-10-13 08:00, #610)' '2026-10-13 08:05' 'espera'
    Remove-Item (Join-Path $channel 'LOG.md')
    $html = Run-Viewer
    Assert-True ((Get-Counter $html) -eq 1 -and (Get-PanelItem $html 'T01') -notmatch 'resuelta') 'no LOG.md: the wait is shown, unresolved'
}
catch {
    Write-Host $_.Exception.Message
    $failed = $true
}
finally {
    if ($env:SEG_KEEP) { Write-Host $temp } elseif ($temp -and (Test-Path -LiteralPath $temp)) { & (Join-Path $PSScriptRoot 'Remove-Temp.ps1') $temp }
}
if ($failed) { exit 1 }
Write-Host 'FLU-T06 Esperas visibles PASS'

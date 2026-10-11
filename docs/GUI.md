# Operator GUI

The local operator GUI at `/gui/` is a separate interface from the Stage 0
run-console prototype at `/ui/`. The prototype remains available until the
new GUI covers its use cases. The GUI is served by the local API and is an
operator view, not a public API or a separate orchestration layer.

## Start

From an installed project environment, run either command:

```console
multiagent gui [--port N] [--real] [--no-browser]
python -m multiagent.gui
```

The GUI defaults to Mock mode. Use `--real` to leave the configured provider
mode in effect; real provider calls may cost money. The server binds only to a
loopback host and refuses other hosts. It creates a random 256-bit local token
in `~/.multiagent/gui-token` and opens
`http://127.0.0.1:<port>/gui/#token=…`. The page stores the token in
`sessionStorage` and removes it from the URL. `--no-browser` prints the URL
without opening it. The default port is `APP_PORT` (8000).

## Views

**Sesiones** is the default view. It combines a read-only, filterable and
paginated list of Claude Code, Claude Desktop, Codex, GitHub Copilot, and
framework-run sessions. Details are rendered according to item kind and
include subagents; keyboard navigation and live follow over server-sent events
(SSE) are supported.

**Ejecuciones** creates a framework run using named required inputs derived from
placeholders in the selected workflow's prompt. Workflows do not yet declare
input schemas. The GUI previews the rendered prompt before creating the run.
Run details show skipped steps, counters, and human-action history. Available
human actions include approve, request changes, reject, regenerate, cancel,
and submit a human-produced result.

## Open a session in its application

- **Codex:** follows a `codex://threads/<id>` link.
- **GitHub Copilot:** follows a `ghapp://sessions/<id>` link; the application
  asks the user to confirm.
- **Claude:** after confirmation in the page, the server starts
  `claude --desktop --resume <id>` in a new visible console from the user's
  home folder, with a cleaned environment. Set `GUI_ALLOW_LAUNCH=false` to
  disable launch; the page then shows the command to copy.

Claude and Copilot show new content after reopening the application. Codex
blocks writes while the application has the thread open; the GUI viewer only
reads the session.

## Configuration

Settings are read from the environment using the uppercase names shown below,
or from the project's normal settings file. Session roots may be supplied as
a JSON list or as values separated by `;`.

| Setting | Default | Purpose |
| --- | --- | --- |
| `GUI_SESSION_ROOTS` | `[]` | Allowed working-folder roots for listing sessions; empty means none are listed. |
| `GUI_SESSION_SOURCES` | `["claude", "codex", "copilot", "framework"]` | Session sources to read. |
| `GUI_CLAUDE_HOME` | unset | Override Claude's session store location. |
| `GUI_CODEX_HOME` | unset | Override Codex's session store location. |
| `GUI_COPILOT_HOME` | unset | Override Copilot's session store location. |
| `GUI_CLAUDE_DESKTOP_DIR` | unset | Override Claude Desktop's session directory. |
| `GUI_ALLOW_LAUNCH` | `true` | Allow the GUI to launch Claude Desktop sessions. |
| `GUI_TOKEN_FILE` | `~/.multiagent/gui-token` | GUI access-token file. |
| `GUI_TOKEN_OUTSIDE_HOME` | `false` | Allow the token file outside the user's home folder. |
| `GUI_HEARTBEAT_SECONDS` | `20.0` | SSE heartbeat interval. |
| `GUI_POLL_SECONDS` | `0.5` | Poll interval for GUI run/session updates. |
| `GUI_STREAM_MAX_SECONDS` | `0.0` | Maximum SSE connection duration; zero keeps it open until the client leaves. |

When `GUI_SESSION_ROOTS` is empty, the GUI explains how to configure it rather
than listing sessions. The store-location overrides are optional; unset values
use the readers' normal locations.

## Security model

Every `/gui/api/*` request requires the local token in an
`Authorization: Bearer <token>` header. The API middleware also enforces a
Host allow-list and same-origin checks to mitigate DNS rebinding and CSRF.
State-changing requests accept JSON only. The GUI uses a strict Content
Security Policy with Trusted Types; its Preact and htm dependencies are
vendored under `multiagent/gui/static/vendor/`, with their licenses alongside.
SSE events use a resumable cursor.

The token is a local access credential, not an account or network access
mechanism. Keep the server on loopback and the token file private. Allowing a
token path outside the home folder is an explicit operator choice and is
refused by default.

## Read-only sessions and limitations

Session readers in `multiagent/sessions/` do not write or persist data.
SQLite stores are opened read-only, lines larger than 1 MB are skipped,
reasoning is never shown, and all session content is rendered as plain text.
Only sessions whose working folder is under a configured
`GUI_SESSION_ROOTS` path are listed.

Copilot desktop-only data is not read. Framework-run status is reduced to
active or idle. Long sessions do not have a "jump to end" control. The session
viewer is read-only; the separate Runs view is where the GUI creates and
manages framework runs.

The GUI HTTP routes live under `/gui/api`, are token-protected, and are not
part of the public `/api/v1` contract. The equivalent local, read-only session
commands are `multiagent sessions list`, `multiagent sessions show`, and
`multiagent sessions items`.

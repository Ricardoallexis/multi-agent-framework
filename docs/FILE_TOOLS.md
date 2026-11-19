# Built-in file tools

Workflow steps can list, read, write, and move text files inside one user-declared folder without Git.

## Declaring the folder

Set `FILE_TOOLS_ROOT` in `.local/.env` (or pass `Settings(file_tools_root=...)`):

```dotenv
FILE_TOOLS_ROOT=D:\my-notes
```

When `FILE_TOOLS_ROOT` is unset or empty, no `file.*` tools are registered and any step declaring them fails with `unknown_tool` or a missing capability. When set, `Settings` resolves the path (expanding `~`) and `build_system` registers the four built-in tools (`declared_by="builtin"`, each with a capability matching its `tool_id`).

`NativeBackend` (`EXECUTION_BACKEND=native`, the default) executes step tools. `MockBackend` (`EXECUTION_BACKEND=mock`) does not declare the `tools` capability.

## Tools

All four tools reject unknown argument keys (`additionalProperties: false`) and confine every path to `FILE_TOOLS_ROOT`.

### `file.list` (`read`)

Lists files and subfolders inside a relative folder.

- **Arguments:**
  - `path` (`string`, optional, default `""`): relative directory path; empty lists the root folder.
  - `recursive` (`boolean`, optional, default `false`): descend into subdirectories (never follows symlinks or directory junctions).
  - `max_depth` (`integer`, optional, default `4`, max `10`, min `1`): maximum recursion depth.
  - `max_entries` (`integer`, optional, default `200`, max `1000`, min `1`): maximum entries returned.
- **Output:** `{path, entries, truncated}`
  - `path` (`string`): normalized relative POSIX path of the listed folder (`""` for root).
  - `entries` (`array`): items `{name, type, size}` where `type` is `"file"` or `"dir"` and `size` is in bytes (`0` for directories).
  - `truncated` (`boolean`): `true` when `max_entries`, `max_depth`, or `max_output_bytes` stops the listing early.

### `file.read` (`read`)

Reads a UTF-8 `.md`, `.csv`, `.json`, or `.txt` file up to `max_output_bytes` (default `262,144` bytes).

- **Arguments:** `path` (`string`, required).
- **Output:** `{path, content, size}` — normalized relative POSIX `path`, decoded UTF-8 `content`, and byte `size`.

Tool outputs enter the step prompt wrapped as untrusted data (`<<<UNTRUSTED_DATA ... trust=untrusted ...>>>`).

### `file.write` (`write`)

Creates or replaces a UTF-8 `.md`, `.csv`, `.json`, or `.txt` file up to `max_output_bytes`, creating missing parent directories inside the root. For `.json` files, `content` must be valid JSON and is checked before touching disk.

- **Arguments:** `path` (`string`, required), `content` (`string`, required).
- **Output:** `{path, size}` — normalized relative POSIX `path` and written UTF-8 byte `size`.
- **Effects:** `write` on `/path`.

### `file.move` (`write`)

Moves or renames a `.md`, `.csv`, `.json`, or `.txt` file without overwriting an existing destination, creating missing parent directories for `destination` inside the root.

- **Arguments:** `source` (`string`, required), `destination` (`string`, required).
- **Output:** `{source, destination}` — normalized relative POSIX paths.
- **Effects:** `write` on `/source` and `/destination`.

## Path confinement and formats

- Paths must be relative to `FILE_TOOLS_ROOT`. Null bytes, leading `/` or `\`, drive prefixes, UNC paths, `..` segments, and symlinks or directory junctions resolving outside the root are refused.
- Only `.md`, `.csv`, `.json`, and `.txt` files (case-insensitive) in UTF-8 are accepted.

## Approvals

`file.list` and `file.read` have `read` impact and run without pausing. `file.write` and `file.move` have `write` impact: each call pauses the run in `status = "waiting_human"` with `waiting_reason = "tool_approval"` before touching disk.

- **Inspect the pending request:** `GET /api/v1/runs/{run_id}` (and `GET /gui/api/runs/{run_id}` with the GUI bearer token) returns `tool_approval` with `permission_request_id`, `tool_id`, `tool_version`, `step_id`, `attempt`, `summary`, `impacts`, `actions`, and `arguments_sha256` (raw arguments are omitted).
- **CLI:** run `multiagent approve <run> --permission-request <id>`. Calling `multiagent approve <run>` without `--permission-request` prints the pending `tool_approval` payload and exits with code `2` without approving.
- **API and GUI:** `POST /api/v1/runs/{run_id}/approve` never approves a tool call (`409 person_required`). Approve either in the operator GUI at `/gui/` (**Aprobar esta llamada** under *Acciones humanas*) or via `POST /gui/api/runs/{run_id}/approve` with `Authorization: Bearer <token>` and JSON body `{"permission_request_id": "<id>"}`.
- **Preview a `file.write` call:** the operator GUI shows what the call would write before you approve it, in the run thread (`#new/run/<id>`) and under *Acciones humanas*: the content, and for an existing file the diff against it with a notice when lines would be removed. It comes from `GET /gui/api/runs/{run_id}/tool-approval/preview?permission_request_id=<id>` (GUI bearer token), which only reads: it rebuilds the arguments from the step's declaration and the run inputs and writes nothing. The preview is shown only when the rebuilt arguments have the same SHA-256 as `arguments_sha256` of the pending request; otherwise `available` is `false` with a `reason` (`no_pending_approval`, `permission_request_mismatch`, `file_tools_disabled`, `file_write_unavailable`, `run_inputs_unavailable`, `declaration_unavailable`, `arguments_unavailable` or `arguments_mismatch`), and a tool other than `file.write` answers `unsupported_tool`. Arguments that take the output of an earlier step cannot be rebuilt yet, so they show no preview. Approving and rejecting work the same with or without it.
- **Resume:** approval returns the run to `queued` with a single-use grant valid for 10 minutes; the worker re-runs the paused step and executes the tool.
- **Reject:** `multiagent reject <run>`, `POST /api/v1/runs/{run_id}/reject`, `POST /gui/api/runs/{run_id}/reject`, or the GUI reject control fails the run without writing to disk.

## Error codes

When a `file.*` tool raises `ToolExecutionError`, `ToolInvoker` emits `tool_call_failed` with `code = "execution_failed"` and records the domain code in `details.code` (keeping the raw message opaque):

| `details.code` | Cause |
| --- | --- |
| `outside_root` | Path is absolute, has a drive/UNC prefix, `..`, null byte, or resolves via link/junction outside `FILE_TOOLS_ROOT`. |
| `unsupported_format` | Missing extension, extension outside `.md`/`.csv`/`.json`/`.txt`, or non-UTF-8 content. |
| `not_found` | Target directory (`file.list`), file (`file.read`), or `source` (`file.move`) does not exist, or root is not a directory. |
| `already_exists` | `file.move` destination already exists (or is a symlink), or `file.write` target is an existing directory. |
| `too_large` | File size, content length, or serialized output exceeds `max_output_bytes`. |
| `invalid_json` | `file.write` to a `.json` path with invalid JSON content (fails before touching disk). |

Schema violations fail with `invalid_arguments` before execution. Calling `file.*` when `FILE_TOOLS_ROOT` is unset fails step resolution with `unknown_tool`.

## Workflow step example

```yaml
steps:
  - id: draft
    # ...
    tools:
      - tool: file.read
        arguments:
          path: "notes/brief.md"
      - capability: file.write
        arguments:
          path: "drafts/post.md"
          content: "${topic}"
```

A string argument written exactly as `"${input_name}"` reads from the run's string inputs; all other values are passed literally.

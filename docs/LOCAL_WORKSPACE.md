# Local workspace

The repository deliberately separates public source code from private runtime data.

## Writable workspace

An editable checkout uses the ignored `.local/` directory by default:

```text
.local/
├── .env
└── data/
    ├── multiagent.db
    ├── runs/
    └── assets/
```

The entire directory is ignored by Git.

A normal wheel installation defaults to `~/.multi-agent-framework/` instead.
Read-only catalog, workflow, prompt, skill, and migration files come from the
installed package. Neither mode writes runtime data to those resource files.

## What belongs there

- API keys and private configuration.
- Development or production SQLite databases.
- Runs and model responses.
- Private assets, logos, or customer material.
- Manual-test data.
- Backups and snapshots.
- Internal notes not intended for the public repository.

## Changing the location

Set `LOCAL_DIR` in `.local/.env` for an editable checkout, or in
`~/.multi-agent-framework/.env` for a wheel installation, to move the writable
workspace:

```dotenv
LOCAL_DIR=D:\multiagent-private
```

This allows the repository to be deleted or cloned again without losing local runtime data.

## Practical rule

If a file contains information that should never appear in a public commit, its safety should not depend on remembering to clean it before committing. Generate or store it directly inside `.local/` or the path configured through `LOCAL_DIR`.

## Built-in file tools

Workflow steps can list, read, write, and move text files inside a single user-declared folder without Git.

### Declaring the folder

Set `FILE_TOOLS_ROOT` in `.local/.env` (or pass `Settings(file_tools_root=...)`):

```dotenv
FILE_TOOLS_ROOT=D:\my-notes
```

When `FILE_TOOLS_ROOT` is unset or empty, no file tools are registered and any step referencing `file.*` fails as an unknown tool or missing capability. When set, `build_system` resolves the folder and registers the four built-in tools (`declared_by="builtin"`, each declaring a capability equal to its tool id):

- `file.list` (`read`): lists a relative subfolder (defaults to the root) with `name`, `type` (`file` or `dir`), and `size`. Non-recursive unless `recursive: true` is passed, bounded by `max_depth` and `max_entries`.
- `file.read` (`read`): reads a UTF-8 text file up to `max_output_bytes`.
- `file.write` (`write`, effect on `/path`): creates or replaces a UTF-8 text file up to `max_output_bytes`.
- `file.move` (`write`, effects on `/source` and `/destination`): moves or renames a file without overwriting an existing destination.

### Path confinement and formats

- All paths must be relative to `FILE_TOOLS_ROOT`. Absolute paths, `..` segments, drive prefixes, UNC paths, and symlinks or directory junctions that resolve outside the root fail with `details.code = "outside_root"`.
- Supported formats are `.md`, `.csv`, `.json`, and `.txt` in UTF-8. Other extensions or non-UTF-8 content fail with `unsupported_format`; invalid JSON on `file.write` fails with `invalid_json` before touching disk.
- Other stable failure codes in `tool_call_failed` `details.code`: `not_found`, `already_exists`, and `too_large`.

### Approvals and workflow example

`file.list` and `file.read` are `read` tools and run without pausing. `file.write` and `file.move` have `write` impact: a step calling them pauses in `waiting_human` / `tool_approval` until a person approves the specific `permission_request_id` (via `multiagent approve RUN_ID --permission-request ...`, the GUI, or `POST /gui/api/runs/{id}/approve`).

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

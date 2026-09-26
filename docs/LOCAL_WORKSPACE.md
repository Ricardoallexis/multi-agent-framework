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

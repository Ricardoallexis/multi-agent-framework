# Permission profiles

These are reviewed starting points for Claude Code and Codex CLI. They are
inactive templates. GitHub Copilot CLI uses options for each run instead of a
committed project permission profile.

## Claude Code

`permissions.allow` permits read-only tools and routine project commands:
reading and searching files, checking Git status, and running pytest.
`permissions.ask` pauses for commits, diffs, history, and edits outside the
project. Diffs and history ask before running so options such as
`--no-index` and `--output`, which write files, are not auto-approved. Pytest is allowed for normal runs, but
`--basetemp` is denied because pytest removes that directory; pytest also runs
project code, including plugins and `conftest.py`. `permissions.deny` blocks
pushing, hard resets, recursive deletion, bypass modes, reads of environment
files and secret folders, and PowerShell equivalents of the risky commands.
Git clean is denied because it can remove local run data.
Downloads through curl, wget, or PowerShell web-request commands ask for
approval rather than relying on a pipeline rule. It also denies reading
`.local/`, where the framework keeps local run data; remove that rule only if
your workflow needs agents to read it. `blockReadsOutsideWorkingDirectories`
and the user-level credential denies limit broad read permissions.
`defaultMode` is `default`, so unlisted actions do not receive automatic
approval. The `../` edit rule is omitted: edits outside the project already
require approval in the default mode.

These denies are defense in depth, not a sandbox. Pattern rules can miss
`git -C <dir> push`, `bash -c` or `pwsh -c` wrappers, and flags placed later
on a command line. The explicit patterns cover common forms, but do not treat
them as a guarantee. Real protections are the application's permission
prompts, giving workers no credentials or account files, and giving them no
push rights. Use the operating-system sandbox or a `PreToolUse` hook as an
additional boundary where available.

Machine-specific directories belong in `.claude/settings.local.json`, not in
the template or Git. Keep that file out of Git.

## Codex CLI

`approval_policy = "on-request"` requests approval when an action needs it.
`sandbox_mode = "workspace-write"` allows work in the project workspace while
keeping other locations outside the writable area. Network access is disabled.
The Windows sandbox is set to `elevated`; check the `sandbox` line in
`codex doctor` on the first run. The template's `writable_roots` is empty; add
machine-specific roots locally only, and never commit them. Codex reads a
project `.codex/config.toml` for trusted projects. With `-p <name>`, put the
profile in `$CODEX_HOME/<name>.config.toml`.

## GitHub Copilot CLI

Set permissions per run. Use `--allow-tool` and `--deny-tool` for specific
tools, not broad tool access. Use `--add-dir` only for folders that run needs.
`--no-ask-user` disables Copilot's question form; it does not disable
permission prompts. Use it only when the run's actions and permissions have
already been reviewed. `--assisted-approval` is not a security boundary.

## Never allowed

For all apps, never use Claude's `bypassPermissions` mode or
`--dangerously-skip-permissions`; Copilot's `--allow-all-tools`,
`--allow-all-paths`, `--allow-all`, `--allow-all-urls`, `--yolo`, or
`COPILOT_ALLOW_ALL`; or Codex's `danger-full-access`,
`--dangerously-bypass-approvals-and-sandbox`, or
`--dangerously-bypass-hook-trust`. Do not use Copilot `--assisted-approval`
or Codex `--approve-for-me` as a security control: both hand approvals to an
automatic reviewer. Use them only for runs whose sandbox, writable roots and
credentials already limit what can happen. The Claude template denies both
flags when an agent types them; a reviewed launcher script that sets them for
a worker run is not affected. Agents must not push changes.
Never give workers credentials or account files.

## First trusted run

The user opens the project folder and accepts the application's trust prompt
themself. An agent never accepts the prompt or establishes trust on the user's
behalf.

## Activate manually

Activation is a manual step for the user. Copy
`docs/permissions/claude-settings.template.json` to
`.claude/settings.json`. Copy `docs/permissions/codex-config.template.toml` to the project
`.codex/config.toml` for a trusted project, or to
`$CODEX_HOME/<name>.config.toml` for a named profile. Review each file, then
run the application once with the user. Do not commit machine paths or local
settings. Configure Copilot CLI options for each run instead; there is no
active Copilot project settings file here.

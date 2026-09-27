# Versioning

## Product version

Public releases use:

```text
M<generation>-B<backend>-F<frontend>-<stage>
```

Example:

```text
M1-B04-F02-alpha
```

- `M`: product architecture generation.
- `B`: backend/runtime/API revision.
- `F`: frontend/UI revision.
- `stage`: `alpha`, `beta`, `rcN`, or omitted for a stable release.

## Rules

A backend-only change increments `B`:

```text
M1-B04-F02-alpha -> M1-B05-F02-alpha
```

A UI-only change increments `F`:

```text
M1-B05-F02-alpha -> M1-B05-F03-alpha
```

If both components change, increment both:

```text
M1-B05-F03-alpha -> M1-B06-F04-alpha
```

`M` changes only for an incompatible generation or a significant architectural reorganization.

## Python package version

Python packaging tools require PEP 440 versions, so the package keeps a separate technical version. The two are different by design and are not derived from each other, but they must agree on the stage:

| Product stage | Python pre-release | Example |
| --- | --- | --- |
| `alpha` | `aN` | `M1-B02-F00-alpha` shipped as `0.2.0a1` |
| `beta` | `bN` | `…-beta` → `0.5.0b1` |
| `rcN` | `rcN` | `…-rc2` → `1.0.0rc2` |
| stable (no stage) | none | `M1-B10-F04` → `1.0.0` |

## Single source of truth

Both versions are defined only in `multiagent/version.py`:

- `PLATFORM_GENERATION`, `BACKEND_REVISION`, `FRONTEND_REVISION`, and `STAGE` build the product release (`__version__`);
- `__package_version__` is the Python package version.

Everything else reads or is checked against that file:

| Place | How it stays consistent |
| --- | --- |
| `pyproject.toml` | Reads `__package_version__` dynamically; a literal version is rejected by the check. |
| `multiagent --version`, the API `/health` | Import `multiagent.version`. |
| `README.md` header | The lines marked `<!-- version:release -->` and `<!-- version:package -->` are checked. |
| `CHANGELOG.md` | The newest released section `## [<release>] - <date>` and its `Python package:` line are checked. |
| `PROGRESS.md` | The last row of the Milestones table is checked. |
| Git tag | On a tag build, the tag must equal the release. |

Historical references are not checked: older changelog sections, earlier milestones, the first baseline in the roadmap, and the examples on this page. Any Markdown line that calls a version the *current release* must carry `<!-- version:release -->`; otherwise the check reports it. This prevents new, unchecked copies.

`scripts/check_version_consistency.py` implements these rules. It exits with status 1 and one `ERROR:` line per problem. CI runs it on every push, pull request, and tag before the tests, so an inconsistent version cannot pass CI.

## Tags and releases

Git tags use the exact product version:

```text
M1-B01-F00-alpha
M1-B02-F00-alpha
M1-B02-F01-alpha
```

Published tags and releases are immutable and should not be reused.

## Preparing a release

1. Decide the new versions from the rules above: which of `B` and `F` change, and the matching Python version.
2. Edit `multiagent/version.py` only.
3. In `CHANGELOG.md`, rename `[Unreleased]` to `## [<release>] - <YYYY-MM-DD>`, add `Python package: \`<version>\``, keep Added/Changed and a **Roadmap** subsection, and open a new empty `[Unreleased]`.
4. Update the two marked lines at the top of `README.md` and add the release to the Milestones table in `PROGRESS.md`.
5. If roadmap checkboxes changed, run `python scripts/update_progress.py`.
6. Run the checks locally (Windows PowerShell, from the repository root):

   ```powershell
   .venv\Scripts\python.exe scripts\check_version_consistency.py
   .venv\Scripts\python.exe scripts\update_progress.py --check
   .\run_tests_windows.bat
   ```

   `run_tests_windows.bat` runs both checks before the test suite.
7. Review `git status` and `git diff`, and confirm that no secrets, `.env` files, or private data (`.local/`) are included.
8. Commit and push. Confirm that CI is green.
9. Tag the release with the exact product version (`git tag <release>`) and push the tag. CI checks that the tag matches `multiagent/version.py`.

Documentation-only or maintenance changes between releases do not change the version; record them under `[Unreleased]`.

An optional local Git hook can run the check before each commit, for example with `pre-commit` and a local hook whose entry is `python scripts/check_version_consistency.py`. Hooks are not distributed with a clone, so CI remains the authority.

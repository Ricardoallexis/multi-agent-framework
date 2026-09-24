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

Python packaging tools require PEP 440 versions, so the package keeps a separate technical version. For example:

```text
Current baseline:
Product: M1-B01-F00-alpha
Python:  0.1.0a1
```

The single source of truth is `multiagent/version.py`.

## Tags and releases

Git tags use the exact product version:

```text
M1-B01-F00-alpha
M1-B02-F00-alpha
M1-B02-F01-alpha
```

Published tags and releases are immutable and should not be reused.

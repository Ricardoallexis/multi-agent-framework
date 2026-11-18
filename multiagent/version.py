"""Single source of truth for product and package version information.

Release procedure and consistency checks: docs/VERSIONING.md and
scripts/check_version_consistency.py (run in CI).
"""

PLATFORM_GENERATION = 1
BACKEND_REVISION = 3
FRONTEND_REVISION = 1
# "alpha", "beta", "rcN", or "" for a stable release.
STAGE = "rc1"

# PEP 440 version used by Python packaging tools. Its pre-release must match STAGE
# (alpha -> aN, beta -> bN, rcN -> rcN, stable -> none).
__package_version__ = "1.0.0rc1"

# Human-facing release version used by CLI/API/GitHub releases.
__version__ = (
    f"M{PLATFORM_GENERATION}-B{BACKEND_REVISION:02d}-F{FRONTEND_REVISION:02d}"
    + (f"-{STAGE}" if STAGE else "")
)


def version_info() -> dict[str, int | str]:
    return {
        "generation": PLATFORM_GENERATION,
        "backend": BACKEND_REVISION,
        "frontend": FRONTEND_REVISION,
        "stage": STAGE,
        "release": __version__,
        "package": __package_version__,
    }

"""Single source of truth for product and package version information."""

PLATFORM_GENERATION = 1
BACKEND_REVISION = 1
FRONTEND_REVISION = 0
STAGE = "alpha"

# PEP 440 version used by Python packaging tools.
__package_version__ = "0.1.0a1"

# Human-facing release version used by CLI/API/GitHub releases.
__version__ = (
    f"M{PLATFORM_GENERATION}-B{BACKEND_REVISION:02d}-"
    f"F{FRONTEND_REVISION:02d}-{STAGE}"
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

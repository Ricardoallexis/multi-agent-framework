# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Application services: the public Python API of the framework.

One facade used by every interface (HTTP API, CLI and later UI, MCP server and
notebooks), so they all behave the same way::

    services = ApplicationServices.create(bundles={"my_team": "path/to/bundle"})
    services.definitions.list_workflows()
    run = services.runs.create(RunRequest(workflow_id="...", project_name="demo"))
    services.runs.approve(run["id"])

Methods return JSON-ready data and report known failures as ``ServiceError``
(``code``, ``message``, ``status``, ``details``; see ``errors``).

Bundles are registered by name, in Python only. A bundle may ship a
``contracts.py`` that is imported, so register trusted folders only; remote
interfaces refer to bundles by name and never by path.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from .definitions import DefinitionServices
from .errors import NOT_FOUND, ServiceError, service_errors, translate
from .runs import RunServices

if TYPE_CHECKING:
    from ..bootstrap import System
    from ..config import Settings

__all__ = [
    "ApplicationServices",
    "DefinitionServices",
    "RunServices",
    "ServiceError",
    "service_errors",
    "translate",
]

_BUNDLE_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]{0,119}$")


class ApplicationServices:
    """Facade over one ``System``: ``definitions`` and ``runs``."""

    def __init__(self, system: System, *, bundles: Mapping[str, str | Path] | None = None):
        self.system = system
        self._bundles: dict[str, Path] = {}
        for name, path in (bundles or {}).items():
            if not _BUNDLE_NAME.fullmatch(name):
                raise ValueError(f"Invalid bundle name: {name!r}")
            self._bundles[name] = Path(path).resolve()
        self.definitions = DefinitionServices(self)
        self.runs = RunServices(self)

    @classmethod
    def create(cls, settings: Settings | None = None, *,
               bundles: Mapping[str, str | Path] | None = None, **system_options: Any) -> ApplicationServices:
        """Build the runtime (``build_system``) and wrap it."""
        from ..bootstrap import build_system

        return cls(build_system(settings, **system_options), bundles=bundles)

    @property
    def settings(self) -> Settings:
        return self.system.settings

    def bundle_names(self) -> list[str]:
        return sorted(self._bundles)

    def bundle_path(self, name: str) -> Path:
        """Folder of a registered bundle; ``ServiceError(not_found)`` otherwise.

        For service implementations only: paths are never returned to callers.
        """
        try:
            return self._bundles[name]
        except KeyError:
            raise ServiceError(NOT_FOUND, f"Bundle not found: {name}", status=404) from None

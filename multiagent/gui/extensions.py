# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI extension folders (FLU-T04): a view, a right-panel slot or a help text is added by a folder, not by editing shared files.

A task that adds something to the GUI creates ``static/extensions/<name>/`` and puts everything in it:

``index.js``    the entry module (required): calls ``registerView``, ``registerSlot`` and ``registerHelp`` of ``js/extensions.js``
``style.css``   the stylesheet of the view (optional); the page adds it, ``index.html`` is not edited
``help.js``     the help texts of the view (optional): ``export const HELP = { ... }`` as strict JSON, keys ``<name>.*``
anything else   helpers of the extension (models, components); only ``index.js`` and ``style.css`` are loaded by the page

``<name>`` is lowercase letters and ``_`` (``projects``, ``doc_alerts``). The folders are found when the manifest is
asked for (``GET /gui/api/extensions``), so a new folder needs no restart and no list anywhere. The page loads the
entry modules with ``import()`` and the sheets as ``<link>`` elements: same origin, no inline code, so the strict CSP
(``script-src 'self'; style-src 'self'``) and Trusted Types stay as they are. The page waits at most 3 s for all of it and
draws the core screens anyway (js/extensions.js). See docs/GUI.md.
"""
from __future__ import annotations

import re
from pathlib import Path

EXTENSIONS_DIRNAME = "extensions"
ENTRY_FILE = "index.js"
STYLE_FILE = "style.css"
HELP_FILE = "help.js"
NAME_PATTERN = re.compile(r"[a-z][a-z_]{0,39}")
URL_PREFIX = "/gui/static/" + EXTENSIONS_DIRNAME


def extension_dirs(static_dir: Path) -> list[Path]:
    """The extension folders under ``static_dir``: a valid name and an entry module, sorted by name."""
    root = Path(static_dir) / EXTENSIONS_DIRNAME
    if not root.is_dir():
        return []
    return sorted((entry for entry in root.iterdir()
                   if entry.is_dir() and NAME_PATTERN.fullmatch(entry.name) and (entry / ENTRY_FILE).is_file()),
                  key=lambda entry: entry.name)


def discover_extensions(static_dir: Path) -> list[dict[str, str | None]]:
    """The manifest the page loads: id, entry module URL and stylesheet URL (``None`` when there is none)."""
    return [{
        "id": folder.name,
        "script": f"{URL_PREFIX}/{folder.name}/{ENTRY_FILE}",
        "style": f"{URL_PREFIX}/{folder.name}/{STYLE_FILE}" if (folder / STYLE_FILE).is_file() else None,
    } for folder in extension_dirs(static_dir)]

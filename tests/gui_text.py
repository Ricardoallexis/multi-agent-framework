# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Read GUI source as the selected language for static source assertions."""
from __future__ import annotations

import json
import re
from pathlib import Path

from multiagent.gui import STATIC_DIR


def _dictionaries(source: str, lang: str) -> list[Path]:
    """Find dictionary modules used by this source, including future views."""
    keys = set(re.findall(r'\bt\("([^"]+)"', source))
    if not keys and "localizedHelp(" not in source:
        return []
    paths = []
    for path in sorted((STATIC_DIR / "i18n" / lang).glob("*.js")):
        contents = path.read_text(encoding="utf-8")
        if any(f'"{key}"' in contents for key in keys) or (
            "localizedHelp(" in source and '"help.' in contents
        ):
            paths.append(path)
    return paths


def _words(paths: list[Path]) -> dict[str, str]:
    words: dict[str, str] = {}
    for path in paths:
        source = path.read_text(encoding="utf-8")
        for key, value in re.findall(r'"([^"\\]+)"\s*:\s*("(?:\\.|[^"\\])*")', source):
            words[key] = json.loads(value)
    return words


def ui_text(path: Path | str, lang: str = "es") -> str:
    """Reconstruct literal UI strings in source without executing browser code."""
    source = Path(path).read_text(encoding="utf-8")
    selected = _dictionaries(source, lang)
    if not selected:
        return source
    english = _dictionaries(source, "en")
    words = {**_words(english), **_words(selected)}
    rendered = source
    for key, value in words.items():
        escaped = re.escape(key)
        rendered = re.sub(r'([\w-]+)=\$\{t\("' + escaped + r'"\)\}',
                          lambda match: match[1] + '=' + json.dumps(value, ensure_ascii=False), rendered)
        rendered = re.sub(r'\$\{t\("' + escaped + r'"\)\}', lambda _: value, rendered)
        rendered = re.sub(r't\("' + escaped + r'"\)', lambda _: json.dumps(value, ensure_ascii=False), rendered)
    # Preserve the old literal assertions in their source context, then append
    # every dictionary string for text moved out of that source expression.
    dictionary_text = "\n".join(
        json.loads(value)
        for module in selected
        for value in re.findall(r'"(?:\\.|[^"\\])*"', module.read_text(encoding="utf-8"))
    )
    return "\n".join((rendered, dictionary_text))

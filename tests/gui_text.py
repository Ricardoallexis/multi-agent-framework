# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Read GUI source as the selected language for static source assertions."""
from __future__ import annotations

import json
import re
from pathlib import Path

from multiagent.gui import STATIC_DIR

_HELP_PART = re.compile(r'^import \{ HELP as \w+ \} from "\./(help-text-[\w-]+\.js)";$', re.M)
_HELP_BLOCK = re.compile(r"^export const HELP = (\{.*?^\});$", re.S | re.M)
_HELP_ORDER = re.compile(r"^const ORDER = (\[.*?^\]);$", re.S | re.M)
_HELP_JOIN = re.compile(r"^export const HELP = (?!\{).*?;$", re.M)


def _with_help_parts(path: Path, source: str) -> str:
    """help-text.js joins one part per view (help-text-<part>.js) into HELP; read it as the one catalog it was.

    The joined HELP comes back as a strict JSON block in the catalog order, followed by the text of every part, so a
    test that looks for an entry or parses ``export const HELP = {...};`` works whichever part holds the entry."""
    parts = _HELP_PART.findall(source)
    if not parts:
        return source
    texts = [(path.parent / name).read_text(encoding="utf-8") for name in parts]
    joined: dict = {}
    for name, text in zip(parts, texts):
        for key, entry in json.loads(_HELP_BLOCK.search(text)[1]).items():
            if key in joined:
                raise ValueError(f"help topic {key} is in two parts ({name})")
            joined[key] = entry
    order = _HELP_ORDER.search(source)
    listed = [key for key in json.loads(order[1]) if key in joined] if order else []
    keys = listed + [key for key in joined if key not in set(listed)]
    block = "export const HELP = " + json.dumps({key: joined[key] for key in keys}, ensure_ascii=False, indent=2) + ";"
    return "\n".join([_HELP_JOIN.sub(lambda _: block, source, count=1), *texts])


def _dictionary_source(path: Path, lang: str) -> str:
    """The text of one dictionary in one language: a view file as it is, an extension i18n.js only its ``lang`` block."""
    contents = path.read_text(encoding="utf-8")
    if path.name != "i18n.js":
        return contents
    block = re.search(r"^const " + lang + r" = (\{.*?^\});$", contents, re.S | re.M)
    return block[1] if block else ""


def _dictionaries(source: str, lang: str) -> list[tuple[Path, str]]:
    """Find dictionary modules used by this source, including future views and extensions."""
    keys = set(re.findall(r'\bt\("([^"]+)"', source))
    if not keys and "localizedHelp(" not in source:
        return []
    found = []
    modules = [*sorted((STATIC_DIR / "i18n" / lang).glob("*.js")), *sorted((STATIC_DIR / "extensions").glob("*/i18n.js"))]
    for path in modules:
        contents = _dictionary_source(path, lang)
        if any(f'"{key}"' in contents for key in keys) or (
            "localizedHelp(" in source and '"help.' in contents
        ):
            found.append((path, contents))
    return found


def _words(found: list[tuple[Path, str]]) -> dict[str, str]:
    words: dict[str, str] = {}
    for _path, source in found:
        for key, value in re.findall(r'"([^"\\]+)"\s*:\s*("(?:\\.|[^"\\])*")', source):
            words[key] = json.loads(value)
    return words


def ui_text(path: Path | str, lang: str = "es") -> str:
    """Reconstruct literal UI strings in source without executing browser code."""
    source = _with_help_parts(Path(path), Path(path).read_text(encoding="utf-8"))
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
        for _path, module in selected
        for value in re.findall(r'"(?:\\.|[^"\\])*"', module)
    )
    return "\n".join((rendered, dictionary_text))

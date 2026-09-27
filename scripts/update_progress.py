# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Keep the progress shown in ROADMAP.md and PROGRESS.md in sync with the roadmap checkboxes.

Progress is counted, not estimated: every ``- [x]`` / ``- [ ]`` item under a ``## `` section of
ROADMAP.md weighs the same, nested items included. The script rewrites:

* one progress line directly under each roadmap section heading (marked ``<!-- progress -->``);
* the table between ``<!-- progress:table:start -->`` and ``<!-- progress:table:end -->`` in PROGRESS.md.

Usage::

    python scripts/update_progress.py          # update both files
    python scripts/update_progress.py --check  # exit 1 if either file is out of date (used in CI)
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROADMAP = ROOT / "ROADMAP.md"
PROGRESS = ROOT / "PROGRESS.md"

LINE_MARKER = "<!-- progress -->"
TABLE_START = "<!-- progress:table:start -->"
TABLE_END = "<!-- progress:table:end -->"
BAR_WIDTH = 10

HEADING = re.compile(r"^## (?P<title>.+?)\s*$")
CHECKBOX = re.compile(r"^\s*[-*] \[(?P<mark>[ xX])\] ")


@dataclass(frozen=True)
class Area:
    title: str
    done: int
    total: int

    @property
    def percent(self) -> int:
        return round(100 * self.done / self.total) if self.total else 0

    @property
    def bar(self) -> str:
        filled = round(BAR_WIDTH * self.done / self.total) if self.total else 0
        return "█" * filled + "░" * (BAR_WIDTH - filled)

    @property
    def summary(self) -> str:
        return f"`{self.bar}` {self.done}/{self.total} · {self.percent}%"


def count_areas(roadmap_text: str) -> list[Area]:
    areas: list[Area] = []
    title, done, total = None, 0, 0
    for line in roadmap_text.splitlines():
        heading = HEADING.match(line)
        if heading:
            if title is not None and total:
                areas.append(Area(title, done, total))
            title, done, total = heading.group("title"), 0, 0
            continue
        box = CHECKBOX.match(line)
        if box and title is not None:
            total += 1
            done += box.group("mark") in "xX"
    if title is not None and total:
        areas.append(Area(title, done, total))
    return areas


def render_roadmap(roadmap_text: str, areas: list[Area]) -> str:
    by_title = {area.title: area for area in areas}
    lines = [line for line in roadmap_text.splitlines() if not line.rstrip().endswith(LINE_MARKER)]
    output: list[str] = []
    for index, line in enumerate(lines):
        output.append(line)
        heading = HEADING.match(line)
        if heading and heading.group("title") in by_title:
            output.extend(["", f"**Progress:** {by_title[heading.group('title')].summary} {LINE_MARKER}"])
            # Keep a single blank line between the progress line and the section body.
            if index + 1 < len(lines) and lines[index + 1].strip():
                output.append("")
    text = "\n".join(output) + "\n"
    return re.sub(r"(" + re.escape(LINE_MARKER) + r")\n\n\n+", r"\1\n\n", text)


def render_table(areas: list[Area]) -> str:
    rows = ["| Area | Progress | Items |", "| --- | --- | --- |"]
    for area in areas:
        rows.append(f"| {area.title} | `{area.bar}` {area.percent}% | {area.done} of {area.total} |")
    done = sum(area.done for area in areas)
    total = sum(area.total for area in areas)
    rows.append(f"| **All roadmap items** | **{round(100 * done / total) if total else 0}%** | **{done} of {total}** |")
    return "\n".join(rows)


def render_progress(progress_text: str, areas: list[Area]) -> str:
    pattern = re.compile(re.escape(TABLE_START) + r".*?" + re.escape(TABLE_END), re.S)
    if not pattern.search(progress_text):
        raise SystemExit(f"{PROGRESS.name} has no {TABLE_START} ... {TABLE_END} block")
    block = f"{TABLE_START}\n{render_table(areas)}\n{TABLE_END}"
    return pattern.sub(lambda _: block, progress_text, count=1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="only report whether the files are up to date")
    args = parser.parse_args(argv)

    roadmap_text = ROADMAP.read_text(encoding="utf-8")
    progress_text = PROGRESS.read_text(encoding="utf-8")
    areas = count_areas(roadmap_text)
    expected = {ROADMAP: render_roadmap(roadmap_text, areas), PROGRESS: render_progress(progress_text, areas)}
    stale = [path for path, text in expected.items() if path.read_text(encoding="utf-8") != text]

    if args.check:
        if stale:
            names = ", ".join(path.name for path in stale)
            print(f"Progress is out of date in {names}. Run: python scripts/update_progress.py", file=sys.stderr)
            return 1
        print("Progress is up to date.")
        return 0
    for path in stale:
        path.write_text(expected[path], encoding="utf-8", newline="\n")
        print(f"Updated {path.name}")
    if not stale:
        print("Progress is already up to date.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

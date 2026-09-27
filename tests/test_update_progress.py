"""The progress generator counts roadmap checkboxes and keeps both documents in sync."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "update_progress.py"
spec = importlib.util.spec_from_file_location("update_progress", SCRIPT)
progress = importlib.util.module_from_spec(spec)
# Registered like a normal import: @dataclass looks the module up in sys.modules.
sys.modules[spec.name] = progress
spec.loader.exec_module(progress)

ROADMAP = """# Roadmap

Intro.

## Done area

- [x] First.
- [x] Second.

## Mixed area
- [ ] Parent item.
  - [x] Nested item that is already done.
- [ ] Another item.

## Narrative only

No checkboxes here.
"""
PROGRESS = """# Progress

<!-- progress:table:start -->
stale
<!-- progress:table:end -->
"""


@pytest.fixture
def files(tmp_path, monkeypatch):
    roadmap = tmp_path / "ROADMAP.md"
    report = tmp_path / "PROGRESS.md"
    roadmap.write_text(ROADMAP, encoding="utf-8")
    report.write_text(PROGRESS, encoding="utf-8")
    monkeypatch.setattr(progress, "ROADMAP", roadmap)
    monkeypatch.setattr(progress, "PROGRESS", report)
    return roadmap, report


def test_counts_each_checkbox_including_nested_ones():
    areas = progress.count_areas(ROADMAP)
    assert [(a.title, a.done, a.total, a.percent) for a in areas] == [
        ("Done area", 2, 2, 100),
        ("Mixed area", 1, 3, 33),
    ]


def test_update_writes_a_line_per_area_and_the_table(files):
    roadmap, report = files
    assert progress.main([]) == 0
    text = roadmap.read_text(encoding="utf-8")
    assert "## Done area\n\n**Progress:** `██████████` 2/2 · 100% <!-- progress -->\n\n- [x] First." in text
    assert "## Mixed area\n\n**Progress:** `███░░░░░░░` 1/3 · 33% <!-- progress -->\n\n- [ ] Parent item." in text
    assert "## Narrative only\n\nNo checkboxes" in text
    table = report.read_text(encoding="utf-8")
    assert "| Mixed area | `███░░░░░░░` 33% | 1 of 3 |" in table
    assert "| **All roadmap items** | **60%** | **3 of 5** |" in table
    assert "stale" not in table


def test_update_is_idempotent_and_check_passes_afterwards(files):
    roadmap, report = files
    progress.main([])
    first = (roadmap.read_text(encoding="utf-8"), report.read_text(encoding="utf-8"))
    progress.main([])
    assert (roadmap.read_text(encoding="utf-8"), report.read_text(encoding="utf-8")) == first
    assert progress.main(["--check"]) == 0


def test_check_fails_when_a_checkbox_changes(files):
    roadmap, _ = files
    progress.main([])
    roadmap.write_text(roadmap.read_text(encoding="utf-8").replace("- [ ] Another item.", "- [x] Another item."),
                       encoding="utf-8")
    assert progress.main(["--check"]) == 1
    progress.main([])
    assert "2/3 · 67%" in roadmap.read_text(encoding="utf-8")


def test_repository_documents_are_up_to_date():
    assert progress.main(["--check"]) == 0

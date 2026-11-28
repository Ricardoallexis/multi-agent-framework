# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Temporary CLI runs release their SQLite files before removing the directory."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from multiagent import cli
from multiagent.brand_service import BrandService


@pytest.fixture
def temporary_directories(monkeypatch, tmp_path):
    original = cli.tempfile.TemporaryDirectory
    created = []

    @contextmanager
    def tracked(*args, **kwargs):
        with original(*args, dir=tmp_path, **kwargs) as folder:
            created.append(Path(folder))
            yield folder

    monkeypatch.setattr(cli.tempfile, "TemporaryDirectory", tracked)
    return created


def test_dry_run_removes_temporary_database(temporary_directories, capsys):
    args = cli.build_parser().parse_args(["dry-run", "--topic", "synthetic lighting"])
    args.func(args)

    assert '"id"' in capsys.readouterr().out
    assert len(temporary_directories) == 1
    assert not temporary_directories[0].exists()


def test_brand_generate_dry_run_removes_temporary_database(
    temporary_directories, capsys, monkeypatch, tmp_path,
):
    answers = tmp_path / "answers.txt"
    answers.write_text("Synthetic brand answers for a fictional lighting company. " * 2, encoding="utf-8")

    def fake_generate(self, answers, *, model_id):
        with self.store.db.connect() as conn:
            assert conn.execute("SELECT 1").fetchone()[0] == 1
        return {"profile": "synthetic"}

    monkeypatch.setattr(BrandService, "generate_from_answers", fake_generate)
    args = cli.build_parser().parse_args([
        "brand-generate", "--answers", str(answers), "--dry-run",
    ])
    args.func(args)

    assert '"synthetic"' in capsys.readouterr().out
    assert len(temporary_directories) == 1
    assert not temporary_directories[0].exists()

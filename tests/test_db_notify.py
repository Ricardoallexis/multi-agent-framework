# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Database notices describe committed changes without exposing their contents."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from multiagent.config import Settings
from multiagent.db.database import Database


@pytest.mark.parametrize("pooled", [True, False])
def test_only_committed_writes_notify_and_cancel_survives_close(tmp_path, pooled):
    db = Database(Settings(_env_file=None, local_dir=tmp_path, db_path=tmp_path / "test.db",
                           db_reuse_connections=pooled))
    assert not db._subscribers
    assert not db._pool.entries
    seen = []
    cancel = db.subscribe(lambda: seen.append("changed"))
    with db.tx() as conn:
        conn.execute("CREATE TABLE items (value INTEGER)")
    with db.tx() as conn:
        conn.execute("INSERT INTO items VALUES (1)")
    assert seen == ["changed"]
    with db.connect() as conn:
        conn.execute("SELECT * FROM items").fetchall()
        conn.commit()
    with db.tx() as conn:
        conn.execute("SELECT * FROM items").fetchall()
    with db.connect() as conn:
        conn.execute("INSERT INTO items VALUES (2)")
        conn.rollback()
        conn.commit()
    assert seen == ["changed"]
    with db.connect() as conn:
        conn.execute("INSERT INTO items VALUES (3)")
        conn.commit()
    assert seen == ["changed", "changed"]
    db.close()
    with db.tx() as conn:
        conn.execute("INSERT INTO items VALUES (4)")
    assert len(seen) == 3
    cancel()
    with db.tx() as conn:
        conn.execute("INSERT INTO items VALUES (5)")
    assert len(seen) == 3


def test_notification_is_after_commit_and_bad_callback_cannot_break_write(tmp_path):
    db = Database(Settings(_env_file=None, local_dir=tmp_path, db_path=tmp_path / "test.db"))
    with db.tx() as conn:
        conn.execute("CREATE TABLE items (value INTEGER)")
    seen = []
    def observe():
        with ThreadPoolExecutor(max_workers=1) as pool:
            seen.append(pool.submit(lambda: read_value(db)).result(timeout=2))
    db.subscribe(lambda: (_ for _ in ()).throw(RuntimeError("broken observer")))
    db.subscribe(observe)
    with db.tx() as conn:
        conn.execute("INSERT INTO items VALUES (7)")
    assert seen == [7]


def read_value(db):
    with db.connect() as conn:
        return conn.execute("SELECT value FROM items").fetchone()[0]

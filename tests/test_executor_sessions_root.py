# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""The session viewer permits the dedicated executor root and keeps other roots scoped."""
from __future__ import annotations

import pytest

from multiagent.services import ServiceError
from session_fixtures import CLAUDE_ID, CLAUDE_OTHER, make_services, make_stores, write_claude


def test_default_executor_root_is_readable_without_operator_roots(tmp_path):
    stores = make_stores(tmp_path)
    services = make_services(stores, gui_session_roots=[])
    run_dir = services.settings.executor_workdir_root / "task-T1"
    run_dir.mkdir(parents=True)
    write_claude(stores, CLAUDE_ID, cwd=run_dir)
    write_claude(stores, CLAUDE_OTHER, cwd=stores.other)
    try:
        assert services.sessions._roots() == (str(services.settings.executor_workdir_root),)
        assert [row["id"] for row in services.sessions.list_sessions(source="claude")["sessions"]] == [
            f"claude:{CLAUDE_ID}"]
        assert services.sessions.get_session(f"claude:{CLAUDE_ID}")["cwd"] == str(run_dir)
        with pytest.raises(ServiceError) as caught:
            services.sessions.get_session(f"claude:{CLAUDE_OTHER}")
        assert (caught.value.code, caught.value.status) == ("not_found", 404)
    finally:
        services.runs.close()


def test_operator_roots_are_preserved_without_admitting_siblings(tmp_path):
    stores = make_stores(tmp_path)
    services = make_services(stores)
    try:
        assert services.sessions._roots() == (str(stores.roots),)
        services.settings.executor_workdir_root.mkdir(parents=True)
        assert services.sessions._roots() == (str(stores.roots), str(services.settings.executor_workdir_root))
        write_claude(stores, CLAUDE_ID, cwd=stores.work)
        assert services.sessions.get_session(f"claude:{CLAUDE_ID}")["id"] == f"claude:{CLAUDE_ID}"
        sibling = services.settings.executor_workdir_root.with_name("executor_runs-other")
        sibling.mkdir(parents=True)
        write_claude(stores, CLAUDE_OTHER, cwd=sibling)
        with pytest.raises(ServiceError) as caught:
            services.sessions.get_session(f"claude:{CLAUDE_OTHER}")
        assert caught.value.code == "not_found"
    finally:
        services.runs.close()

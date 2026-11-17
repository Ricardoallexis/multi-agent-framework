"""Namespaced capabilities and configured tool selection (CAP-T10, S5.C1-C2). Synthetic data, no network."""
from __future__ import annotations

import logging
import random
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from multiagent.capabilities import (
    CapabilityIdError, invalid_capability_ids, is_valid_capability_id, normalize_capability_id,
    validate_capability_id, validate_capability_ids,
)
from multiagent.catalog import Catalog, CatalogError
from multiagent.config import Settings
from multiagent.routing.executor_capabilities import descriptor_from_declaration, executor_capability_warnings
from multiagent.tasks.envelopes import envelope_sha256
from multiagent.tools import InvocationKind, ImpactClass, SchemaRef, ToolRegistry, ToolSpec
from multiagent.tools.selection import SelectionError, parse_selection_config, select_tool

GOOD = ["file.read", "web.search", "doc.table_extract", "a.b.c.d", "x1.y_2"]
BAD = ["text_generation", "File.Read", "file..read", ".file.read", "file.read.", "file-read.x", "file.read ",
       "1file.read", "a.b.c.d.e", "", "file." + "x" * 41, None, 3]
SCHEMA = {"type": "object", "additionalProperties": False}


def spec(tool_id, version=1, capabilities=("file.read",)):
    ref = SchemaRef(schema_id="t.io/1", json_schema=SCHEMA, sha256=envelope_sha256(SCHEMA))
    return ToolSpec(tool_id=tool_id, version=version, title=tool_id, invocation=InvocationKind.LOCAL,
                    capabilities=frozenset(capabilities), impacts=frozenset({ImpactClass.READ}),
                    input_schema=ref, output_schema=ref, declared_by="builtin")


def build(entries, order=None):
    registry = ToolRegistry()
    items = list(entries)
    if order is not None:
        random.Random(order).shuffle(items)
    for item in items:
        registry.register(spec(*item), lambda arguments, context, *, should_cancel: {})
    return registry


ENTRIES = [("fs.zeta", 1), ("fs.alpha", 1), ("fs.alpha", 3), ("fs.alpha", 2), ("fs.other", 1, ("web.search",))]


# --- pattern ---------------------------------------------------------------------------------

@pytest.mark.parametrize("value", GOOD)
def test_valid_ids(value):
    assert is_valid_capability_id(value)
    assert validate_capability_id(value) == value


@pytest.mark.parametrize("value", BAD)
def test_invalid_ids(value):
    assert not is_valid_capability_id(value)
    with pytest.raises(CapabilityIdError) as info:
        validate_capability_id(value)
    assert info.value.code == "invalid_capability_id"


def test_normalize_and_sets():
    assert normalize_capability_id("  File.Read ") == "file.read"
    with pytest.raises(CapabilityIdError):
        normalize_capability_id("file read")
    assert validate_capability_ids(["file.read", "web.search"]) == {"file.read", "web.search"}
    with pytest.raises(CapabilityIdError):
        validate_capability_ids("file.read")
    assert invalid_capability_ids(["file.read", "text", "Bad.x"]) == ["Bad.x", "text"]


# --- tools, agents, executors ----------------------------------------------------------------

def test_tool_rejects_invalid_capability():
    with pytest.raises(ValidationError):
        spec("fs.read", capabilities=("text_generation",))
    with pytest.raises(ValidationError):
        spec("fs.read", capabilities=("File.Read",))
    assert spec("fs.read", capabilities=("file.read",)).capabilities == {"file.read"}


def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, local_dir=tmp_path / "local")


def load_agents(tmp_path, agents):
    folder = tmp_path / "catalog"
    folder.mkdir(exist_ok=True)
    (folder / "agents.yaml").write_text(yaml.safe_dump({"agents": agents}), encoding="utf-8")
    (folder / "models.yaml").write_text("models: {}\n", encoding="utf-8")
    return Catalog(settings(tmp_path), catalog_dir=folder)


def test_agent_capability_list_valid_and_invalid(tmp_path):
    catalog = load_agents(tmp_path, {"a": {"role": "r", "capabilities": ["file.read", "web.search"]}})
    assert catalog.agents["a"]["capabilities"] == ["file.read", "web.search"]
    with pytest.raises(CatalogError, match="invalid capability id"):
        load_agents(tmp_path, {"a": {"role": "r", "capabilities": ["file.read", "Not Valid"]}})
    with pytest.raises(CatalogError):
        load_agents(tmp_path, {"a": {"role": "r", "capabilities": "file.read"}})


def test_agent_legacy_mapping_and_missing_are_left_alone(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="multiagent.catalog"):
        catalog = load_agents(tmp_path, {"a": {"role": "r", "capabilities": {"research": "PRIMARY"}}, "b": {"role": "r"}})
    assert set(catalog.agents) == {"a", "b"}
    assert caplog.text == ""


def test_bundled_catalog_still_loads():
    root = Path(__file__).resolve().parents[1]
    Catalog(Settings(_env_file=None, local_dir=root / ".nope"), catalog_dir=root / "catalog")


def test_executor_invalid_capability_only_warns(caplog):
    declaration = {"surface": "app", "capabilities_available": ["text", "file.read"]}
    with caplog.at_level(logging.WARNING, logger="multiagent.routing.executor_capabilities"):
        descriptor = descriptor_from_declaration("ex1", declaration)
    assert descriptor.capabilities_available == {"text", "file.read"}
    assert executor_capability_warnings(descriptor) == ["text"]
    assert "outside the namespaced pattern" in caplog.text
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="multiagent.routing.executor_capabilities"):
        descriptor_from_declaration("ex2", {"surface": "app", "capabilities_available": ["file.read"]})
    assert caplog.text == ""


# --- selection -------------------------------------------------------------------------------

def test_default_tie_break_is_tool_id_then_highest_version():
    chosen = select_tool(build(ENTRIES), "file.read")
    assert (chosen.spec.tool_id, chosen.spec.version) == ("fs.alpha", 3)


def test_config_changes_choice_without_code_change():
    registry = build(ENTRIES)
    assert select_tool(registry, "file.read", {"version": 1, "tools": {"file.read": "fs.zeta"}}).spec.tool_id == "fs.zeta"
    pinned = select_tool(registry, "file.read", {"tools": {"file.read": {"tool_id": "fs.alpha", "version": 2}}})
    assert (pinned.spec.tool_id, pinned.spec.version) == ("fs.alpha", 2)
    assert select_tool(registry, "file.read", {"tools": {"file.read": "fs.alpha@1"}}).spec.version == 1
    # configuration for another capability does not affect this one
    other = {"tools": {"web.search": "fs.other"}}
    assert select_tool(registry, "file.read", other).spec.tool_id == "fs.alpha"
    # latest version when unpinned
    assert select_tool(registry, "file.read", {"tools": {"file.read": "fs.alpha"}}).spec.version == 3


def test_choice_is_stable_across_runs_and_registration_order():
    config = {"tools": {"web.search": "fs.other"}}
    results = set()
    for seed in range(12):
        registry = build(ENTRIES, order=seed)
        for _ in range(3):
            tool = select_tool(registry, "file.read", config)
            results.add((tool.spec.tool_id, tool.spec.version))
    assert results == {("fs.alpha", 3)}


@pytest.mark.parametrize("capability, config, code", [
    ("file.write", None, "no_tool_for_capability"),
    ("text_generation", None, "invalid_capability"),
    ("file.read", {"tools": {"file.read": "fs.missing"}}, "config_unknown_tool"),
    ("file.read", {"tools": {"file.read": "fs.alpha@9"}}, "config_version_not_found"),
    ("file.read", {"tools": {"file.read": "fs.other"}}, "config_wrong_capability"),
    ("file.read", {"tools": {"Bad": "fs.alpha"}}, "invalid_config"),
    ("file.read", {"tools": {"file.read": 5}}, "invalid_config"),
    ("file.read", {"tools": {"file.read": {"tool_id": "fs.alpha", "version": 0}}}, "invalid_config"),
    ("file.read", {"version": 2}, "invalid_config"),
    ("file.read", {"tools": {}, "extra": 1}, "invalid_config"),
])
def test_selection_fails_closed(capability, config, code):
    with pytest.raises(SelectionError) as info:
        select_tool(build(ENTRIES), capability, config)
    assert info.value.code == code


def test_empty_config_means_default():
    assert parse_selection_config(None).choices == {}
    assert parse_selection_config({}).choices == {}

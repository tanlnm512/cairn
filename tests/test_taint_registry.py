"""Taint registry contract: default source/sink tables and config overrides."""

from __future__ import annotations

import cairn.graph.taint as taint


EXPECTED_SOURCE_CATEGORIES = {"http", "cli", "env", "file-read", "api-response"}
EXPECTED_SINK_CATEGORIES = {
    "sql",
    "shell",
    "file-write",
    "network-send",
    "eval-deserialize",
}


def test_default_tables_name_every_category():
    assert set(taint.DEFAULT_SOURCES) == EXPECTED_SOURCE_CATEGORIES
    assert set(taint.DEFAULT_SINKS) == EXPECTED_SINK_CATEGORIES


def test_default_categories_hold_non_empty_string_name_sets():
    for table in (taint.DEFAULT_SOURCES, taint.DEFAULT_SINKS):
        for names in table.values():
            assert isinstance(names, set)
            assert names
            assert all(isinstance(name, str) and name for name in names)


def test_build_registry_without_overrides_yields_defaults():
    registry = taint.build_registry()
    assert registry.sources == taint.DEFAULT_SOURCES
    assert registry.sinks == taint.DEFAULT_SINKS


def test_override_replaces_same_named_default_category():
    registry = taint.build_registry(
        source_overrides={"http": ["custom_fetch"]},
        sink_overrides={"sql": ["run_statement"]},
    )
    assert registry.sources["http"] == {"custom_fetch"}
    assert registry.sinks["sql"] == {"run_statement"}
    # Sibling default categories survive an unrelated override.
    assert registry.sources["cli"] == taint.DEFAULT_SOURCES["cli"]
    assert registry.sinks["shell"] == taint.DEFAULT_SINKS["shell"]


def test_new_override_category_extends_the_table():
    registry = taint.build_registry(
        source_overrides={"queue-msg": ["consume"]},
        sink_overrides={"render": ["render_template"]},
    )
    assert registry.sources["queue-msg"] == {"consume"}
    assert registry.sinks["render"] == {"render_template"}
    assert set(registry.sources) == EXPECTED_SOURCE_CATEGORIES | {"queue-msg"}
    assert set(registry.sinks) == EXPECTED_SINK_CATEGORIES | {"render"}


def test_override_name_lists_are_normalized_to_sets():
    registry = taint.build_registry(
        source_overrides={"http": ["fetch_a", "fetch_a", "fetch_b"]},
    )
    assert registry.sources["http"] == {"fetch_a", "fetch_b"}


def test_empty_override_category_replaces_the_default():
    registry = taint.build_registry(source_overrides={"env": []})
    assert registry.sources["env"] == set()
    assert registry.source_names().isdisjoint(taint.DEFAULT_SOURCES["env"])


def test_module_defaults_are_never_mutated():
    taint.build_registry(
        source_overrides={"http": ["mutant"], "brand-new": ["x"]},
        sink_overrides={"sql": ["mutant"]},
    )
    assert "mutant" not in taint.DEFAULT_SOURCES["http"]
    assert "brand-new" not in taint.DEFAULT_SOURCES
    assert taint.DEFAULT_SINKS["sql"] == {"execute", "executemany", "executescript"}
    registry = taint.build_registry()
    registry.sources["http"].add("mutant")
    assert "mutant" not in taint.DEFAULT_SOURCES["http"]


def test_flattened_name_views_reflect_overrides():
    registry = taint.build_registry(
        source_overrides={"queue-msg": ["consume"]},
        sink_overrides={"render": ["render_template"]},
    )
    names = registry.source_names()
    assert isinstance(names, set)
    assert {"urlopen", "input", "getenv", "consume"} <= names
    assert {"execute", "system", "write_text", "sendall", "eval", "render_template"} <= (
        registry.sink_names()
    )

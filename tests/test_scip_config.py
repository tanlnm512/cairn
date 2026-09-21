"""The ``scip`` key in cairn.json."""
from __future__ import annotations

import json

from cairn.graph.config import CairnConfig, load_config


def _write_config(tmp_path, payload):
    (tmp_path / "cairn.json").write_text(json.dumps(payload), encoding="utf-8")


def test_scip_section_parses_indexes(tmp_path):
    _write_config(tmp_path, {"scip": {"indexes": {"python": "index.scip"}}})

    cfg = load_config(tmp_path)
    assert cfg.scip == {"indexes": {"python": "index.scip"}}
    assert not cfg.is_default  # configured indexes => not default


def test_missing_file_and_missing_key_default_to_empty(tmp_path):
    assert load_config(tmp_path).scip == {}
    _write_config(tmp_path, {"exclude": ["build/"]})
    assert load_config(tmp_path).scip == {}


def test_malformed_scip_section_is_ignored(tmp_path, capsys):
    for bad in (["not", "a", "dict"], "nope", 42, None):
        _write_config(tmp_path, {"scip": bad})
        assert load_config(tmp_path).scip == {}
    assert "must be a JSON object" in capsys.readouterr().err


def test_malformed_indexes_is_ignored(tmp_path, capsys):
    _write_config(tmp_path, {"scip": {"indexes": ["not", "a", "dict"]}})
    assert load_config(tmp_path).scip == {}
    assert "scip.indexes" in capsys.readouterr().err


def test_indexes_drops_non_string_entries(tmp_path):
    _write_config(
        tmp_path, {"scip": {"indexes": {"python": "index.scip", "go": 7, "": "x"}}}
    )

    assert load_config(tmp_path).scip == {"indexes": {"python": "index.scip"}}


def test_empty_scip_section_stays_default(tmp_path):
    _write_config(tmp_path, {"scip": {}})

    cfg = load_config(tmp_path)
    assert cfg.scip == {}
    assert cfg.is_default


def test_scip_coexists_with_existing_keys(tmp_path):
    _write_config(
        tmp_path,
        {
            "exclude": ["build/"],
            "repo_namespaces": {"com.example.sdk": "sdk"},
            "scip": {"indexes": {"python": "index.scip"}},
        },
    )

    cfg = load_config(tmp_path)
    assert cfg.exclude == ["build/"]
    assert cfg.repo_namespaces == {"com.example.sdk": "sdk"}
    assert cfg.scip == {"indexes": {"python": "index.scip"}}


def test_default_config_has_empty_scip():
    cfg = CairnConfig()
    assert cfg.scip == {}
    assert cfg.is_default

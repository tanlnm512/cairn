"""Tests for configurable cross-repo namespace map (Step 1).

Verifies the resolution priority:
  CAIRN_REPO_NAMESPACES env > cairn.json repo_namespaces > built-in default.

Also covers config.py's ``repo_namespaces`` parsing (shape, malformed-input
resilience — a bad config never crashes).
"""
from __future__ import annotations

import json
import sqlite3
import textwrap

import pytest

from cairn.graph import cross_repo
from cairn.graph.config import CairnConfig, _as_string_dict, load_config


# --------------------------------------------------------------------------
# Config parsing
# --------------------------------------------------------------------------

def test_config_parses_repo_namespaces(tmp_path):
    cfg_file = tmp_path / "cairn.json"
    cfg_file.write_text(json.dumps({
        "exclude": ["build/"],
        "repo_namespaces": {
            "com.example.sdk": "sdk",
            "com.example.core": "core",
        },
    }))
    cfg = load_config(tmp_path)
    assert cfg.repo_namespaces == {"com.example.sdk": "sdk", "com.example.core": "core"}
    assert not cfg.is_default  # populated namespaces => not default


def test_config_repo_namespaces_malformed_is_ignored(tmp_path, capsys):
    cfg_file = tmp_path / "cairn.json"
    cfg_file.write_text(json.dumps({"repo_namespaces": "not-an-object"}))
    cfg = load_config(tmp_path)
    assert cfg.repo_namespaces == {}  # bad value -> empty, no crash
    captured = capsys.readouterr()
    assert "repo_namespaces" in captured.err  # warned


def test_as_string_dict_drops_non_string_entries():
    out = _as_string_dict({"a": "b", 1: "x", "c": 2, "": "y", "d": ""}, __import__("pathlib").Path("x"), "k")
    assert out == {"a": "b"}  # only the all-string, non-empty pair survives


def test_default_config_has_empty_namespaces():
    cfg = CairnConfig()
    assert cfg.repo_namespaces == {}
    assert cfg.is_default


# --------------------------------------------------------------------------
# Namespace resolution priority
# --------------------------------------------------------------------------

@pytest.fixture
def reset_cache():
    """Reset the process-level namespace cache before and after each test."""
    cross_repo._reset_namespaces_cache()
    yield
    cross_repo._reset_namespaces_cache()


@pytest.fixture(autouse=True)
def clear_env(monkeypatch):
    monkeypatch.delenv("CAIRN_REPO_NAMESPACES", raising=False)


def test_env_override_wins_over_default(reset_cache, monkeypatch):
    monkeypatch.setenv("CAIRN_REPO_NAMESPACES", json.dumps({"io.app.api": "api"}))
    ns = cross_repo._load_namespaces()
    assert ns == {"io.app.api": "api"}
    assert ns != cross_repo._DEFAULT_NAMESPACES


def test_env_malformed_json_falls_back(reset_cache, monkeypatch, capsys):
    monkeypatch.setenv("CAIRN_REPO_NAMESPACES", "{not json")
    ns = cross_repo._load_namespaces()
    assert ns == cross_repo._DEFAULT_NAMESPACES  # fell through to default
    assert "invalid JSON" in capsys.readouterr().err


def test_env_non_object_falls_back(reset_cache, monkeypatch, capsys):
    monkeypatch.setenv("CAIRN_REPO_NAMESPACES", "[1,2,3]")
    ns = cross_repo._load_namespaces()
    assert ns == cross_repo._DEFAULT_NAMESPACES
    assert "JSON object" in capsys.readouterr().err


def test_config_file_used_when_no_env(reset_cache, monkeypatch, tmp_path):
    # A cairn.json with repo_namespaces at the workspace root.
    (tmp_path / "cairn.json").write_text(json.dumps({
        "repo_namespaces": {"org.platform.billing": "billing-svc"},
    }))
    monkeypatch.setenv("CAIRN_WORKSPACE", str(tmp_path))

    ns = cross_repo._load_namespaces()
    assert ns == {"org.platform.billing": "billing-svc"}


def test_default_fallback(reset_cache, monkeypatch, tmp_path):
    # No env, no cairn.json -> built-in default.
    monkeypatch.setenv("CAIRN_WORKSPACE", str(tmp_path))
    ns = cross_repo._load_namespaces()
    assert ns == cross_repo._DEFAULT_NAMESPACES


def test_result_is_cached(reset_cache, monkeypatch):
    monkeypatch.setenv("CAIRN_REPO_NAMESPACES", json.dumps({"a.b": "x"}))
    first = cross_repo._load_namespaces()
    # Change env after first load — cache should ignore it.
    monkeypatch.setenv("CAIRN_REPO_NAMESPACES", json.dumps({"c.d": "y"}))
    second = cross_repo._load_namespaces()
    assert first is second  # same object, cached


# --------------------------------------------------------------------------
# cross_repo_deps honors the resolved map
# --------------------------------------------------------------------------

def _seed_two_repos(conn: sqlite3.Connection) -> None:
    """Seed two repos where repo-a imports a namespace owned by repo-b."""
    cur = conn.cursor()
    cur.execute("INSERT INTO repos(id,name,path,language) VALUES(?,?,?,?)",
                ("repo-a", "repo-a", "/a", "kotlin"))
    cur.execute("INSERT INTO repos(id,name,path,language) VALUES(?,?,?,?)",
                ("repo-b", "repo-b", "/b", "kotlin"))
    # A file in repo-a imports repo-b's namespace.
    cur.execute("INSERT INTO files(id,repo_id,path,language) VALUES(?,?,?,?)",
                ("f-a", "repo-a", "/a/A.kt", "kotlin"))
    cur.execute("INSERT INTO symbols(id,file_id,name,kind,line_start) VALUES(?,?,?,?,?)",
                ("s-a", "f-a", "A", "class", 1))
    cur.execute("INSERT INTO imports(id,file_id,imported_path,line) VALUES(?,?,?,?)",
                ("i-a", "f-a", "com.custom.sdk.useful", 1))
    conn.commit()


def test_cross_repo_deps_uses_env_namespaces(reset_cache, fresh_db, monkeypatch):
    _seed_two_repos(fresh_db)
    monkeypatch.setenv("CAIRN_REPO_NAMESPACES",
                       json.dumps({"com.custom.sdk": "repo-b"}))

    result = cross_repo.cross_repo_deps(fresh_db, "repo-a")
    assert [d["repo"] for d in result["dependencies"]] == ["repo-b"]
    assert [d["evidence"] for d in result["dependencies"]] == ["com.custom.sdk"]


def test_cross_repo_deps_default_map_finds_nothing(reset_cache, fresh_db, monkeypatch):
    """With the built-in default (be-workspace) map, com.custom.sdk maps nowhere."""
    _seed_two_repos(fresh_db)
    monkeypatch.setenv("CAIRN_WORKSPACE", "/nonexistent-for-test")  # no config

    result = cross_repo.cross_repo_deps(fresh_db, "repo-a")
    assert result["dependencies"] == []  # default map doesn't know com.custom.sdk

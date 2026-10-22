"""Contracts of the rust-migration golden harnesses."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts" / "rust_migration"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import flow_golden  # noqa: E402
import parity  # noqa: E402


def test_flow_golden_writes_no_parity_fixture():
    """flow_golden fixture paths never live under parity's mcp tree.

    parity --record rewrites every json under its out-dir, so a flow fixture
    placed there drifts on the next parity record and --check can never pass.
    """
    for path in flow_golden._fixture_paths().values():
        assert parity.DEFAULT_OUT_DIR not in path.parents, str(path)


def _patch_record_machinery(monkeypatch, tmp_path):
    """Freeze the record pipeline after corpus staging; capture the build input."""
    seen = {}

    def fake_run_build(workspace, db):
        seen["workspace"] = Path(workspace)
        return {"seconds": 0.0}

    monkeypatch.setattr(parity.baseline, "run_build", fake_run_build)
    monkeypatch.setattr(parity.baseline, "run_embed", lambda db: {})
    monkeypatch.setattr(parity.gr, "seed_bundle", lambda src, dst: 0)
    monkeypatch.setattr(parity.gr, "seed_concepts", lambda knowledge: None)
    monkeypatch.setattr(parity, "pin_db_bytes", lambda db: None)
    monkeypatch.setattr(parity, "_write_pinned_dir",
                        lambda store, scratch, workspace, seeded: None)
    monkeypatch.setattr(parity, "PINNED_STORES", tmp_path / "stores")
    cfg = {"workspace": str(tmp_path / "source"), "knowledge": str(tmp_path)}
    return seen, cfg


def test_record_reproduces_pinned_corpus(tmp_path, monkeypatch):
    """--record builds over the checked-in pinned corpus bytes, never over the
    (possibly drifted) live source corpus."""
    source = tmp_path / "source"
    (source / "a.py").parent.mkdir(parents=True)
    (source / "a.py").write_text("drifted bytes\n")
    (source / "b.py").write_text("new file\n")
    pinned_corpus = tmp_path / "stores" / "yarl" / "corpus"
    pinned_corpus.mkdir(parents=True)
    (pinned_corpus / "a.py").write_text("pinned bytes\n")

    seen, cfg = _patch_record_machinery(monkeypatch, tmp_path)
    monkeypatch.setattr(parity, "CORPORA", {"yarl": source})

    parity.build_pinned_store("yarl", tmp_path / "scratch", cfg)

    workspace = seen["workspace"]
    assert (workspace / "a.py").read_text() == "pinned bytes\n"
    assert not (workspace / "b.py").exists()


def test_record_refuses_unreproducible_pinned_store(tmp_path, monkeypatch):
    """A pinned store without its corpus cannot be reproduced; record refuses
    instead of silently re-pinning from the live source."""
    (tmp_path / "stores" / "yarl").mkdir(parents=True)
    (tmp_path / "source").mkdir()
    seen, cfg = _patch_record_machinery(monkeypatch, tmp_path)
    monkeypatch.setattr(parity, "CORPORA", {"yarl": tmp_path / "source"})

    with pytest.raises(SystemExit, match="REFUSING record"):
        parity.build_pinned_store("yarl", tmp_path / "scratch", cfg)
    assert "workspace" not in seen

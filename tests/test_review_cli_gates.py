"""`cairn review` CLI boundary contracts: git/refresh failures map to clean
errors, --gate fails closed on an unreadable memory store, and a captured
event survives an embed-backend outage without disk/DB divergence."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

from cairn.graph.schema import _apply_schema


def _make_db(tmp_path: Path) -> str:
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    _apply_schema(conn)
    conn.commit()
    conn.close()
    return str(db)


def test_base_mode_maps_runtime_failures_to_clean_errors(
    tmp_path, monkeypatch
):
    """A git or strict-refresh RuntimeError behind --base renders as click's
    single-line error, never a raw traceback (BlastBaseError rides the same
    handler)."""
    import cairn.cli.review as review_cli

    def failing_build_pack(conn, workspace, **kwargs):
        raise RuntimeError("graph refresh failed: parse exploded")

    monkeypatch.setattr(review_cli, "build_pack", failing_build_pack)
    db = _make_db(tmp_path)

    result = CliRunner().invoke(
        review_cli.review, ["--base", "origin/main", "--db", str(db)]
    )

    assert result.exit_code == 1
    assert not isinstance(result.exception, RuntimeError), result.exception
    assert "graph refresh failed" in result.stderr
    assert "parse exploded" in result.stderr


def test_gate_fails_closed_when_memory_reader_errors(tmp_path, monkeypatch):
    """--gate with an unreadable memory store must not pass silently."""
    import cairn.cli.review as review_cli

    def unreadable(conn, workspace, **kwargs):
        return {"memories": {"entries": [], "error": "bundle unreadable"}}

    monkeypatch.setattr(review_cli, "build_pre_submit", unreadable)
    db = _make_db(tmp_path)

    gated = CliRunner().invoke(
        review_cli.review, ["--pre-submit", "--gate", "--db", str(db)]
    )

    assert gated.exit_code == 1
    assert not isinstance(gated.exception, RuntimeError), gated.exception
    assert "could not read memories" in gated.stderr
    assert "bundle unreadable" in gated.stderr

    ungated = CliRunner().invoke(
        review_cli.review, ["--pre-submit", "--db", str(db)],
        catch_exceptions=False,
    )
    assert ungated.exit_code == 0
    assert "memory enrichment unavailable" in ungated.output


def test_capture_event_survives_embed_backend_failure(tmp_path, monkeypatch):
    """An embed failure must not roll back the capture: the memory stays
    recorded (embeddings are rebuildable via `cairn embed`)."""
    import cairn.cli.review as review_cli

    event_file = tmp_path / "event.json"
    event_file.write_text(
        json.dumps(
            {
                "resolved": True,
                "file": "a.py",
                "line": 1,
                "body": "b",
                "thread_url": "",
            }
        ),
        encoding="utf-8",
    )
    captured: dict = {}

    def fake_capture(conn, bundle, payload):
        captured["payload"] = payload
        return {
            "recorded": True,
            "type": "mistake",
            "title": "t",
            "path": "memory/x",
            "tier": "drafts",
        }

    def dead_backend(conn, bundle, paths):
        raise RuntimeError("embed backend down")

    monkeypatch.setattr(
        "cairn.review.capture.capture_review_event", fake_capture
    )
    monkeypatch.setattr(
        "cairn.graph.embeddings.embed_memory_concepts", dead_backend
    )
    monkeypatch.setattr(
        "cairn.paths.resolve_store",
        lambda *a, **k: SimpleNamespace(knowledge=str(tmp_path / "kb")),
    )
    db = _make_db(tmp_path)

    result = CliRunner().invoke(
        review_cli.review, ["--capture-event", str(event_file), "--db", str(db)]
    )

    assert result.exit_code == 0, result.output
    assert "Recorded mistake 't'" in result.output
    assert "Embedding failed; memories saved without vectors" in result.stderr
    assert captured["payload"]["resolved"] is True

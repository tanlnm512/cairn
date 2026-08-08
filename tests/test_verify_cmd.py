"""Tests for `cairn verify <doc-path>` — the single-concept critic verdict (Phase 2.2.1).

`cairn verify` is the user-facing front to the critic gate that promise #2 of
the verification contract rests on. It loads any one compass/wiki/memory
concept, runs the deterministic critic, and prints passed / errors / warnings /
quality. This test exercises the three cases that matter: a clean concept
passes, a concept citing a file not in the graph fails (blocking), and the
exit code reflects the verdict for CI/scripts.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from click.testing import CliRunner

from cairn.cli import main
from cairn.graph.schema import _apply_schema


def _db_with_one_symbol(db_path: Path) -> None:
    """A file DB with the schema + one repo/file/symbol so a real ref verifies."""
    conn = sqlite3.connect(str(db_path))
    _apply_schema(conn)
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('r1', 'r1', '.')")
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES (1, 'r1', 'src/auth.py', 'python')"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
        "VALUES (1, 1, 'login', 'function', 'auth.login', 1, 10)"
    )
    conn.commit()
    conn.close()


def _write_concept(knowledge_dir: Path, rel: str, body: str) -> Path:
    """Write a minimal OKF concept markdown file under the knowledge bundle."""
    p = knowledge_dir / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    # Minimal OKF markdown: front matter (type + title) + body.
    p.write_text(
        "---\n"
        "type: Compass\n"
        "title: test\n"
        "---\n"
        f"{body}\n"
    )
    return p


def test_verify_passes_for_real_refs(tmp_path):
    """A concept citing a file + symbol that exist in the graph → exit 0, OK."""
    db = tmp_path / "test.kg"
    _db_with_one_symbol(db)
    knowledge = tmp_path / ".knowledge"
    _write_concept(
        knowledge,
        "compass/auth.md",
        (
            "# What Does This Module Do?\nCalls `src/auth.py`'s `login()`.\n"
            "# Common Modification Patterns\n...\n"
            "# Build-Failure Patterns\n...\n"
            "# Cross-Module Dependencies\n...\n"
            "# Tribal Knowledge\n...\n"
        ),
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["verify", "compass/auth", "--db", str(db), "--knowledge", str(knowledge)],
    )
    assert result.exit_code == 0, result.output
    assert "[OK]" in result.output
    assert "compass/auth" in result.output


def test_verify_fails_for_unknown_file_ref(tmp_path):
    """A concept citing a file NOT in the graph → exit 1, FAIL, error listed."""
    db = tmp_path / "test.kg"
    _db_with_one_symbol(db)
    knowledge = tmp_path / ".knowledge"
    _write_concept(
        knowledge,
        "compass/bad.md",
        "See `does/not/exist.py` for the entry point.\n",
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["verify", "compass/bad", "--db", str(db), "--knowledge", str(knowledge)],
    )
    assert result.exit_code == 1, result.output
    assert "[FAIL]" in result.output
    assert "does/not/exist.py" in result.output  # the offending ref is named


def test_verify_missing_concept_exits_nonzero(tmp_path):
    """A doc-path that doesn't resolve → exit 2, clear error."""
    db = tmp_path / "test.kg"
    _db_with_one_symbol(db)
    knowledge = tmp_path / ".knowledge"
    knowledge.mkdir()
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["verify", "nope/missing", "--db", str(db), "--knowledge", str(knowledge)],
    )
    assert result.exit_code == 2, result.output
    assert "nope/missing" in result.output


def test_critic_verdict_block_is_machine_readable():
    """The structured verdict block (2.2.2) is valid JSON an agent can parse,
    carrying passed / quality_score / errors / warnings. Additive: it does not
    alter the human-readable prose that precedes it.
    """
    import json
    from types import SimpleNamespace
    from cairn.mcp_server.tools_compass import _critic_verdict_block

    result = SimpleNamespace(
        passed=True,
        quality_score=0.8,
        errors=[],
        warnings=["Unknown symbol: Foo"],
    )
    block = _critic_verdict_block(result)
    assert block.startswith("```cairn-critic\n")
    assert block.endswith("\n```")
    payload = json.loads(block.removeprefix("```cairn-critic\n").removesuffix("\n```"))
    assert payload["passed"] is True
    assert payload["quality_score"] == 0.8
    assert payload["errors"] == []
    assert payload["warnings"] == ["Unknown symbol: Foo"]

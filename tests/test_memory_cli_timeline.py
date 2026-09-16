"""CLI ``cairn memory timeline``: temporal history of memories citing a symbol.

Seeds a store with validity intervals and successor links and asserts the
timeline lists them ordered by ``valid_from``; an unknown symbol exits 0.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from cairn.graph.schema import get_db
from cairn.memory.promotion import capture_memory
from cairn.memory.store import write_validity
from cairn.okf.bundle import OKFBundle


def _cli():
    from cairn.cli.main import main

    return main


@pytest.fixture(autouse=True)
def _hash_backend(hash_backend):
    """The CLI record path embeds; keep it on the dep-free backend."""


@pytest.fixture
def runner():
    return CliRunner()


def _paths(tmp_path):
    return str(tmp_path / "g.db"), str(tmp_path / "k")


def _timeline(runner, db_path, knowledge, symbol):
    return runner.invoke(
        _cli(),
        ["memory", "timeline", symbol,
         "--db", db_path, "--knowledge", knowledge],
    )


class TestMemoryTimeline:
    def test_intervals_and_successor_links_ordered_by_valid_from(
        self, runner, tmp_path
    ):
        db_path, knowledge = _paths(tmp_path)
        conn = get_db(db_path)
        bundle = OKFBundle(knowledge)
        try:
            invalidated = capture_memory(
                conn, bundle, "decision", "RetryProbe v1 policy",
                "Use `RetryProbe()` with backoff; replaced by `NewProbe`.",
            )["concept"]
            write_validity(
                invalidated,
                valid_from="2026-01-01T00:00:00Z",
                valid_until="2026-06-01T00:00:00Z",
                successor_symbol="NewProbe",
                bundle=bundle,
                conn=conn,
            )
            capture_memory(
                conn, bundle, "decision", "RetryProbe v2 note",
                "`RetryProbe` is the current probe.",
            )
            capture_memory(
                conn, bundle, "decision", "UnrelatedWidget note",
                "`UnrelatedWidget` does not cite the probe.",
            )
        finally:
            conn.close()

        result = _timeline(runner, db_path, knowledge, "RetryProbe")
        assert result.exit_code == 0, result.output
        # Oldest valid_from first; the unrelated memory stays out.
        assert result.output.index("RetryProbe v1 policy") < result.output.index(
            "RetryProbe v2 note"
        )
        assert "UnrelatedWidget" not in result.output
        # Closed window, open window, successor link, tier/score.
        assert "2026-01-01" in result.output
        assert "2026-06-01" in result.output
        assert "-> present" in result.output
        assert "successor: NewProbe" in result.output
        assert re.search(r"\[[a-z]+ [0-9.]+\]", result.output)

    def test_backtick_cite_matches_leaf_of_dotted_ref(self, runner, tmp_path):
        db_path, knowledge = _paths(tmp_path)
        conn = get_db(db_path)
        bundle = OKFBundle(knowledge)
        try:
            capture_memory(
                conn, bundle, "decision", "ProbeModule probe note",
                "`ProbeModule.RetryProbe` is the qualified home.",
            )
        finally:
            conn.close()

        result = _timeline(runner, db_path, knowledge, "RetryProbe")
        assert result.exit_code == 0, result.output
        assert "ProbeModule probe note" in result.output

    def test_plain_text_title_mention_surfaces(self, runner, tmp_path):
        """A memory mentioning the symbol without backticks still lists."""
        db_path, knowledge = _paths(tmp_path)
        rec = runner.invoke(
            _cli(),
            ["memory", "record", "decision", "RetryProbe policy notes",
             "--body", "qa fixture for the timeline view",
             "--db", db_path, "--knowledge", knowledge],
        )
        assert rec.exit_code == 0, rec.output

        result = _timeline(runner, db_path, knowledge, "RetryProbe")
        assert result.exit_code == 0, result.output
        assert "RetryProbe policy notes" in result.output
        assert re.search(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", result.output)

    def test_unknown_symbol_exits_zero(self, runner, tmp_path):
        db_path, knowledge = _paths(tmp_path)
        Path(knowledge).mkdir(parents=True)
        result = _timeline(runner, db_path, knowledge, "NoSuchSymbolQA")
        assert result.exit_code == 0, result.output

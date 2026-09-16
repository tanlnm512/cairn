"""CLI ``--as-of``: ``cairn memory search`` filters by validity instant.

Records through the CLI and searches through the CLI so the option's
forwarding contract is exercised end to end: the default (now) shows only
currently-valid memories; a past ``--as-of`` hides memories created after
it; a future ``--as-of`` shows them.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from click.testing import CliRunner

from cairn.graph.schema import get_db


@pytest.fixture(autouse=True)
def _hash_backend(hash_backend):
    """The CLI record/search paths embed; keep them on the dep-free backend."""


@pytest.fixture
def runner():
    return CliRunner()


def _store(tmp_path):
    db_path = str(tmp_path / "g.db")
    knowledge = str(tmp_path / "k")
    conn = get_db(db_path)
    conn.close()
    return db_path, knowledge


def _record(runner, db_path, knowledge, title):
    from cairn.cli import main

    result = runner.invoke(
        main,
        ["memory", "record", "decision", title, "--body", "qa fixture memory",
         "--db", db_path, "--knowledge", knowledge],
    )
    assert result.exit_code == 0, result.output


def _search(runner, db_path, knowledge, query, *extra):
    from cairn.cli import main

    return runner.invoke(
        main,
        ["memory", "search", query, "--db", db_path, "--knowledge", knowledge,
         *extra],
    )


def _result_text(output):
    """Result rows only — a bare ``No memories matching '<query>'.`` echo
    quotes the query text and must not count as a hit."""
    return "".join(l for l in output.splitlines() if l.startswith("  ["))


class TestMemorySearchAsOf:
    def test_future_as_of_shows_past_as_of_hides(self, runner, tmp_path):
        db_path, knowledge = _store(tmp_path)
        _record(runner, db_path, knowledge, "CliAsOf probe one")

        future = _search(runner, db_path, knowledge,
                         "CliAsOf probe one", "--as-of", "2099-12-31")
        assert future.exit_code == 0, future.output
        assert "CliAsOf probe one" in _result_text(future.output)

        past = _search(runner, db_path, knowledge,
                       "CliAsOf probe one", "--as-of", "2000-01-01")
        assert past.exit_code == 0, past.output
        assert "CliAsOf probe one" not in _result_text(past.output)

    def test_default_matches_today_as_of(self, runner, tmp_path):
        db_path, knowledge = _store(tmp_path)
        _record(runner, db_path, knowledge, "CliDefault probe two")

        default = _search(runner, db_path, knowledge, "CliDefault probe two")
        assert default.exit_code == 0, default.output
        assert "CliDefault probe two" in _result_text(default.output)

        today = datetime.now(timezone.utc).date()
        # A date-only --as-of is midnight UTC, so a memory created later
        # today is not yet valid at "today" — use tomorrow for the visible leg.
        tomorrow = (today + timedelta(days=1)).isoformat()
        as_of_tomorrow = _search(runner, db_path, knowledge,
                                 "CliDefault probe two", "--as-of", tomorrow)
        assert as_of_tomorrow.exit_code == 0, as_of_tomorrow.output
        assert "CliDefault probe two" in _result_text(as_of_tomorrow.output)

    def test_empty_store_as_of_exits_zero(self, runner, tmp_path):
        db_path, knowledge = _store(tmp_path)
        result = _search(runner, db_path, knowledge, "nothing here",
                         "--as-of", "2000-01-01")
        assert result.exit_code == 0, result.output

    def test_invalid_as_of_is_a_usage_error(self, runner, tmp_path):
        db_path, knowledge = _store(tmp_path)
        _record(runner, db_path, knowledge, "CliInvalid probe three")
        result = _search(runner, db_path, knowledge, "CliInvalid probe three",
                         "--as-of", "not-a-date")
        assert result.exit_code == 2
        assert "ISO-8601" in result.output

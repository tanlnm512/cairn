"""End-to-end overlap detection on the shared memory bus.

``cairn memory check --agent <id> --symbols a,b,c`` records the caller's
edit intent, then warns — one line per other agent — naming each other
agent and the overlapping symbols from their recent share/intent activity.
An agent's own activity never warns; with no overlap the check says so and
exits 0. Concurrent share/check writers on one store stay lossless under
WAL + busy_timeout, and single-agent memory commands never demand an agent
id nor emit overlap noise.
"""
from __future__ import annotations

import subprocess
import sys

import pytest
from click.testing import CliRunner

from cairn.graph.schema import get_db
from cairn.memory.store import create_memory, store_memory
from cairn.okf.bundle import OKFBundle

AGENT_A = "agentA"
AGENT_B = "agentB"
AGENT_C = "agentC"
AGENT_D = "agentD"

_AGENT_ENTRY = "from cairn.cli import main; main()"


@pytest.fixture
def runner():
    return CliRunner()


def _store(tmp_path):
    """File-backed store opened writable once so the schema exists."""
    db_path = str(tmp_path / "g.db")
    conn = get_db(db_path)
    conn.close()
    return db_path


def _share(runner, db_path, agent_id, symbols):
    from cairn.cli import main

    return runner.invoke(
        main,
        ["memory", "share", "--agent", agent_id, "--symbols", symbols,
         "--db", db_path],
    )


def _check(runner, db_path, agent_id, symbols):
    from cairn.cli import main

    return runner.invoke(
        main,
        ["memory", "check", "--agent", agent_id, "--symbols", symbols,
         "--db", db_path],
    )


def _warning_lines(output):
    return [line for line in output.splitlines()
            if line.startswith("Overlap warning:")]


def _seed_memory(knowledge, title, body):
    bundle = OKFBundle(knowledge)
    return store_memory(
        create_memory(type_="decision", title=title, body=body,
                      confidence=0.5),
        bundle,
    )


def test_warning_names_other_agent_and_overlapping_symbol(runner, tmp_path):
    """agentA's recent share on auth,session vs agentB's edit set
    auth,users: the warning names agentA and auth only — session and users
    are not reported as overlapping."""
    db_path = _store(tmp_path)
    share = _share(runner, db_path, AGENT_A, "auth,session")
    assert share.exit_code == 0, share.output

    checked = _check(runner, db_path, AGENT_B, "auth,users")
    assert checked.exit_code == 0, checked.output
    assert _warning_lines(checked.output) == [
        f"Overlap warning: agent '{AGENT_A}' has recent activity on auth"
    ]


def test_no_overlap_produces_no_warning(runner, tmp_path):
    """agentB's edit set disjoint from agentA's shared symbols checks
    clean: no warning and no other agent named."""
    db_path = _store(tmp_path)
    share = _share(runner, db_path, AGENT_A, "auth")
    assert share.exit_code == 0, share.output

    checked = _check(runner, db_path, AGENT_B, "parser,wiki")
    assert checked.exit_code == 0, checked.output
    assert "No overlap" in checked.output
    assert "Overlap warning" not in checked.output
    assert AGENT_A not in checked.output


def test_own_activity_is_not_an_overlap(runner, tmp_path):
    """An agent's own recent share on the checked symbol never warns: the
    overlap contract is against other agents only."""
    db_path = _store(tmp_path)
    share = _share(runner, db_path, AGENT_B, "auth")
    assert share.exit_code == 0, share.output

    checked = _check(runner, db_path, AGENT_B, "auth")
    assert checked.exit_code == 0, checked.output
    assert "No overlap" in checked.output
    assert "Overlap warning" not in checked.output


def test_edit_intent_alone_triggers_warning(runner, tmp_path):
    """agentA's earlier check (edit intent, nothing shared) warns agentB's
    overlapping edit set: edit activity counts like shared memory."""
    db_path = _store(tmp_path)
    first = _check(runner, db_path, AGENT_A, "payments")
    assert first.exit_code == 0, first.output
    assert "No overlap" in first.output

    second = _check(runner, db_path, AGENT_B, "payments,ledger")
    assert second.exit_code == 0, second.output
    assert _warning_lines(second.output) == [
        f"Overlap warning: agent '{AGENT_A}' has recent activity on payments"
    ]


def test_intent_records_surface_to_later_checks(runner, tmp_path):
    """Every check records the caller's intent: a third agent checking the
    same symbol is warned about both earlier checkers."""
    db_path = _store(tmp_path)
    a = _check(runner, db_path, AGENT_A, "auth")
    assert a.exit_code == 0, a.output
    b = _check(runner, db_path, AGENT_B, "auth")
    assert b.exit_code == 0, b.output
    assert _warning_lines(b.output) == [
        f"Overlap warning: agent '{AGENT_A}' has recent activity on auth"
    ]

    c = _check(runner, db_path, AGENT_C, "auth")
    assert c.exit_code == 0, c.output
    assert _warning_lines(c.output) == [
        f"Overlap warning: agent '{AGENT_A}' has recent activity on auth",
        f"Overlap warning: agent '{AGENT_B}' has recent activity on auth",
    ]


def test_one_warning_line_lists_every_overlapping_symbol(runner, tmp_path):
    """Share and intent from the same other agent collapse into one warning
    line listing every overlapping symbol; symbols outside the edit set
    stay out of the report."""
    db_path = _store(tmp_path)
    share = _share(runner, db_path, AGENT_A, "auth")
    assert share.exit_code == 0, share.output
    intent = _check(runner, db_path, AGENT_A, "session")
    assert intent.exit_code == 0, intent.output

    checked = _check(runner, db_path, AGENT_B, "auth,session,wiki")
    assert checked.exit_code == 0, checked.output
    assert _warning_lines(checked.output) == [
        f"Overlap warning: agent '{AGENT_A}'"
        f" has recent activity on auth, session"
    ]


def test_concurrent_share_and_check_writers_stay_lossless(runner, tmp_path):
    """Three agents race share/check writes on one store as separate
    processes: every row lands with its own kind, the journal mode stays
    WAL, and a later check is warned about the racing agents."""
    db_path = _store(tmp_path)
    workers = [
        ("share", AGENT_A, "auth,session"),
        ("check", AGENT_B, "auth,users"),
        ("check", AGENT_C, "parser,wiki"),
    ]
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _AGENT_ENTRY, "memory", verb,
             "--agent", agent_id, "--symbols", symbols, "--db", db_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for verb, agent_id, symbols in workers
    ]
    for proc in procs:
        _out, err = proc.communicate(timeout=120)
        assert proc.returncode == 0, err

    conn = get_db(db_path)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        rows = conn.execute(
            "SELECT agent_id, symbol, kind FROM agent_symbols"
            " ORDER BY agent_id, symbol"
        ).fetchall()
    finally:
        conn.close()
    assert [(r["agent_id"], r["symbol"], r["kind"]) for r in rows] == [
        (AGENT_A, "auth", "share"),
        (AGENT_A, "session", "share"),
        (AGENT_B, "auth", "intent"),
        (AGENT_B, "users", "intent"),
        (AGENT_C, "parser", "intent"),
        (AGENT_C, "wiki", "intent"),
    ]

    late = _check(runner, db_path, AGENT_D, "auth,users")
    assert late.exit_code == 0, late.output
    warnings = _warning_lines(late.output)
    agent_b_line = (
        f"Overlap warning: agent '{AGENT_B}'"
        f" has recent activity on auth, users"
    )
    assert len(warnings) == 2
    assert (f"Overlap warning: agent '{AGENT_A}'"
            f" has recent activity on auth") in warnings
    assert agent_b_line in warnings


def test_single_agent_memory_ops_run_without_agent_id(runner, tmp_path):
    """list and stats run with no --agent anywhere, alongside share/check
    traffic, and the recorded memory still lists — the single-agent surface
    is unchanged."""
    knowledge = str(tmp_path / "k")
    db_path = _store(tmp_path)
    _seed_memory(knowledge, "Cache stampede guard",
                 "Coalesce concurrent refills. Why: one flight.")

    share = _share(runner, db_path, AGENT_A, "auth")
    assert share.exit_code == 0, share.output
    checked = _check(runner, db_path, AGENT_B, "parser")
    assert checked.exit_code == 0, checked.output

    from cairn.cli import main

    listed = runner.invoke(
        main,
        ["memory", "list", "--db", db_path, "--knowledge", knowledge],
    )
    assert listed.exit_code == 0, listed.output
    assert "Cache stampede guard" in listed.output
    assert "Overlap warning" not in listed.output

    stats = runner.invoke(
        main,
        ["memory", "stats", "--knowledge", knowledge],
    )
    assert stats.exit_code == 0, stats.output
    assert "avg score" in stats.output


@pytest.mark.parametrize(
    "bad_id",
    ["", "   ", "a" * 300, '../evil" --x', "line1\nline2"],
)
def test_invalid_agent_ids_rejected_at_share_and_check(
    runner, tmp_path, bad_id
):
    """Both surfaces refuse ids violating non-empty / 64-char /
    [A-Za-z0-9._-] with a message naming the agent-id requirement, and
    record nothing under them."""
    db_path = _store(tmp_path)
    share = _share(runner, db_path, bad_id, "auth")
    check = _check(runner, db_path, bad_id, "auth")
    for result in (share, check):
        assert result.exit_code != 0, result.output
        assert "agent id" in result.output

    conn = get_db(db_path)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM agent_symbols"
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 0

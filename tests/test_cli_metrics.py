"""CLI invocation metrics: batch recording, exit-time drain, redaction, gates.

Covers the TC-001 / TC-002 / TC-005 auto halves of spec ``cli-usage-recording``
(FR-001, FR-003) against the landed implementation
(``cairn.telemetry.cli_metrics`` + ``_RecordingGroup`` in ``cairn.cli.main``):

* TC-001 -- a representative CliRunner batch against a tmp store with a forced
  drain: one row per invocation that reaches dispatch, with status/duration/
  timestamp per D-005.
* TC-005 -- argv summaries are scrubbed (``strip_private_data`` chokepoint) and
  capped at ``MAX_CLI_ARGS_SUMMARY_CHARS`` (200), both through the live invoke
  path and as a ``build_row`` unit.
* TC-002 / SC-2 -- a short-lived REAL process (subprocess of the actual entry
  point, tmp ``CAIRN_HOME``) lands its row purely via the atexit drain: the
  test performs no flush itself.
* Gates -- ``CAIRN_TELEMETRY=off`` records nothing (paired on/off run).
* TC-007 / FR-006 / D-003 -- session-identity derivation over an env matrix
  (``TERM_SESSION_ID`` > ``TMUX_PANE`` > per-invocation ``cli:<uuid>``) and
  the never-``unknown`` row assertion, both as ``build_row`` units and
  store-level after a live batch.

TWO empirically pinned divergences from the task brief's stated facts (click
8.4.2, verified against the landed code; the audit should reconcile these with
D-004/D-005 -- see the digest):

1. ``--help`` records NO row (nor does ``--version`` or any other group-level
   parse-time exit/parse error such as an unknown GROUP option). Those exits
   raise from ``parse_args`` and never reach ``_RecordingGroup.invoke``; the
   ``parse_args`` hook covers only the bare no-args shape (D-004). So the
   batch asserts "help -> zero rows", not "help -> ok".
2. ``tool_name`` is ``"cli:" + command_path`` WITHOUT the invoked subcommand
   suffix (``cli:main`` under CliRunner, ``cli:cairn`` in a real run). D-005's
   wrapper reads ``ctx.invoked_subcommand`` BEFORE ``super().invoke()``, but
   click 8.4.2 sets it inside ``Group.invoke`` (after capture), so the suffix
   never lands; deeper command identity is visible only via ``args_summary``.

Hermetic: the suite-wide ``_hermetic_env`` conftest scrubs ``CAIRN_*`` env per
test; the autouse fixture here then points ``CAIRN_HOME``/``CAIRN_DB`` at the
test's tmp sandbox, pre-creates the store, resets ``cli_metrics`` module state
(mirroring ``test_metrics.py::_reset_metric_state``), and injects the same
call-time-resolving factory ``_wire_flusher_conn`` installs in production.
"""
from __future__ import annotations

import importlib
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

import cairn
from cairn.cli import main as cairn_cli  # the click Group (shadows the submodule attr)
from cairn.cli.main import _RecordingGroup
from cairn.graph.schema import _apply_schema, get_db
from cairn.telemetry import cli_metrics

# ``cairn.cli.main`` (the package attribute) is shadowed by the click group
# after cli/__init__ re-exports it; import_module returns the real module so
# the teardown can reset _FLUSH_CONN_WIRED.
_cli_main_module = importlib.import_module("cairn.cli.main")

# Canary shaped to actually match privacy.py's ``sk-ant-[A-Za-z0-9\-_]{20,}``
# pattern (the brief's literal "sk-ant-TEST123" payload is too short to match,
# so it would never be redacted). Placed EARLY in argv so the 200-char
# truncation alone could not remove it -- the "not in summary" assertion then
# proves redaction, not truncation.
_CANARY = "sk-ant-TEST123CANARY00000000"


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def cli_store(monkeypatch, tmp_path):
    """Hermetic per-test store + ``cli_metrics`` state reset.

    Sets ``CAIRN_HOME``/``CAIRN_DB`` into the test sandbox (overriding
    ``_hermetic_env``'s defaults), pre-creates the store so flushes never race
    first-open, resets ``cli_metrics`` module globals exactly like the metric
    suites reset ``metric_buffering``, and injects the production-shaped
    factory (resolves the store at FLUSH time from env). Teardown re-resets
    and restores ``_FLUSH_CONN_WIRED`` so later suites in the same
    process see pristine wiring.
    """
    home = tmp_path / "cairn-home"
    home.mkdir()
    db = tmp_path / "graph.db"
    monkeypatch.setenv("CAIRN_HOME", str(home))
    monkeypatch.setenv("CAIRN_DB", str(db))
    monkeypatch.delenv("CAIRN_TELEMETRY", raising=False)
    monkeypatch.delenv("CAIRN_READ_ONLY", raising=False)

    conn = sqlite3.connect(str(db))
    _apply_schema(conn)
    conn.commit()
    conn.close()

    cli_metrics._reset_for_tests()
    cli_metrics.configure_conn(lambda: get_db(str(db)))
    yield db
    cli_metrics._reset_for_tests()
    _cli_main_module._FLUSH_CONN_WIRED = False


def _rows(db, marker=None):
    """Read ``tool_metrics`` rows (optionally filtered by argv marker)."""
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        if marker is None:
            cursor = conn.execute("SELECT * FROM tool_metrics ORDER BY rowid")
        else:
            cursor = conn.execute(
                "SELECT * FROM tool_metrics WHERE args_summary LIKE ? ORDER BY rowid",
                (f"%{marker}%",),
            )
        return [dict(row) for row in cursor]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# TC-001: representative batch -> one row per invocation
# ---------------------------------------------------------------------------


def test_batch_one_row_per_invocation(cli_store, monkeypatch):
    """Each dispatch-reaching invocation lands exactly one correctly-stamped row.

    The recorded argv is ``sys.argv[1:]`` (not CliRunner's args), so each case
    tags ``sys.argv`` with a unique marker to make its row addressable. Expected
    exits: 0 for help/config, 2 for the parse-error shapes (click's UsageError
    exit code). Divergences pinned per the module docstring: ``--help`` records
    nothing (parse-time Exit never reaches the wrapper) and ``tool_name``
    carries no subcommand suffix.
    """
    runner = CliRunner()
    # (marker, args, expected exit, expected rows, (status, error substring))
    cases = [
        ("mk-help", ["--help"], 0, 0, None),
        ("mk-config", ["config", "--db"], 0, 1, ("ok", None)),
        ("mk-badopt", ["config", "--bogus"], 2, 1, ("error", "No such option")),
        ("mk-badcmd", ["no-such-command-xyz"], 2, 1, ("error", "No such command")),
        ("mk-bare", [], 2, 1, ("error", "no command given")),
    ]
    for marker, args, want_exit, want_rows, expect in cases:
        t0 = time.time()
        # The wrapper snapshots sys.argv[1:] at dispatch time; give this case a
        # unique one so its row is identifiable regardless of CliRunner args.
        monkeypatch.setattr(sys, "argv", ["cairn", marker])
        result = runner.invoke(cairn_cli, args)
        t1 = time.time()
        cli_metrics._flush_cli_metrics()

        assert result.exit_code == want_exit, (marker, result.exit_code, result.output)
        rows = _rows(cli_store, marker)
        assert len(rows) == want_rows, (marker, rows)
        if expect is None:
            continue
        status, error_fragment = expect
        row = rows[0]
        # D-005: one subcommand level rides the tool_name (deeper identity
        # lives in args_summary). Unknown commands fail resolution before
        # click sets invoked_subcommand, so those rows stay root-only.
        resolved = args and not (
            error_fragment and "No such command" in error_fragment
        )
        expected_name = f"cli:main {args[0]}" if resolved else "cli:main"
        assert row["tool_name"] == expected_name, row
        assert row["status"] == status, row
        assert row["duration_ms"] >= 0.0, row
        assert t0 - 1.0 <= row["invoked_at"] <= t1 + 5.0, row
        if error_fragment is None:
            assert row["error_message"] is None, row
        else:
            assert error_fragment in (row["error_message"] or ""), row
        if marker == "mk-bare":
            # D-004: the parse_args hook has no timing context -> literal 0.0.
            assert row["duration_ms"] == 0.0, row
        if marker == "mk-config":
            # The command body genuinely ran (its output is the resolved db).
            assert str(cli_store) in result.output

    # Exactly one row per dispatch-reaching invocation -- no duplicates, no
    # cross-case bleed (the --help case contributed none).
    assert len(_rows(cli_store)) == 4


# ---------------------------------------------------------------------------
# D-005: exit-code semantics (ok on exit(0), error + str(code)/str(exc) else)
# ---------------------------------------------------------------------------


def test_exit_code_semantics_per_d005(cli_store, monkeypatch):
    """Pin D-005's status rules on a scratch ``_RecordingGroup``.

    A throwaway group (NOT registered on ``main`` -- that would leak a command
    into every later test's help output in this process) exercises the four
    exit shapes the CLI's real commands use: plain return, ``sys.exit(0)``,
    ``click.exceptions.Exit`` (ctx.exit), ``sys.exit(3)``, and an unexpected
    exception.
    """
    group = _RecordingGroup(name="fake")

    @group.command(name="plain")
    def plain():
        return "done"

    @group.command(name="zero")
    def zero():
        raise SystemExit(0)

    @group.command(name="cexit")
    def cexit():
        raise click.exceptions.Exit(0)

    @group.command(name="three")
    def three():
        raise SystemExit(3)

    @group.command(name="raises")
    def raises_():
        raise RuntimeError("boom")

    # (marker, command, expected CliRunner exit, expected status, expected error)
    shapes = [
        ("x-plain", "plain", 0, "ok", None),
        ("x-zero", "zero", 0, "ok", None),
        ("x-cexit", "cexit", 0, "ok", None),
        ("x-three", "three", 3, "error", "3"),
        ("x-raises", "raises", 1, "error", "boom"),
    ]
    runner = CliRunner()
    for marker, cmd, want_exit, want_status, want_error in shapes:
        monkeypatch.setattr(sys, "argv", ["fake", marker])
        result = runner.invoke(group, [cmd])
        cli_metrics._flush_cli_metrics()

        assert result.exit_code == want_exit, (cmd, result.exit_code)
        rows = _rows(cli_store, marker)
        assert len(rows) == 1, (cmd, rows)
        row = rows[0]
        assert row["tool_name"] == f"cli:fake {cmd}", row
        assert row["status"] == want_status, row
        assert row["duration_ms"] >= 0.0, row
        assert row["error_message"] == want_error, row

    assert len(_rows(cli_store)) == 5


# ---------------------------------------------------------------------------
# TC-005: redaction + truncation at the write chokepoint
# ---------------------------------------------------------------------------


def test_recorded_summary_redacts_and_truncates(cli_store, monkeypatch):
    """The stored argv summary contains neither the canary nor >200 chars.

    Drives the live invoke path with ``sys.argv`` embedding a secret-shaped
    canary early (so truncation alone couldn't remove it) plus a long filler
    argument that pushes the raw JSON far past the cap.
    """
    monkeypatch.setattr(
        sys, "argv", ["cairn", "--api-key", _CANARY, "L" * 400]
    )
    result = CliRunner().invoke(cairn_cli, ["config", "--db"])
    assert result.exit_code == 0
    cli_metrics._flush_cli_metrics()

    rows = _rows(cli_store, "--api-key")
    assert len(rows) == 1, rows
    summary = rows[0]["args_summary"]
    assert summary is not None
    assert _CANARY not in summary
    # The canary was REPLACED (not merely truncated past 200): its stand-in is
    # present within the capped window.
    assert "[REDACTED_SECRET]" in summary
    assert len(summary) <= cli_metrics.MAX_CLI_ARGS_SUMMARY_CHARS
    assert len(summary) == cli_metrics.MAX_CLI_ARGS_SUMMARY_CHARS  # filler forces the cap


def test_build_row_redaction_chokepoint_unit():
    """``build_row`` unit: scrub + caps on both args_summary and error_message.

    Positional contract of the returned tuple (== ``_INSERT_SQL`` columns):
    (tool_name, session_id, invoked_at, duration_ms, status, error_message,
    req_chars, resp_chars, args_summary, source).
    """
    long_error = "failed " + _CANARY + " " + "e" * 600
    row = cli_metrics.build_row(
        "cairn memory record",
        ["--key", _CANARY, "x" * 500],
        12.5,
        "error",
        long_error,
    )
    (
        tool_name,
        _sid,
        invoked_at,
        duration_ms,
        status,
        error_message,
        req_chars,
        resp_chars,
        args_summary,
        source,
    ) = row
    assert tool_name == "cli:cairn memory record"
    assert source == "cli"  # FR-002: explicit on every CLI row (D-002)
    assert status == "error"
    assert duration_ms == 12.5
    assert invoked_at > 0
    assert req_chars > 0
    assert resp_chars is None
    assert _CANARY not in (args_summary or "")
    assert "[REDACTED_SECRET]" in args_summary
    assert len(args_summary) <= cli_metrics.MAX_CLI_ARGS_SUMMARY_CHARS
    assert _CANARY not in error_message
    assert "[REDACTED_SECRET]" in error_message
    assert len(error_message) <= 500  # the error-side cap (500, not 200)


# ---------------------------------------------------------------------------
# TC-002 / SC-2: short-lived process drains on exit (no manual flush)
# ---------------------------------------------------------------------------


def test_short_lived_process_drains_on_exit_atexit(tmp_path):
    """A real subprocess of the real entry point lands its row via atexit only.

    Runs ``cairn.cli.main`` under a fresh interpreter with a tmp
    ``CAIRN_HOME``/``CAIRN_DB`` (one fast command), waits for exit, then opens
    the store and asserts the ``cli:`` row EXISTS. The test never calls any
    flush -- the row can only have landed through the shared sink's atexit
    drain (FR-003's flush-on-clean-exit path). ``prog_name='cairn'`` mirrors
    the installed ``cairn`` entry point's program name.
    """
    home = tmp_path / "exit-drain-home"
    home.mkdir()
    db = tmp_path / "exit-drain.db"

    env = {k: v for k, v in os.environ.items() if not k.startswith("CAIRN_")}
    env["CAIRN_HOME"] = str(home)
    env["CAIRN_DB"] = str(db)
    # Make the import work even when the test interpreter is not the uv venv
    # that carries the (editable) install.
    src_root = str(Path(cairn.__file__).resolve().parent.parent)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = f"{src_root}{os.pathsep}{existing}" if existing else src_root

    code = "import sys; from cairn.cli import main; main(args=sys.argv[1:], prog_name='cairn')"
    proc = subprocess.run(
        [sys.executable, "-c", code, "config", "--db"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    assert str(db) in proc.stdout  # the command body ran, not just its parse

    assert db.exists()
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM tool_metrics")]
    finally:
        conn.close()
    assert len(rows) == 1, rows
    row = rows[0]
    # D-005: one subcommand level rides the tool_name (deeper identity in
    # args_summary).
    assert row["tool_name"] == "cli:cairn config"
    assert row["status"] == "ok"
    assert row["error_message"] is None
    assert row["duration_ms"] >= 0.0
    assert row["invoked_at"] > 0
    assert "config" in (row["args_summary"] or "")


# ---------------------------------------------------------------------------
# Gates: CAIRN_TELEMETRY=off records nothing (paired run)
# ---------------------------------------------------------------------------


def test_telemetry_off_records_nothing_paired(cli_store, monkeypatch):
    """Paired on/off runs: identical command, rows only when telemetry is on."""
    runner = CliRunner()

    monkeypatch.setenv("CAIRN_TELEMETRY", "off")
    result_off = runner.invoke(cairn_cli, ["config", "--db"])
    cli_metrics._flush_cli_metrics()
    assert result_off.exit_code == 0
    assert _rows(cli_store) == []
    assert len(cli_metrics._CLI_BUFFER) == 0  # gated before buffering

    monkeypatch.delenv("CAIRN_TELEMETRY")
    result_on = runner.invoke(cairn_cli, ["config", "--db"])
    cli_metrics._flush_cli_metrics()
    assert result_on.exit_code == 0
    # Command behavior is otherwise identical (the paired-output half of TC-006).
    assert result_on.output == result_off.output
    rows = _rows(cli_store)
    assert len(rows) == 1
    assert rows[0]["status"] == "ok"


# ---------------------------------------------------------------------------
# TC-007 / FR-006 / D-003: session identity derivation, never "unknown"
# ---------------------------------------------------------------------------

# Every env shape derive_session_id can see. (term, pane); None = unset.
_ENV_SHAPES = [
    ("term-sess-A", None),  # terminal id present -> term:<v>
    (None, "17"),  # tmux pane only -> tmux:<v>
    ("term-sess-B", "23"),  # both -> term wins (landed precedence)
    (None, None),  # neither -> per-invocation uuid fallback
]


def _apply_env_shape(monkeypatch, term, pane):
    """Set one identity env shape, scrubbing BOTH vars first.

    The suite-wide ``_hermetic_env`` conftest scrubs only ``CAIRN_*`` vars, so
    without this scrub a dev machine running pytest inside tmux/iTerm would
    silently turn every ``(None, None)`` case into a ``term:``/``tmux:`` hit --
    the fallback path would never be exercised where it matters.
    """
    monkeypatch.delenv("TERM_SESSION_ID", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    if term is not None:
        monkeypatch.setenv("TERM_SESSION_ID", term)
    if pane is not None:
        monkeypatch.setenv("TMUX_PANE", pane)


def test_derive_session_id_env_matrix(monkeypatch):
    """D-003 precedence: term:<v> > tmux:<v> > fresh cli:<uuid> per call."""
    # TERM_SESSION_ID set -> "term:<v>", STABLE across calls (one shell groups).
    _apply_env_shape(monkeypatch, "abc-123", None)
    assert cli_metrics.derive_session_id() == "term:abc-123"
    assert cli_metrics.derive_session_id() == "term:abc-123"

    # TMUX_PANE set, no TERM_SESSION_ID -> "tmux:<v>".
    _apply_env_shape(monkeypatch, None, "42")
    assert cli_metrics.derive_session_id() == "tmux:42"

    # Both set -> term wins (the landed precedence, verified here).
    _apply_env_shape(monkeypatch, "abc-123", "42")
    assert cli_metrics.derive_session_id() == "term:abc-123"

    # Neither -> "cli:"-prefixed and DIFFERENT across calls: each invocation
    # is its own session (FR-006's per-invocation fallback).
    _apply_env_shape(monkeypatch, None, None)
    first = cli_metrics.derive_session_id()
    second = cli_metrics.derive_session_id()
    assert first.startswith("cli:")
    assert second.startswith("cli:")
    assert first != second


def test_build_row_session_id_never_unknown(monkeypatch):
    """build_row stamps a real session id under every env shape (FR-006).

    Belt to the env matrix's braces: whatever the host env looks like, the
    row builder can never emit the table's legacy ``unknown`` default (a CLI
    row stamped ``unknown`` would vanish into the mega-session that
    ui-dashboard-traffic-scale exists to bound). session_id is tuple slot 1
    (the ``_INSERT_SQL`` column order).
    """
    for term, pane in _ENV_SHAPES:
        _apply_env_shape(monkeypatch, term, pane)
        row = cli_metrics.build_row("cairn config", ["--db"], 1.0, "ok")
        session_id = row[1]
        assert session_id, (term, pane, session_id)
        assert session_id != "unknown", (term, pane, session_id)


def test_no_unknown_cli_session_rows_in_store(cli_store, monkeypatch):
    """Store-level TC-007: a live batch across env shapes, none ``unknown``.

    Drives the real invoke path under each identity shape -- the same shell
    twice (groups), a tmux pane once, and two identity-less invocations
    (per-invocation, distinct) -- then asserts on the persisted rows: no
    ``tool_metrics`` row with ``source='cli'`` carries session_id ``unknown``,
    and the FR-006 grouping semantics hold (shared terminal -> ONE id;
    no identity -> distinct ids).
    """
    shapes = [
        ("term-sess-A", None),  # same shell...
        ("term-sess-A", None),  # ...twice -> both rows group
        (None, "9"),  # tmux pane
        (None, None),  # no identity...
        (None, None),  # ...twice -> distinct per-invocation ids
    ]
    runner = CliRunner()
    for i, (term, pane) in enumerate(shapes):
        _apply_env_shape(monkeypatch, term, pane)
        monkeypatch.setattr(sys, "argv", ["cairn", f"idcase{i}"])
        result = runner.invoke(cairn_cli, ["config", "--db"])
        assert result.exit_code == 0, result.output
        cli_metrics._flush_cli_metrics()

    rows = _rows(cli_store)
    assert len(rows) == len(shapes), rows
    cli_rows = [r for r in rows if r["source"] == "cli"]
    assert len(cli_rows) == len(shapes), rows  # every row is a CLI row here

    # The never-unknown row assertion, store-level (belt-and-braces with the
    # env matrix): no cli-sourced tool_metrics row lands in 'unknown'.
    assert all(r["session_id"] != "unknown" for r in cli_rows), cli_rows

    # FR-006 grouping: shared terminal id -> one session for both invocations.
    assert sum(1 for r in cli_rows if r["session_id"] == "term:term-sess-A") == 2
    assert sum(1 for r in cli_rows if r["session_id"] == "tmux:9") == 1
    # Fallback: per-invocation identities, all distinct.
    fallback = [r["session_id"] for r in cli_rows if r["session_id"].startswith("cli:")]
    assert len(fallback) == 2
    assert len(set(fallback)) == 2

"""Tests for the central `cairn` logging config (spec T01).

Covers: ``CAIRN_LOG_LEVEL`` resolution (DEBUG / default WARNING / invalid ->
WARNING + stderr notice), case-insensitivity, the ``-v`` group-flag path via
CliRunner, idempotency, the stderr-only handler, and the cardinal rule that
the root logger is never configured by us.

The ``cairn`` logger is process-global, so every test restores its
handlers/level (and the env var) via ``reset_cairn_logger`` to avoid leaking
state into sibling tests — especially the caplog-based ones in
``test_embedding_backend_quality.py``, which rely on cairn records still
propagating to root (we therefore never disable propagation).
"""
from __future__ import annotations

import logging
import os
import sys

import pytest
from click.testing import CliRunner

from cairn.cli import main
from cairn.utils.logging import configure_logging

_CAIRN = logging.getLogger("cairn")


@pytest.fixture
def reset_cairn_logger(monkeypatch):
    """Snapshot, isolate & restore the process-global `cairn` logger + env var.

    configure_logging() mutates the shared `cairn` logger (attaches a handler,
    sets the level). Without this fixture those mutations would leak across
    tests: a later test seeing an unexpected DEBUG level, or a stacked handler
    doubling up log lines. We also pin CAIRN_LOG_LEVEL off during the test and
    restore the original env on teardown.

    Handlers are DETACHED at setup (not just snapshotted): earlier
    full-suite tests invoke the CLI, whose group callback attaches a handler
    capturing *their* pytest-swapped stderr. Without the detach, our
    idempotent configure_logging() would keep that stale handler and the
    assertions below would test another test's stream.
    """
    saved_handlers = _CAIRN.handlers[:]
    saved_level = _CAIRN.level
    _CAIRN.handlers = []
    monkeypatch.delenv("CAIRN_LOG_LEVEL", raising=False)
    yield _CAIRN
    _CAIRN.handlers = saved_handlers
    _CAIRN.setLevel(saved_level)


# --- env-var level resolution ------------------------------------------------


def test_default_level_is_warning(reset_cairn_logger):
    """No env var, no flag -> WARNING (the spec default)."""
    assert configure_logging() == "WARNING"
    assert reset_cairn_logger.level == logging.WARNING


def test_env_debug_sets_debug(reset_cairn_logger):
    os.environ["CAIRN_LOG_LEVEL"] = "DEBUG"
    assert configure_logging() == "DEBUG"
    assert reset_cairn_logger.level == logging.DEBUG


def test_env_is_case_insensitive(reset_cairn_logger):
    """DEBUG / debug / Debug all resolve to the same level."""
    for raw in ("debug", "Info", "WARNING", "error", "CRITICAL"):
        os.environ["CAIRN_LOG_LEVEL"] = raw
        assert configure_logging() == raw.upper()


def test_invalid_env_falls_back_to_warning(reset_cairn_logger, capsys):
    """A bogus value never crashes; it warns once on stderr and uses WARNING."""
    os.environ["CAIRN_LOG_LEVEL"] = "VERBOSE"  # not a real level
    assert configure_logging() == "WARNING"
    assert reset_cairn_logger.level == logging.WARNING
    err = capsys.readouterr().err
    assert "invalid" in err.lower()
    assert "VERBOSE" in err
    # Exactly one notice line.
    assert err.strip().count("\n") == 0


def test_blank_env_falls_back_to_default(reset_cairn_logger):
    os.environ["CAIRN_LOG_LEVEL"] = "   "
    assert configure_logging() == "WARNING"


# --- verbose flag & CLI path -------------------------------------------------


def test_verbose_flag_forces_debug(reset_cairn_logger):
    """-v wins over the env var (here DEBUG), per spec precedence."""
    os.environ["CAIRN_LOG_LEVEL"] = "ERROR"
    assert configure_logging(verbose=True) == "DEBUG"
    assert reset_cairn_logger.level == logging.DEBUG


def test_verbose_flag_via_cli(reset_cairn_logger):
    """`cairn -v <cmd>` routes the flag through the group callback.

    Uses `build --help` as a no-side-effect subcommand: click runs the group
    callback (-> configure_logging(verbose=True)) before printing help and
    exiting 0, so the level is set without needing a DB. Note the flag MUST
    precede the subcommand — `cairn build -v` is rejected by click as an
    unknown option of `build` (verified against click 8.4.2).
    """
    os.environ.pop("CAIRN_LOG_LEVEL", None)
    runner = CliRunner()
    result = runner.invoke(main, ["-v", "build", "--help"], catch_exceptions=False)
    assert result.exit_code == 0
    assert reset_cairn_logger.level == logging.DEBUG


def test_env_debug_via_cli(reset_cairn_logger):
    """`CAIRN_LOG_LEVEL=DEBUG cairn <cmd>` (position-independent) sets DEBUG."""
    os.environ["CAIRN_LOG_LEVEL"] = "DEBUG"
    runner = CliRunner()
    result = runner.invoke(main, ["build", "--help"], catch_exceptions=False)
    assert result.exit_code == 0
    assert reset_cairn_logger.level == logging.DEBUG


def test_default_cli_leaves_level_at_warning(reset_cairn_logger):
    """Default invocation configures WARNING; normal output is unaffected."""
    runner = CliRunner()
    result = runner.invoke(main, ["build", "--help"], catch_exceptions=False)
    assert result.exit_code == 0
    assert reset_cairn_logger.level == logging.WARNING


# --- handler / root invariants ----------------------------------------------


def test_handler_is_stderr_only(reset_cairn_logger):
    """The single handler we attach streams to stderr, not stdout."""
    configure_logging()
    handlers = reset_cairn_logger.handlers
    assert len(handlers) == 1
    h = handlers[0]
    assert isinstance(h, logging.StreamHandler)
    assert h.stream is sys.stderr


def test_configure_is_idempotent(reset_cairn_logger):
    """Repeated calls never stack handlers (but do re-apply the level)."""
    configure_logging()
    configure_logging(verbose=True)
    configure_logging()
    assert len(reset_cairn_logger.handlers) == 1
    assert reset_cairn_logger.level == logging.WARNING


def test_root_logger_untouched(reset_cairn_logger):
    """We add no handlers to root and never reconfigure it."""
    root = logging.getLogger()
    before = root.handlers[:]
    configure_logging(verbose=True)
    assert root.handlers[:] == before


def test_module_logger_inherits_level(reset_cairn_logger):
    """A `cairn.*` child picks up the parent's effective level."""
    os.environ["CAIRN_LOG_LEVEL"] = "DEBUG"
    configure_logging()
    child = logging.getLogger("cairn.graph.semantic")
    assert child.getEffectiveLevel() == logging.DEBUG

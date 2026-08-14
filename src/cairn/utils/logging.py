"""Central logging configuration for the ``cairn`` namespace.

The single config point invoked from both the CLI (``cli/main.py`` group
callback) and the MCP server (``mcp_server/server.run()``). It configures
ONLY the ``cairn`` logger and NEVER the root logger, because:

* **stdout is sacred.** Under the stdio MCP transport stdout is the JSON-RPC
  channel the client reads; routing library/framework logs there (which is
  what a root handler would do) corrupts message framing.
* FastMCP pins its own ``log_level="WARNING"``
  (``mcp_server/_server_core.py:75``) precisely to avoid reconfiguring root
  when the singleton is constructed on every CLI invocation; this helper
  respects that same rationale by steering well clear of root.

Every cairn module logger is created via ``logging.getLogger(__name__)``
(e.g. ``cairn.graph.semantic``, ``cairn.memory.promotion``), so configuring
the parent ``cairn`` logger cascades to all of them through the standard
effective-level / propagation mechanism.

Propagation is left at its default (``True``) on purpose: pytest's ``caplog``
fixture attaches its capture handler to the *root* logger and relies on
records propagating up through ``cairn`` to reach it. Since we never attach a
handler to root ourselves, in production cairn records are emitted exactly
once (by our own handler on ``cairn``) and never double-emit.
"""
from __future__ import annotations

import logging
import os
import sys

__all__ = ["configure_logging"]

# The cairn namespace logger — the single ancestor of every
# `logging.getLogger("cairn.<...>")` created across the codebase.
_CAIRN_LOGGER_NAME = "cairn"

_DEFAULT_LEVEL = "WARNING"
_VALID_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
# Keep the format human-readable and prefix the logger name so multi-module
# output stays attributable (e.g. `cairn.graph.semantic`). No ANSI/colors:
# output may be piped (CliRunner, logs to a file).
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class _StderrHandler(logging.StreamHandler):
    """StreamHandler that resolves ``sys.stderr`` at *emit* time.

    A plain ``StreamHandler(sys.stderr)`` binds whatever object ``sys.stderr``
    references at construction. This process-global ``cairn`` logger outlives
    stderr swaps (pytest capture, test harnesses, embedders redirecting
    streams), which would strand records on a dead/captured stream. Emit-time
    resolution keeps every record on the *current* stderr.
    """

    def emit(self, record: logging.LogRecord) -> None:
        self.stream = sys.stderr  # re-resolve on every record
        super().emit(record)


def _resolve_level(override: str | None) -> str:
    """Return a validated, uppercased level name; WARNING on invalid input.

    Resolution precedence: ``override`` (caller-supplied, e.g. a resolved
    flag) → ``CAIRN_LOG_LEVEL`` env → ``WARNING``. Values are matched
    case-insensitively (``debug``/``DEBUG``/``Debug`` all work).

    An invalid value falls back to ``WARNING`` and emits exactly one notice
    to **stderr** (never stdout — stdout is the JSON-RPC channel under stdio).
    It never raises, so a typo in ``CAIRN_LOG_LEVEL`` can't break
    ``cairn build`` or server boot.
    """
    raw = (override or os.environ.get("CAIRN_LOG_LEVEL") or _DEFAULT_LEVEL).strip()
    upper = raw.upper()
    if upper not in _VALID_LEVELS:
        print(
            f"cairn: invalid CAIRN_LOG_LEVEL={raw!r} (expected one of "
            f"{', '.join(_VALID_LEVELS)}); defaulting to {_DEFAULT_LEVEL}",
            file=sys.stderr,
        )
        return _DEFAULT_LEVEL
    return upper


def configure_logging(verbose: bool = False, level_override: str | None = None) -> str:
    """Configure the ``cairn`` logger. Idempotent; never touches root.

    Args:
        verbose: if ``True``, force ``DEBUG``. The ``-v`` CLI flag wins over
            the environment, matching `tasks.md` T01 ("-v/--verbose → DEBUG").
        level_override: an explicit level name; wins over the env but loses
            to ``verbose``. Kept for callers that resolve a level themselves.

    Returns:
        The resolved (uppercased) level name, for callers that want it.

    Level precedence: ``verbose`` > ``level_override`` > ``CAIRN_LOG_LEVEL``
    > ``WARNING``.

    The handler is attached at most once (skipped if the ``cairn`` logger
    already has handlers), so repeated calls — including CLI + server in one
    process — never produce duplicate log lines. The level, however, is
    re-applied on every call so a later ``-v`` can raise an existing session
    to DEBUG without stacking handlers.
    """
    level_name = "DEBUG" if verbose else _resolve_level(level_override)

    logger = logging.getLogger(_CAIRN_LOGGER_NAME)
    if not logger.handlers:
        # stderr only — see module docstring (stdout is the JSON-RPC channel).
        handler = _StderrHandler()
        handler.setFormatter(logging.Formatter(_FORMAT))
        logger.addHandler(handler)
    logger.setLevel(level_name)
    return level_name

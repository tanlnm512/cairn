"""Central logging configuration for the ``cairn`` namespace."""
from __future__ import annotations

import logging
import os
import sys

__all__ = ["configure_logging", "quiet_server_noise"]

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
    """Logging handler that re-resolves sys.stderr dynamically at emit time."""

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


# Third-party loggers that are WARNING-noisy in long-running servers.
_NOISY_LOGGERS = ("huggingface_hub", "mcp")


def quiet_server_noise() -> None:
    """Suppress non-actionable third-party output for long-running servers.

    1. `_NOISY_LOGGERS` loggers -> ERROR (Hub rate-limit hints,
       MCP pre-initialization request chatter)
    2. progress bars -> off (huggingface_hub ``disable_progress_bars``;
       ``TQDM_DISABLE=1`` as fallback): bar escape sequences corrupt
       log files

    Existing ``TQDM_DISABLE`` values are respected. Idempotent.
    """
    os.environ.setdefault("TQDM_DISABLE", "1")
    try:
        from huggingface_hub.utils import disable_progress_bars

        disable_progress_bars()
    except ImportError:
        pass  # optional dependency; TQDM_DISABLE covers its bars
    # After the HF import: importing huggingface_hub re-runs its own
    # logging setup and would reset the level set here.
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.ERROR)

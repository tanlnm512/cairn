"""cairn CLI main group and shared imports.

The `cairn` command group lives here. Individual command modules under cli/
decorate their commands onto this `main` via @main.command() /
@<subgroup>.command(); the package __init__.py imports each module for the
side effect of registration.
"""
from __future__ import annotations

import sys
import time
from typing import Any

import click

from ..graph import builder  # noqa: F401  (re-exported for command modules)
from ..graph import queries  # noqa: F401
from ..graph import scanner as scanner_mod  # noqa: F401
from ..graph.schema import DEFAULT_DB_PATH, get_db  # noqa: F401
from ..paths import default_knowledge_path, resolve_store
from ..utils.logging import configure_logging

DEFAULT_KNOWLEDGE_PATH = str(default_knowledge_path())

# Wired-once flag: the cli-metrics flusher's connection factory is injected the
# first time a command dispatches, never at import. The factory itself resolves
# the store at CALL time (once per flush), so CAIRN_DB/cwd are read when
# flushing, not at boot.
_FLUSH_CONN_WIRED = False


def _wire_flusher_conn() -> None:
    """Inject the cli-metrics flusher's writable connection factory (once).

    Best-effort by contract: a telemetry-module import failure or a wiring
    error must never kill the command — skip wiring and leave rows buffered.
    """
    global _FLUSH_CONN_WIRED
    if _FLUSH_CONN_WIRED:
        return
    try:
        from ..telemetry import cli_metrics

        cli_metrics.configure_conn(lambda: get_db(str(resolve_store().db)))
        _FLUSH_CONN_WIRED = True
    except Exception:
        pass


def _record_invocation(
    command_path: str,
    argv: list[str],
    duration_ms: float,
    status: str,
    error_message: str = "",
) -> None:
    """Buffer one usage row via the cli-metrics builder; never raises.

    Recording must never fail the command (FR-001 best-effort doctrine), so
    even the import is guarded — a missing/broken telemetry module degrades
    to "no row", never to a failed CLI run.
    """
    try:
        from ..telemetry import cli_metrics

        cli_metrics.record_cli_invocation(
            command_path=command_path,
            argv=argv,
            duration_ms=duration_ms,
            status=status,
            error_message=error_message,
        )
    except Exception:
        pass


class _RecordingGroup(click.Group):
    """Group whose dispatch records every top-level invocation (FR-001).

    The single interception point (D-001): one row per top-level command —
    commands registered today or in the future are covered automatically, and
    subcommand hops are intentionally not rows. Timing/status capture happens
    in `invoke`; the `parse_args` hook exists only because click 8.x raises
    NoArgsIsHelpError from parse_args for a bare `cairn` and never reaches
    `invoke`.
    """

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        # Bare `cairn` (no subcommand) exits from parse_args with the help
        # text — record it here, then let super() raise exactly as before.
        if not args and self.no_args_is_help and not ctx.resilient_parsing:
            _wire_flusher_conn()
            _record_invocation(
                ctx.command_path, sys.argv[1:], 0.0, "error", "no command given"
            )
        return super().parse_args(ctx, args)

    def invoke(self, ctx: click.Context) -> Any:
        # Captured before dispatch so the row exists even on error paths.
        # invoked_subcommand is read at RECORD time — click's Group.invoke
        # sets it while resolving the subcommand, so it is available after
        # super().invoke() on success and error paths alike, extending the
        # root path one level (per-subcommand aggregation, D-005).
        argv = sys.argv[1:]

        def sub_path() -> str:
            return " ".join(
                p
                for p in (ctx.command_path, getattr(ctx, "invoked_subcommand", None))
                if p
            )

        _wire_flusher_conn()
        t0 = time.time()
        try:
            result = super().invoke(ctx)
        except BaseException as exc:
            duration_ms = (time.time() - t0) * 1000.0
            if isinstance(exc, (SystemExit, click.exceptions.Exit)):
                # This CLI signals success via sys.exit(0)/ctx.exit() too
                # (bench, embed, ...): only a non-zero code is an error.
                code = getattr(exc, "exit_code", getattr(exc, "code", None))
                if code is None or code == 0:
                    _record_invocation(sub_path(), argv, duration_ms, "ok")
                else:
                    _record_invocation(
                        sub_path(), argv, duration_ms, "error", str(code)
                    )
            else:
                # Includes Click's UsageError flow — recorded, then
                # re-raised so Click formats it exactly as before.
                _record_invocation(sub_path(), argv, duration_ms, "error", str(exc))
            raise
        duration_ms = (time.time() - t0) * 1000.0
        _record_invocation(sub_path(), argv, duration_ms, "ok")
        return result


@click.group(cls=_RecordingGroup)
@click.version_option(
    version=__import__("cairn").__version__,
    message="cairn-intel %(version)s",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="Enable DEBUG logging for the cairn namespace (overrides CAIRN_LOG_LEVEL).",
)
def main(verbose: bool):
    """cairn-intel: local codebase intelligence system."""
    # Central logging config point for the CLI surface. Configures ONLY the
    # `cairn` logger — never root — because stdout must stay clean (it's the
    # JSON-RPC channel for the stdio MCP transport, and FastMCP pins its own
    # level to avoid clobbering root; see mcp_server/_server_core.py:75).
    #
    # Group-option placement note (click 8.x): `-v` must precede the
    # subcommand, e.g. `cairn -v build`. Placing it after the subcommand
    # (`cairn build -v`) is rejected by click as an unknown option of the
    # subcommand. For position-independent control, set CAIRN_LOG_LEVEL=DEBUG.
    # The group callback runs before any subcommand, so this fires once per
    # invocation and is idempotent (configure_logging attaches its handler at
    # most once).
    configure_logging(verbose=verbose)

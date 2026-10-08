"""cairn CLI main group and shared imports."""
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

# Wired once: the cli-metrics flusher's connection factory is injected on first
# dispatch, never at import, and resolves the store at call time so CAIRN_DB
# and cwd are read when flushing, not at boot.
_FLUSH_CONN_WIRED = False


def _wire_flusher_conn() -> None:
    """Inject the cli-metrics flusher's writable connection factory once; best-effort, never kills a command."""
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
    """Buffer one usage row via the cli-metrics builder; never raises, even on a broken telemetry module."""
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
    """Click command group that records invocation timing, status, and arguments."""

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
        # Captured before dispatch so the row exists on error paths;
        # invoked_subcommand is set while click resolves the subcommand, so
        # reading it at RECORD time works on success and error alike.
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
    # Configures only the `cairn` logger, never root: stdout is the stdio
    # JSON-RPC channel. `-v` must precede the subcommand; use
    # CAIRN_LOG_LEVEL=DEBUG for position-independent control.
    configure_logging(verbose=verbose)


if __name__ == "__main__":
    # `python -m cairn.cli.main` re-executes this module AFTER cairn.cli's
    # __init__ has registered every subcommand on the imported `main` group;
    # invoke that group, not the bare one re-defined in __main__.
    from cairn.cli.main import main as _registered_main

    _registered_main()

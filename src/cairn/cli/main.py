"""cairn CLI main group and shared imports.

The `cairn` command group lives here. Individual command modules under cli/
decorate their commands onto this `main` via @main.command() /
@<subgroup>.command(); the package __init__.py imports each module for the
side effect of registration.
"""
from __future__ import annotations

import click

from ..graph import builder  # noqa: F401  (re-exported for command modules)
from ..graph import queries  # noqa: F401
from ..graph import scanner as scanner_mod  # noqa: F401
from ..graph.schema import DEFAULT_DB_PATH, get_db  # noqa: F401
from ..paths import default_knowledge_path
from ..utils.logging import configure_logging

DEFAULT_KNOWLEDGE_PATH = str(default_knowledge_path())


@click.group()
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

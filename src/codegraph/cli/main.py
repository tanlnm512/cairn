"""cg CLI main group and shared imports.

The `cg` command group lives here. Individual command modules under cli/
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

DEFAULT_KNOWLEDGE_PATH = str(default_knowledge_path())


@click.group()
@click.version_option(
    version=__import__("codegraph").__version__,
    message="cg-intel %(version)s",
)
def main():
    """cg-intel: local codebase intelligence system."""

"""cairn CLI: cairn commands."""
from __future__ import annotations

# Re-export main for `from cairn.cli import main` (entry point in pyproject.toml
# is `cairn.cli:main`, and tests import it directly).
from .main import main
from .blast import blast

main.add_command(blast)

# Import every command module for its decorator side effects: each
# @main.command() / @<subgroup>.command() call registers the command on
# `main`. The imported names aren't used here -- registration is the point.
from . import agents       # noqa: F401
from . import ask_context  # noqa: F401
from . import bench        # noqa: F401
from . import compass      # noqa: F401
from . import core         # noqa: F401
from . import dashboard    # noqa: F401
from . import dataflow     # noqa: F401
from . import download_reranker  # noqa: F401
from . import embed        # noqa: F401
from . import hooks_viz    # noqa: F401
from . import grep         # noqa: F401
from . import knowledge    # noqa: F401
from . import map          # noqa: F401
from . import memory       # noqa: F401
from . import pack         # noqa: F401
from . import query        # noqa: F401
from . import review       # noqa: F401
from . import serve        # noqa: F401
from . import skill        # noqa: F401
from . import system       # noqa: F401
from . import taint        # noqa: F401
from . import task         # noqa: F401
from . import tree         # noqa: F401
from . import uninstall    # noqa: F401
from . import update       # noqa: F401
from . import upgrade      # noqa: F401
from . import validate     # noqa: F401
from . import wiki         # noqa: F401

__all__ = ["main"]

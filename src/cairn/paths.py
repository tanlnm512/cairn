"""Path discovery for cairn: central store keyed by workspace.

Resolution model (behaves like git/pre-commit — a global tool that finds its
data relative to where you run it):

  CAIRN_HOME (default ~/.cairn) holds one store per workspace, keyed
  by a short hash of the workspace root. Each store is a directory containing
  a `.kg` SQLite graph DB and a `.knowledge/` OKF bundle.

Workspace resolution order, tried at process start:
  1. CAIRN_WORKSPACE env var (absolute path) — highest priority
  2. Walk up from cwd looking for a registered ancestor (a path that maps to
     an existing store in the registry). This is how `cairn build` run from a
     subdirectory still finds the right graph.
  3. cwd itself — lowest priority. `cairn init` registers cwd.

All three also honor CAIRN_DB / CAIRN_KNOWLEDGE as hard overrides of
the resolved store path (used by the MCP server and tests).
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

# Central home for all per-workspace stores. Override for tests/CI.
CAIRN_HOME = Path(
    os.environ.get("CAIRN_HOME", str(Path.home() / ".cairn"))
)

REGISTRY_FILE = CAIRN_HOME / "workspaces.json"

# Shared library directory for the heavy semantic deps (torch,
# sentence-transformers, numpy). Installed via `cairn embed --install-deps`
# with `pip install --target` so they survive `uv tool install --force`
# reinstalls. Prepended to sys.path at import time below.
SHARED_LIB = CAIRN_HOME / "lib"


def shared_lib_path() -> Path:
    """The shared-deps directory (~/.cairn/lib by default)."""
    return Path(os.environ.get("CAIRN_LIB", str(SHARED_LIB)))


# Inject the shared lib dir into sys.path EARLY (at import time of this module,
# which every cairn entry point loads). This must happen before any code tries to
# `import sentence_transformers` / `import torch`. If the dir doesn't exist yet
# (deps not installed), this is a no-op.
_lib = shared_lib_path()
if _lib.is_dir():
    import sys as _sys
    _p = str(_lib)
    if _p not in _sys.path:
        _sys.path.insert(0, _p)


@dataclass(frozen=True)
class StorePaths:
    """Resolved locations for one workspace's cairn data."""

    workspace: Path
    home: Path          # <CAIRN_HOME>/<key>
    db: Path            # <home>/.kg
    knowledge: Path     # <home>/.knowledge

    def ensure(self) -> "StorePaths":
        """Create the store directories if missing. Idempotent."""
        self.home.mkdir(parents=True, exist_ok=True)
        self.knowledge.mkdir(parents=True, exist_ok=True)
        return self


def store_key(workspace: Path) -> str:
    """Stable short key for a workspace path."""
    return hashlib.sha256(str(workspace).encode()).hexdigest()[:16]


# --------------------------------------------------------------------------
# Registry: cwd -> key mapping, persisted as JSON for `cairn config --list`
#           and ancestor-walk lookup.
# --------------------------------------------------------------------------

def _load_registry() -> dict[str, str]:
    """Load the {workspace_abs_path: key} registry. Empty dict if absent/corrupt."""
    if not REGISTRY_FILE.exists():
        return {}
    try:
        data = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_registry(reg: dict[str, str]) -> None:
    CAIRN_HOME.mkdir(parents=True, exist_ok=True)
    REGISTRY_FILE.write_text(json.dumps(reg, indent=2, sort_keys=True), encoding="utf-8")


def register_workspace(workspace: Path) -> StorePaths:
    """Register a workspace (creates its store dir + registry entry)."""
    ws = workspace.resolve()
    key = store_key(ws)
    paths = StorePaths(
        workspace=ws,
        home=CAIRN_HOME / key,
        db=CAIRN_HOME / key / ".kg",
        knowledge=CAIRN_HOME / key / ".knowledge",
    ).ensure()
    reg = _load_registry()
    reg[str(ws)] = key
    _save_registry(reg)
    return paths


def is_registered(workspace: Path) -> bool:
    return str(workspace.resolve()) in _load_registry()


# --------------------------------------------------------------------------
# Workspace resolution
# --------------------------------------------------------------------------

def resolve_workspace(explicit: str | os.PathLike | None = None) -> Path:
    """Resolve which workspace cairn is operating on.

    Order: explicit arg > CAIRN_WORKSPACE env > registered ancestor > cwd.
    """
    if explicit:
        return Path(explicit).resolve()
    env_ws = os.environ.get("CAIRN_WORKSPACE")
    if env_ws:
        return Path(env_ws).resolve()
    # Walk up from cwd looking for a registered ancestor.
    reg = _load_registry()
    if reg:
        here = Path.cwd().resolve()
        for ancestor in [here, *here.parents]:
            if str(ancestor) in reg:
                return ancestor
    return Path.cwd().resolve()


def resolve_store(workspace: str | os.PathLike | None = None) -> StorePaths:
    """Resolve the full StorePaths for the current context.

    Honors CAIRN_DB / CAIRN_KNOWLEDGE as hard overrides (used by the
    MCP server, which sets them in its env). Otherwise derives from the
    resolved workspace's store; auto-registers cwd on first use so a fresh
    `cairn build` just works without an explicit `cairn init`.
    """
    ws = resolve_workspace(workspace)
    key = store_key(ws)
    home = CAIRN_HOME / key
    db = Path(os.environ.get("CAIRN_DB", str(home / ".kg")))
    knowledge = Path(os.environ.get("CAIRN_KNOWLEDGE", str(home / ".knowledge")))
    return StorePaths(workspace=ws, home=home, db=db, knowledge=knowledge)


# --------------------------------------------------------------------------
# Convenience for the schema/CLI: a db path resolved at import time of the
# calling process. CLI decorators read DEFAULT_DB_PATH once at import, so this
# reflects the cwd the user invoked `cairn` from.
# --------------------------------------------------------------------------

def default_db_path() -> Path:
    return resolve_store().db


def default_knowledge_path() -> Path:
    return resolve_store().knowledge


def default_workspace() -> str:
    return str(resolve_workspace())

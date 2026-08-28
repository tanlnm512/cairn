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
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

# Central home for all per-workspace stores. Override for tests/CI.
CAIRN_HOME = Path(
    os.environ.get("CAIRN_HOME", str(Path.home() / ".cairn"))
)

REGISTRY_FILE = CAIRN_HOME / "workspaces.json"

# Persistent settings file (FR-010): flat string->scalar JSON object whose
# keys mirror the CAIRN_EMBED_* env names. Bound at import time exactly like
# REGISTRY_FILE -- tests monkeypatch this attribute, not the env var.
CONFIG_FILE = CAIRN_HOME / "config.json"

# Shared library directory for the heavy semantic deps (torch,
# sentence-transformers, numpy). Installed via `cairn embed --install-deps`
# with `pip install --target` so they survive `uv tool install --force`
# reinstalls. Scoped per interpreter ABI (lib/cp311, lib/cp314, ...) because
# these packages ship ABI-specific wheels: a single flat dir corrupted
# silently once two interpreters installed into it (e.g. a 3.11 dev venv
# and a 3.14 pipx install), and pip's --target skip-if-satisfied semantics
# made the mixed dir unrepairable by re-running the install. Legacy
# pre-scope installs live directly in lib/ and stay usable via
# _inject_shared_libs.
SHARED_LIB = CAIRN_HOME / "lib"


# --------------------------------------------------------------------------
# CAIRN_HOME default ruling: the binding above is import-time, while these
# helpers re-read os.environ at call time (tests and long-lived processes may
# change the variable after import). Comparison is by expanded absolute path,
# so a CAIRN_HOME explicitly set to the default path counts as default. The
# binding's verbatim resolution behavior is unchanged.
# --------------------------------------------------------------------------

def _current_cairn_home() -> Path:
    """Effective CAIRN_HOME for the current environment: the env var's value
    (~-expanded, made absolute) when set, else ``~/.cairn``."""
    return Path(os.path.abspath(os.path.expanduser(
        os.environ.get("CAIRN_HOME", str(Path.home() / ".cairn"))
    )))


def cairn_home_is_default() -> bool:
    """Whether the effective CAIRN_HOME equals the default home (``~/.cairn``),
    compared as expanded absolute paths."""
    return _current_cairn_home() == Path.home() / ".cairn"


def cairn_home_env() -> dict[str, str]:
    """Env block selecting the current CAIRN_HOME for child processes:
    ``{}`` when the home is default, else ``{"CAIRN_HOME": <expanded path>}``."""
    if cairn_home_is_default():
        return {}
    return {"CAIRN_HOME": str(_current_cairn_home())}


def render_env_resolution_chain() -> str:
    """One-line rendering of the env resolution chain in effect (FR-004):
    the CAIRN_HOME / CAIRN_WORKSPACE / CAIRN_DB / CAIRN_KNOWLEDGE values
    (or ``unset``) plus the db path that chain resolves to, per the
    resolve_workspace / resolve_store order. Reads the environment at call
    time like the helpers above and is read-only (no registry writes), so
    it is safe inside error paths.
    """
    def _entry(name: str) -> str:
        value = os.environ.get(name)
        return f"{name}={value}" if value else f"{name}=unset"

    entries = " ".join(
        _entry(name)
        for name in ("CAIRN_HOME", "CAIRN_WORKSPACE", "CAIRN_DB",
                     "CAIRN_KNOWLEDGE")
    )
    return f"{entries}; resolved db: {resolve_store().db}"


def _abi_tag() -> str:
    import sys as _sys

    return f"cp{_sys.version_info[0]}{_sys.version_info[1]}"


def shared_lib_path() -> Path:
    """The shared-deps directory for the RUNNING interpreter's ABI.

    Default: ``<CAIRN_HOME>/lib/cp<major><minor>``. ``CAIRN_LIB`` overrides
    verbatim (no ABI suffix) for tests and explicit user pinning.
    """
    override = os.environ.get("CAIRN_LIB")
    if override:
        return Path(override)
    return SHARED_LIB / _abi_tag()


def _inject_shared_libs() -> None:
    """Prepend the shared lib dirs to sys.path (idempotent, existing dirs only).

    Order (first match wins): the ABI-scoped dir, then the legacy flat
    ``<CAIRN_HOME>/lib`` from pre-scope installs -- an existing
    single-interpreter install keeps working unchanged, while every package
    present in the ABI dir shadows its legacy copy. With ``CAIRN_LIB`` set,
    only that directory is injected (the override is explicit and exact).
    """
    import sys as _sys

    if os.environ.get("CAIRN_LIB"):
        dirs = [shared_lib_path()]
    else:
        dirs = [shared_lib_path(), SHARED_LIB]
    # reversed so the first dir in `dirs` ends up FIRST on sys.path.
    for d in reversed(dirs):
        if d.is_dir() and str(d) not in _sys.path:
            _sys.path.insert(0, str(d))


# Inject the shared lib dirs into sys.path EARLY (at import time of this
# module, which every cairn entry point loads). This must happen before any
# code tries to `import sentence_transformers` / `import torch`. If the dirs
# don't exist yet (deps not installed), this is a no-op.
_inject_shared_libs()


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
# Persistent config: $CAIRN_HOME/config.json. Flat JSON object whose keys
# mirror the CAIRN_EMBED_* env names (e.g. "CAIRN_EMBED_BACKEND": "omlx")
# and whose values are scalars. Consumers resolve env > file > default
# (D-008); this layer only reads/writes/caches the file side.
# --------------------------------------------------------------------------

_CONFIG_CACHE: dict = {"stamp": None, "data": {}}

_logger = logging.getLogger(__name__)


def reset_config_cache() -> None:
    """Drop the cached config so the next get_config_value re-reads disk."""
    _CONFIG_CACHE["stamp"] = None
    _CONFIG_CACHE["data"] = {}


def _load_config() -> dict:
    """One uncached read of CONFIG_FILE.

    Returns the flat scalar mapping; empty dict when the file is absent,
    unreadable, corrupt (one warning on invalid JSON), or not an object
    (one warning). Non-scalar values are dropped with one summary warning
    naming the keys. Never raises.
    """
    if not CONFIG_FILE.exists():
        return {}
    try:
        raw = CONFIG_FILE.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        _logger.warning(
            "%s is not valid JSON; ignoring it (env vars still apply)",
            CONFIG_FILE,
        )
        return {}
    if not isinstance(data, dict):
        _logger.warning(
            "%s is not a JSON object; ignoring it (env vars still apply)",
            CONFIG_FILE,
        )
        return {}
    values = {
        k: v for k, v in data.items() if isinstance(v, (str, int, float, bool))
    }
    dropped = sorted(set(data) - set(values))
    if dropped:
        # Names only, never values: dropped entries can carry secrets
        # (e.g. a mistakenly nested API key) and must not reach the log.
        _logger.warning(
            "%s: ignoring non-scalar keys: %s (values must be strings, "
            "numbers, or booleans)",
            CONFIG_FILE,
            ", ".join(dropped),
        )
    return values


def get_config_value(key: str, default=None):
    """Value for ``key`` from CONFIG_FILE, or ``default`` when unset.

    Re-reads the file only when its (mtime_ns, size) stamp changed, so a
    running process picks up edits without restart. Never raises.
    """
    try:
        st = CONFIG_FILE.stat()
        stamp = (st.st_mtime_ns, st.st_size)
    except OSError:
        stamp = None
    if _CONFIG_CACHE["stamp"] != stamp:
        _CONFIG_CACHE["stamp"] = stamp
        _CONFIG_CACHE["data"] = _load_config() if stamp is not None else {}
    return _CONFIG_CACHE["data"].get(key, default)


def set_config_values(values: dict) -> bool:
    """Merge ``values`` into CONFIG_FILE atomically (tmp file + os.replace).

    Creates the parent directory. Returns True on success; on OSError logs
    one warning, leaves the previous file intact with no tmp file left
    behind, and returns False.
    """
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        merged = {**_load_config(), **values}
        fd, tmp = tempfile.mkstemp(
            dir=str(CONFIG_FILE.parent), prefix=".config-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(merged, fh, indent=2, sort_keys=True)
                fh.write("\n")
                # flush+fsync BEFORE the replace: os.replace is atomic within
                # the filesystem but not durable -- without fsync a crash can
                # persist the directory entry while the tmp file's data blocks
                # were never written, leaving a zero-length config on recovery.
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, CONFIG_FILE)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError:
        _logger.warning(
            "could not write %s; configuration not saved", CONFIG_FILE
        )
        return False
    reset_config_cache()
    return True


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

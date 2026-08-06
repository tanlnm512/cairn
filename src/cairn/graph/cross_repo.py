"""Cross-repo dependency analysis via import namespace mapping.

The namespace map maps import-path prefixes to owning repo ids. It is resolved
per process by :func:`_load_namespaces` in priority order: the
``CAIRN_REPO_NAMESPACES`` env var (JSON), then the ``repo_namespaces`` key
of the workspace's ``cairn.json``, then the built-in :data:`_DEFAULT_NAMESPACES`
fallback. ``cross_repo_deps`` uses the resolved map plus the ``imports`` table
to compute which repos a given repo depends on, and which depend on it.

Both the loader and ``cross_repo_deps`` are imported by name from
``queries.py`` (the backward-compat shim) and from ``src.graph`` (the public
API) by ``viz/query.py`` and ``wiki/generator.py``.
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Dict


# Built-in fallback: per the spec's be-workspace conventions. Used when neither
# the env var nor cairn.json supplies a namespace map. Kept so existing
# be-workspace behavior is preserved silently.
_DEFAULT_NAMESPACES: Dict[str, str] = {
    "xyz.be.utils": "be-sdk",
    "xyz.be.customer.networking": "be-sdk",
    "xyz.be.customer.common": "be-sdk",
    "xyz.be.partner.common": "be-sdk",
    "xyz.be.common": "be-sdk",  # dual-namespace: customer/partner common
    "xyz.be.networking": "be-sdk",
    "xyz.be.coreui": "be-core-ui",
    "xyz.be.newcoreui": "be-core-ui",
    "xyz.be.core_ui_v4": "be-core-ui",
    "xyz.be.core.ui": "be-core-ui",
}

# Backward-compat alias: historical callers imported ``REPO_NAMESPACES``. It is
# the *default* map (not the resolved one) — kept as a stable reference for
# import sites that never call it (e.g. ``viz/query.py``).
REPO_NAMESPACES = _DEFAULT_NAMESPACES


def _escape_like(value: str) -> str:
    """Escape LIKE meta-characters so ``value`` matches literally.

    ``\\``, ``%`` and ``_`` are escaped by prefixing a backslash; the
    accompanying LIKE clause must use ``ESCAPE '\\'``. Namespace prefixes
    (e.g. ``xyz.be.core_ui_v4``) contain ``_`` which would otherwise act as a
    single-char wildcard and match unintended import paths.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

# Process-level cache of the resolved map, keyed by the *resolved workspace
# path* so switching workspaces within a long-lived process (e.g. the MCP
# server) does not serve a stale map from the previous workspace. Cleared by
# ``_reset_namespaces_cache`` (tests / config reload).
_namespaces_cache: Dict[str, Dict[str, str]] = {}


def _load_namespaces() -> Dict[str, str]:
    """Resolve the cross-repo namespace map for the current context.

    Priority (first non-empty wins):
      1. ``CAIRN_REPO_NAMESPACES`` env var — a JSON object of prefix->repo.
      2. ``repo_namespaces`` in the workspace's ``cairn.json``.
      3. :data:`_DEFAULT_NAMESPACES` (the built-in be-workspace map).

    The result is cached for the process, **keyed by the resolved workspace
    path**, so a long-lived process that changes workspaces (the MCP server
    re-pointing ``CAIRN_WORKSPACE``) re-resolves instead of reusing the prior
    workspace's map. A malformed env var or config file is ignored with a
    stderr warning, never raised — a bad config must not break the build
    (same contract as :mod:`graph.config`).
    """
    import sys

    from ..paths import resolve_workspace

    ws_key = str(resolve_workspace())
    cached = _namespaces_cache.get(ws_key)
    if cached is not None:
        return cached

    # 1. Env override.
    env_raw = os.environ.get("CAIRN_REPO_NAMESPACES")
    if env_raw:
        try:
            parsed = json.loads(env_raw)
        except json.JSONDecodeError as e:
            print(f"warning: CAIRN_REPO_NAMESPACES: invalid JSON ({e}); "
                  f"ignoring", file=sys.stderr)
            parsed = None
        if isinstance(parsed, dict):
            clean = {str(k): str(v) for k, v in parsed.items()
                     if isinstance(k, str) and isinstance(v, str)
                     and k.strip() and v.strip()}
            if clean:
                _namespaces_cache[ws_key] = clean
                return clean
        elif parsed is not None:
            print("warning: CAIRN_REPO_NAMESPACES must be a JSON object; "
                  "ignoring", file=sys.stderr)

    # 2. cairn.json at the resolved workspace root.
    try:
        from .config import load_config

        cfg = load_config(resolve_workspace())
        if cfg.repo_namespaces:
            resolved = dict(cfg.repo_namespaces)
            _namespaces_cache[ws_key] = resolved
            return resolved
    except Exception as e:  # pragma: no cover - defensive; never break the build
        print(f"warning: could not load repo_namespaces from config ({e}); "
              f"using defaults", file=sys.stderr)

    # 3. Built-in default.
    resolved = dict(_DEFAULT_NAMESPACES)
    _namespaces_cache[ws_key] = resolved
    return resolved


def _reset_namespaces_cache() -> None:
    """Clear the process cache (tests / config reload)."""
    global _namespaces_cache
    _namespaces_cache = {}


def cross_repo_deps(conn: sqlite3.Connection, repo: str) -> dict:
    """Compute cross-repo dependencies for `repo` via import namespaces.

    Returns {dependencies: [{repo, type, evidence, count}],
             dependents:   [{repo, type, count}]}.
    """
    namespaces = _load_namespaces()
    cur = conn.cursor()
    # Dependencies: imports in `repo` that resolve to another repo's namespace.
    deps: dict[str, dict] = {}
    for row in cur.execute(
        "SELECT imported_path FROM imports WHERE file_id IN "
        "(SELECT id FROM files WHERE repo_id = ?)",
        (repo,),
    ).fetchall():
        path = row["imported_path"]
        for ns, owner in namespaces.items():
            if path.startswith(ns) and owner != repo:
                d = deps.setdefault(owner, {"repo": owner, "type": "import", "evidence": ns, "count": 0})
                d["count"] += 1

    # Dependents: imports in OTHER repos referencing `repo`'s namespaces.
    # The prefix filter is pushed into SQL (``imported_path LIKE ns || '%'``)
    # rather than loading every import row into memory and filtering in
    # Python — the earlier Python-side loop scaled with the total number of
    # import rows across all repos. Namespace prefixes may contain LIKE
    # meta-characters (``_``), so they are escaped and the LIKE uses
    # ``ESCAPE '\\'``.
    my_namespaces = [ns for ns, owner in namespaces.items() if owner == repo]
    dependents: dict[str, dict] = {}
    if my_namespaces:
        escaped_ns = [_escape_like(ns) for ns in my_namespaces]
        # Each namespace contributes ``imported_path LIKE ? || '%' ESCAPE '\'``;
        # ESCAPE is a per-LIKE modifier, so it must be repeated on every term.
        where_clause = " OR ".join(
            "imported_path LIKE ? || '%' ESCAPE '\\'" for _ in escaped_ns
        )
        params = [repo, *escaped_ns]
        for row in cur.execute(
            f"""SELECT imported_path, repo_id
                FROM imports JOIN files ON imports.file_id = files.id
                WHERE files.repo_id != ? AND ({where_clause})""",
            params,
        ).fetchall():
            d = dependents.setdefault(
                row["repo_id"],
                {"repo": row["repo_id"], "type": "import", "count": 0},
            )
            d["count"] += 1

    return {
        "dependencies": sorted(deps.values(), key=lambda x: -x["count"]),
        "dependents": sorted(dependents.values(), key=lambda x: -x["count"]),
    }

"""Cross-repo dependency analysis via import namespace mapping."""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Dict


# Built-in fallback mapping be-workspace import namespaces to repo names.
# Used when neither the env var nor cairn.json supplies a namespace map.
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
    """Escape LIKE meta-characters so ``value`` matches literally."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

# Namespace cache keys use the resolved workspace path.
_namespaces_cache: Dict[str, Dict[str, str]] = {}


def _load_namespaces() -> Dict[str, str]:
    """Resolve the cross-repo namespace map for the current context."""
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
    """Compute cross-repo dependencies for `repo` via import namespaces."""
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
            # Dot boundary: only the exact namespace or a segment extension
            # counts, so "xyz.be.common_extra" never matches "xyz.be.common".
            if (path == ns or path.startswith(ns + ".")) and owner != repo:
                d = deps.setdefault(owner, {"repo": owner, "type": "import", "evidence": ns, "count": 0})
                d["count"] += 1

    # Dependent namespace matching stays in SQL and escapes LIKE metacharacters.
    my_namespaces = [ns for ns, owner in namespaces.items() if owner == repo]
    dependents: dict[str, dict] = {}
    if my_namespaces:
        escaped_ns = [_escape_like(ns) for ns in my_namespaces]
        # Each namespace contributes an exact match plus a dot-boundary
        # extension (``ns`` / ``ns || '.%'``); ESCAPE is a per-LIKE modifier,
        # so it is repeated on every term.
        where_clause = " OR ".join(
            "(imported_path LIKE ? ESCAPE '\\' "
            "OR imported_path LIKE ? || '.%' ESCAPE '\\')"
            for _ in escaped_ns
        )
        params = [repo]
        for ns in escaped_ns:
            params.extend([ns, ns])
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

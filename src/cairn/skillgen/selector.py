"""Resolve a skill selector to candidate symbols via read-only graph queries."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..compass.generator import (
    ModuleResolutionError,
    _resolve_module,
    _symbols_in_module,
)


@dataclass
class SelectorResolution:
    """Resolved selector: qualified symbol names plus tokens that matched nothing."""

    candidates: list[str]
    unmatched: list[str]


def resolve_selector(conn: sqlite3.Connection, selector: str) -> SelectorResolution:
    """Resolve each whitespace-separated selector token against the graph.

    Read-only: issues SELECTs only. Never raises for unresolvable input —
    unmatched tokens are reported in ``unmatched``.
    """
    candidates: list[str] = []
    unmatched: list[str] = []
    for token in selector.split():
        resolved = _resolve_token(conn, token)
        if resolved is None:
            unmatched.append(token)
        else:
            candidates.extend(resolved)
    return SelectorResolution(
        candidates=sorted(set(candidates)),
        unmatched=list(dict.fromkeys(unmatched)),
    )


def _resolve_token(conn: sqlite3.Connection, token: str) -> list[str] | None:
    """Qualified names for one token; None when nothing matches."""
    exact = _exact_symbol_names(conn, token)
    if exact:
        return exact
    under = _symbols_under_path(conn, token)
    return under or None


def _exact_symbol_names(conn: sqlite3.Connection, token: str) -> list[str]:
    """Symbols whose name or qualified_name equals the token, exactly.

    Module-kind rows are excluded: they are packaging artifacts, so a
    token naming a module falls through to module-path resolution.
    """
    rows = conn.execute(
        "SELECT qualified_name, name FROM symbols "
        "WHERE (name = ? OR qualified_name = ?) AND kind != 'module'",
        (token, token),
    ).fetchall()
    return sorted({(r["qualified_name"] or r["name"]) for r in rows})


def _symbols_under_path(conn: sqlite3.Connection, token: str) -> list[str]:
    """Symbols whose file lives under the token's module path."""
    try:
        repo, module_path = _resolve_module(conn, token, None)
    except ModuleResolutionError:
        return []
    rows = _symbols_in_module(conn, module_path, repo)
    return sorted({(r["qualified_name"] or r["name"]) for r in rows})

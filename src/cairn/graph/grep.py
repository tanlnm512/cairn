"""Span-grouped regular-expression search over indexed files."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .scanner import resolve_file_path


DEFAULT_MAX_HITS = 256


@dataclass(frozen=True)
class _SymbolSpan:
    id: str
    qualified_name: str | None
    kind: str
    line_start: int
    line_end: int
    incoming: int


@dataclass(frozen=True)
class _Group:
    repo: str
    path: str
    symbol: _SymbolSpan | None
    hits: list[dict]

    def first_line(self) -> int:
        return self.hits[0]["line"]


def graph_grep(
    conn: sqlite3.Connection,
    workspace: str,
    pattern: str,
    *,
    ignore_case: bool = False,
    fixed: bool = False,
    path_prefix: str = "",
    max_hits: int = DEFAULT_MAX_HITS,
) -> dict:
    """Search indexed files and group hits by their innermost stored symbol."""
    if max_hits < 1:
        raise ValueError("max_hits must be at least 1")
    expression = re.escape(pattern) if fixed else pattern
    flags = re.IGNORECASE if ignore_case else 0
    try:
        matcher = re.compile(expression, flags)
    except re.error as exc:
        raise ValueError(f"invalid pattern: {exc}") from exc

    incoming = _incoming_counts(conn)
    file_rows = conn.execute(
        "SELECT id, repo_id, path FROM files ORDER BY repo_id, path"
    ).fetchall()
    prefix = _normalized_prefix(path_prefix)
    symbols_by_file = _symbols_by_file(conn, incoming)

    searched = 0
    unreadable = 0
    groups: dict[tuple[str, str, str], _Group] = {}
    for row in file_rows:
        path = row["path"]
        if not _under_prefix(path, prefix):
            continue
        source_path = Path(
            resolve_file_path(workspace, row["repo_id"], path)
        )
        try:
            source = source_path.read_bytes().decode("utf-8", errors="replace")
        except OSError:
            unreadable += 1
            continue
        searched += 1
        _collect_file_hits(
            matcher,
            source.splitlines(),
            str(row["id"]),
            row["repo_id"],
            path,
            symbols_by_file.get(str(row["id"]), []),
            groups,
        )

    ordered = sorted(
        groups.values(),
        key=lambda group: (
            -_incoming(group),
            group.path,
            _qualified_name(group),
            group.first_line(),
        ),
    )
    capped: list[_Group] = []
    returned_hits = 0
    for group in ordered:
        if returned_hits >= max_hits:
            break
        allowed = group.hits[: max_hits - returned_hits]
        if allowed:
            capped.append(
                _Group(
                    repo=group.repo,
                    path=group.path,
                    symbol=group.symbol,
                    hits=allowed,
                )
            )
            returned_hits += len(allowed)

    total_hits = sum(len(group.hits) for group in ordered)
    return {
        "searched_files": searched,
        "unreadable_files": unreadable,
        "groups": [_payload_group(group) for group in capped],
        "dropped_hits": total_hits - returned_hits,
        "dropped_groups": len(ordered) - len(capped),
    }


def _incoming_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        row["target_id"]: row["incoming"]
        for row in conn.execute(
            """
            SELECT target_id, COUNT(*) AS incoming
            FROM edges
            WHERE target_id IS NOT NULL
            GROUP BY target_id
            """
        ).fetchall()
    }


def _symbols_by_file(
    conn: sqlite3.Connection, incoming: dict[str, int]
) -> dict[str, list[_SymbolSpan]]:
    result: dict[str, list[_SymbolSpan]] = {}
    rows = conn.execute(
        """
        SELECT id, file_id, qualified_name, kind, line_start, line_end
        FROM symbols
        WHERE kind != 'module'
        ORDER BY file_id, line_start, line_end, qualified_name, id
        """
    ).fetchall()
    for row in rows:
        result.setdefault(str(row["file_id"]), []).append(
            _SymbolSpan(
                id=str(row["id"]),
                qualified_name=row["qualified_name"] or None,
                kind=row["kind"],
                line_start=row["line_start"] or 1,
                line_end=row["line_end"] or 1,
                incoming=incoming.get(str(row["id"]), 0),
            )
        )
    return result


def _normalized_prefix(prefix: str) -> str:
    return "/".join(part for part in prefix.replace("\\", "/").split("/") if part)


def _under_prefix(path: str, prefix: str) -> bool:
    return not prefix or path == prefix or path.startswith(f"{prefix}/")


def _collect_file_hits(
    matcher: re.Pattern[str],
    lines: list[str],
    file_id: str,
    repo: str,
    path: str,
    symbols: list[_SymbolSpan],
    groups: dict[tuple[str, str, str], _Group],
) -> None:
    for line_number, text in enumerate(lines, start=1):
        for match in matcher.finditer(text):
            symbol = _innermost_symbol(symbols, line_number)
            symbol_name = symbol.qualified_name if symbol is not None else ""
            key = (
                repo,
                path,
                symbol_name or "",
            )
            group = groups.get(key)
            if group is None:
                group = _Group(repo, path, symbol, [])
                groups[key] = group
            group.hits.append(
                {
                    "line": line_number,
                    "column": match.start() + 1,
                    "text": text,
                }
            )


def _innermost_symbol(
    symbols: list[_SymbolSpan], line_number: int
) -> _SymbolSpan | None:
    containing = [
        symbol
        for symbol in symbols
        if symbol.line_start <= line_number <= symbol.line_end
    ]
    if not containing:
        return None
    return min(
        containing,
        key=lambda symbol: (
            symbol.line_end - symbol.line_start,
            symbol.line_start,
            symbol.qualified_name or "",
            symbol.id,
        ),
    )


def _incoming(group: _Group) -> int:
    return group.symbol.incoming if group.symbol else 0


def _qualified_name(group: _Group) -> str:
    if group.symbol is None:
        return ""
    return group.symbol.qualified_name or ""


def _payload_group(group: _Group) -> dict:
    symbol = group.symbol
    return {
        "repo": group.repo,
        "path": group.path,
        "qualified_name": symbol.qualified_name if symbol else None,
        "kind": symbol.kind if symbol else "file",
        "incoming": _incoming(group),
        "hits": group.hits,
    }

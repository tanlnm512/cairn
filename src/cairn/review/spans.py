"""Map a review comment's file+line to enclosing symbols via stored spans.

The comment location is resolved against ``symbols.line_start``/``line_end``
(``src/cairn/graph/schema.py``). When no span encloses the line — or the
comment carries no line, or its file is not indexed — the match degrades to
file-level keying: ``file_level`` is true and ``symbols`` is empty, so
callers key on ``file_path`` instead.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

__all__ = ["CommentSymbols", "SymbolSpan", "resolve_comment_symbols"]


@dataclass(frozen=True)
class SymbolSpan:
    """One enclosing symbol; ``line_start``/``line_end`` are inclusive."""

    name: str
    qualified_name: str | None
    kind: str
    line_start: int
    line_end: int


@dataclass(frozen=True)
class CommentSymbols:
    """Span-match result for one comment location.

    ``symbols`` is ordered innermost span first. Empty ``symbols`` means
    file-level keying; ``file_path`` is always the normalized input path.
    """

    file_path: str
    symbols: tuple[SymbolSpan, ...]

    @property
    def file_level(self) -> bool:
        return not self.symbols


def _normalize_path(file_path: str) -> str:
    return file_path.replace("\\", "/").removeprefix("./")


def resolve_comment_symbols(
    conn: sqlite3.Connection,
    file_path: str,
    line: int | None,
) -> CommentSymbols:
    """Resolve a comment location to the symbols whose spans enclose ``line``.

    ``line`` may be ``None`` for file-level comments. Raises ``ValueError``
    for an empty path or a non-positive/non-integer line. Span bounds are
    inclusive; symbols with NULL spans never match.
    """
    if not isinstance(file_path, str) or not file_path.strip():
        raise ValueError("file_path must be a non-empty string")
    if line is not None and (
        not isinstance(line, int) or isinstance(line, bool) or line < 1
    ):
        raise ValueError(f"line must be a positive integer or None, got {line!r}")

    path = _normalize_path(file_path)
    if line is None:
        return CommentSymbols(file_path=path, symbols=())

    rows = conn.execute(
        "SELECT s.name, s.qualified_name, s.kind, s.line_start, s.line_end "
        "FROM symbols s JOIN files f ON f.id = s.file_id "
        "WHERE f.path = ? "
        "AND s.line_start IS NOT NULL AND s.line_end IS NOT NULL "
        "AND s.line_start <= ? AND s.line_end >= ? "
        "ORDER BY (s.line_end - s.line_start), s.name",
        (path, line, line),
    ).fetchall()
    symbols = tuple(
        SymbolSpan(
            name=row["name"],
            qualified_name=row["qualified_name"],
            kind=row["kind"],
            line_start=row["line_start"],
            line_end=row["line_end"],
        )
        for row in rows
    )
    return CommentSymbols(file_path=path, symbols=symbols)

#!/usr/bin/env python3
"""Canonical per-table export of a .kg store for cross-build content diffing (P0-3)."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

# Stdlib-only by design: this is the parity arbiter and must not depend on
# either implementation.

FORMAT_LINE = "# cairn-canonical-dump 1"

# --- dump format -------------------------------------------------------------
# Line-oriented; a pure function of store content plus --map-path, so two dumps
# of content-equal stores are byte-identical.
#   FORMAT_LINE
#   # map-path: <from> => <to>          (only when mappings are active, sorted)
#   [table <name>]
#   # columns: <c1>\t<c2>...
#   <cell>\t<cell>...                   (sorted by escaped-cell tuple)
# Cells: NULL -> \N ; float -> C99 hex (float.hex()); blob -> x'<hex>' ;
# text/int -> text, escaped (\ -> \\, and \n \r \t \0 -> \\n \\r \\t \\0).
# Virtual tables (fts5, vec0), their shadow tables, and sqlite_* internals are
# skipped: their bytes are derivable from the exported source/content tables.


def classify_tables(conn: sqlite3.Connection) -> dict[str, str]:
    """Map table name -> class (content|fts5|vec0|shadow|sqlite-internal)."""
    rows = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    virtual: list[str] = []
    classes: dict[str, str] = {}
    for name, sql in rows:
        if name.startswith("sqlite_"):
            classes[name] = "sqlite-internal"
        elif sql and "using fts5" in sql.lower():
            classes[name] = "fts5"
            virtual.append(name)
        elif sql and "using vec0" in sql.lower():
            classes[name] = "vec0"
            virtual.append(name)
        else:
            classes[name] = "content"
    # Shadow tables are named "<virtual_table>_<suffix>" by both fts5 and vec0.
    for name in classes:
        if classes[name] != "content":
            continue
        if any(name == v or name.startswith(v + "_") for v in virtual):
            classes[name] = "shadow"
    return classes


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
        .replace("\0", "\\0")
    )


def _norm_cell(value, map_paths: list[tuple[str, str]]) -> str:
    if value is None:
        return "\\N"
    if isinstance(value, float):
        return _escape(value.hex())
    if isinstance(value, int):
        return _escape(str(value))
    if isinstance(value, bytes):
        return "x'" + value.hex() + "'"
    for src, dst in map_paths:
        if src and src in value:
            value = value.replace(src, dst)
    return _escape(value)


def dump_table_rows(conn: sqlite3.Connection, table: str, map_paths: list[tuple[str, str]]) -> list[str]:
    """Render the table's rows as sorted, escaped, tab-joined lines."""
    cols = [row[1] for row in conn.execute(f"PRAGMA table_info({_quote_ident(table)})")]
    collist = ", ".join(_quote_ident(c) for c in cols)
    rows: list[list[str]] = []
    for raw in conn.execute(f"SELECT {collist} FROM {_quote_ident(table)}"):
        cells = [_norm_cell(v, map_paths) for v in raw]
        line = "\t".join(cells)
        # A row must never be mistakable for a "[table <name>]" section header.
        if line.startswith("[table ") and line.endswith("]"):
            cells[0] = "\\" + cells[0]
            line = "\t".join(cells)
        rows.append(cells)
    rows.sort()
    return ["\t".join(cells) for cells in rows]


def dump_store(db_path: Path, map_paths: list[tuple[str, str]], only: set[str] | None) -> tuple[dict[str, str], list[str]]:
    """Return (table classification, dump lines)."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        classes = classify_tables(conn)
        lines = [FORMAT_LINE]
        for src, dst in sorted(map_paths):
            lines.append(f"# map-path: {src} => {dst}")
        for table in sorted(classes):
            if classes[table] != "content":
                continue
            if only is not None and table not in only:
                continue
            cols = [row[1] for row in conn.execute(f"PRAGMA table_info({_quote_ident(table)})")]
            lines.append(f"[table {table}]")
            lines.append("# columns: " + "\t".join(_escape(c) for c in cols))
            lines.extend(dump_table_rows(conn, table, map_paths))
        report = dict(classes)
        return report, lines
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db", type=Path, help="Path to the .kg SQLite store.")
    parser.add_argument("-o", "--out", type=Path, default=None, help="Dump path (default: stdout).")
    parser.add_argument(
        "--map-path",
        action="append",
        default=[],
        metavar="FROM=TO",
        help="Rewrite FROM to TO inside text cells; repeatable, recorded in the dump header.",
    )
    parser.add_argument("--tables", default=None, help="Comma-separated content tables to export (default: all).")
    parser.add_argument(
        "--list-tables",
        action="store_true",
        help="Print the table classification (name<TAB>class) to stdout and exit.",
    )
    args = parser.parse_args(argv)
    if not args.db.is_file():
        parser.error(f"store not found: {args.db}")

    map_paths: list[tuple[str, str]] = []
    for spec in args.map_path:
        if "=" not in spec:
            parser.error(f"--map-path needs FROM=TO, got: {spec}")
        src, dst = spec.split("=", 1)
        map_paths.append((src, dst))

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    try:
        classes = classify_tables(conn)
    finally:
        conn.close()

    if args.list_tables:
        print("\n".join(f"{name}\t{cls}" for name, cls in sorted(classes.items())))
        return 0

    only = {t.strip() for t in args.tables.split(",")} if args.tables else None
    unknown = sorted((only or set()) - {t for t, c in classes.items() if c == "content"})
    if unknown:
        parser.error(f"not content tables: {', '.join(unknown)}")

    _, lines = dump_store(args.db, map_paths, only)
    text = "\n".join(lines) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out} ({len(lines)} lines)", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Build the checked-in corner-case store for the Rust canonical-dump parity test."""

from __future__ import annotations

import argparse
import math
import sqlite3
import struct
import sys
from pathlib import Path


def _load_vec0(conn: sqlite3.Connection) -> None:
    import sqlite_vec

    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)


def build(db_path: Path) -> None:
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(str(db_path))
    try:
        _load_vec0(conn)
        conn.executescript(
            """
            CREATE TABLE cells (
                id INTEGER PRIMARY KEY,
                n INTEGER,
                r REAL,
                t TEXT,
                b BLOB
            );
            INSERT INTO cells VALUES
                (1, 0, 0.0, 'zero-int zero-real', x''),
                (2, -7, -0.0, 'tab\there\nnewline\rcr\\backslash', x'deadbeef00'),
                (3, 9223372036854775807, 0.1, '/Users/x/repo/src/main.rs', x'00ff10'),
                (4, NULL, 1e308, 'unicode é中 text', NULL),
                (5, 42, 5e-324, 'inf-dot-and-nul: end', x'0a0d09005c');
            UPDATE cells SET t = CAST((t || char(0)) AS TEXT) WHERE id = 5;

            CREATE TABLE tricky (a TEXT);
            INSERT INTO tricky VALUES ('[table z]'), ('plain');

            CREATE TABLE empty_t (x TEXT, y INTEGER);

            CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, msg TEXT);
            INSERT INTO events (msg) VALUES ('first');

            CREATE VIRTUAL TABLE symbols_fts USING fts5(
                name, qualified_name, docstring,
                content='cells', content_rowid='id'
            );
            INSERT INTO symbols_fts(rowid, name) VALUES (1, 'zero');

            CREATE VIRTUAL TABLE vec_corner USING vec0(
                embedding float[4] distance_metric=cosine
            );
            """
        )
        conn.executemany(
            "INSERT INTO cells (id, n, r, t, b) VALUES (?, ?, ?, ?, ?)",
            [(6, None, -math.inf, None, b"\x01"), (7, None, math.inf, None, None)],
        )
        # Double quotes only: safe next to the triple-quoted block above.
        conn.execute(
            'CREATE TABLE "weird ""name""" ("a b" TEXT, "c""d" INTEGER)'
        )
        conn.execute('INSERT INTO "weird ""name""" VALUES (?, ?)', ("quoted", 3))
        conn.executemany(
            "INSERT INTO vec_corner(rowid, embedding) VALUES (?, ?)",
            [(1, struct.pack("<4f", 1.0, 0.0, 0.0, 0.0)),
             (2, struct.pack("<4f", 0.0, 1.0, 0.0, 0.0))],
        )
        # Fixture self-check: NUL text must survive the driver round trip.
        back = conn.execute("SELECT t FROM cells WHERE id = 5").fetchone()[0]
        expected = "inf-dot-and-nul: end" + chr(0)
        assert back == expected, f"NUL text did not survive the round trip: {back!r}"
        conn.commit()
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db", type=Path, help="Path of the .kg fixture to write.")
    args = parser.parse_args(argv)
    build(args.db)
    print(f"wrote {args.db}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

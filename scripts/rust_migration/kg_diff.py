#!/usr/bin/env python3
"""Diff two kg_dump.py canonical dumps: exit 0 on equality, 1 with a per-table report (P0-3)."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Dump:
    path: Path
    map_paths: list[str] = field(default_factory=list)
    tables: dict[str, tuple[str, list[str]]] = field(default_factory=dict)  # name -> (columns line, row lines)


def parse_dump(path: Path) -> Dump:
    """Parse a canonical dump; raises ValueError on malformed or unsorted input."""
    dump = Dump(path=path)
    table: str | None = None
    columns: str = ""
    rows: list[str] = []
    header_done = False

    def _flush() -> None:
        if table is not None:
            dump.tables[table] = (columns, rows)

    with path.open(encoding="utf-8") as handle:
        for lineno, raw in enumerate(handle, 1):
            line = raw.rstrip("\n")
            if not header_done:
                if lineno == 1:
                    if line != "# cairn-canonical-dump 1":
                        raise ValueError(f"{path}: not a canonical dump (first line: {line!r})")
                    continue
                if line.startswith("# map-path: "):
                    dump.map_paths.append(line)
                    continue
                if line == "":
                    continue
                header_done = True
            if line.startswith("[table ") and line.endswith("]"):
                _flush()
                table = line[len("[table ") : -1]
                columns = ""
                rows = []
            elif line.startswith("# columns: "):
                if table is None:
                    raise ValueError(f"{path}:{lineno}: columns line outside a table section")
                columns = line[len("# columns: ") :]
            elif line.startswith("#"):
                continue
            elif line:
                if table is None or not columns:
                    raise ValueError(f"{path}:{lineno}: row outside a table section")
                if rows and rows[-1] > line:
                    raise ValueError(f"{path}:{lineno}: rows not sorted (dump is not canonical)")
                rows.append(line)
    _flush()
    return dump


def diff_dumps(a: Dump, b: Dump, max_samples: int, only: set[str] | None) -> tuple[list[str], int]:
    """Return (report lines, difference count)."""
    report: list[str] = []
    diffs = 0
    if a.map_paths != b.map_paths:
        diffs += 1
        report.append("header: map-path sets differ")
        for line in sorted(set(a.map_paths) ^ set(b.map_paths)):
            report.append(f"  {line}")

    names_a = {n for n in a.tables if only is None or n in only}
    names_b = {n for n in b.tables if only is None or n in only}
    for name in sorted(names_a - names_b):
        diffs += 1
        report.append(f"only in A: [table {name}]")
    for name in sorted(names_b - names_a):
        diffs += 1
        report.append(f"only in B: [table {name}]")

    for name in sorted(names_a & names_b):
        cols_a, rows_a = a.tables[name]
        cols_b, rows_b = b.tables[name]
        if cols_a != cols_b:
            diffs += 1
            report.append(f"[table {name}]: columns differ")
            report.append(f"  A: {cols_a}")
            report.append(f"  B: {cols_b}")
            continue
        i = j = 0
        only_a: list[str] = []
        only_b: list[str] = []
        while i < len(rows_a) and j < len(rows_b):
            if rows_a[i] == rows_b[j]:
                i += 1
                j += 1
            elif rows_a[i] < rows_b[j]:
                only_a.append(rows_a[i])
                i += 1
            else:
                only_b.append(rows_b[j])
                j += 1
        only_a.extend(rows_a[i:])
        only_b.extend(rows_b[j:])
        if only_a or only_b:
            diffs += 1
            report.append(f"[table {name}]: -{len(only_a)}/+{len(only_b)}  (A-only/B-only rows of {len(rows_a)}/{len(rows_b)})")
            for line in only_a[:max_samples]:
                report.append(f"  - {line[:400]}")
            for line in only_b[:max_samples]:
                report.append(f"  + {line[:400]}")
            if len(only_a) > max_samples:
                report.append(f"  ... {len(only_a) - max_samples} more A-only")
            if len(only_b) > max_samples:
                report.append(f"  ... {len(only_b) - max_samples} more B-only")
    return report, diffs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dump_a", type=Path)
    parser.add_argument("dump_b", type=Path)
    parser.add_argument("--max-samples", type=int, default=5, help="Sample rows shown per table (default 5).")
    parser.add_argument("--tables", default=None, help="Comma-separated tables to compare (default: all).")
    args = parser.parse_args(argv)
    for path in (args.dump_a, args.dump_b):
        if not path.is_file():
            parser.error(f"dump not found: {path}")

    try:
        a = parse_dump(args.dump_a)
        b = parse_dump(args.dump_b)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    only = {t.strip() for t in args.tables.split(",")} if args.tables else None
    report, diffs = diff_dumps(a, b, args.max_samples, only)
    if diffs == 0:
        compared = len([n for n in a.tables if only is None or n in only])
        print(f"dumps identical: {compared} tables, "
              f"{sum(len(a.tables[n][1]) for n in a.tables if only is None or n in only)} rows")
        return 0
    print(f"dumps differ ({diffs} difference(s)):")
    print("\n".join(report))
    return 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Rewrite volatile content in a canonical dump: content-keyed ids, blank stamps."""

import sys
from collections import defaultdict

DROP_TABLES = {"build_runs", "events", "tool_metrics", "dataflow"}
TS = "<TS>"


def load_sections(src: str):
    sections = []  # (table or None, header lines, rows as cell lists)
    current, header, rows = None, [], []
    with open(src, encoding="utf-8") as fh:
        for line in fh.read().split("\n"):
            if line.startswith("[table ") and line.endswith("]"):
                if current is not None:
                    sections.append((current, header, rows))
                current, header, rows = line[len("[table "):-1], [], []
            elif line.startswith("# columns: ") and current is not None:
                header = line[len("# columns: "):].split("\t")
            elif current is not None and line:
                cells = line.split("\t")
                if cells[0].startswith("\\[table "):
                    cells[0] = cells[0][1:]
                rows.append(cells)
    if current is not None:
        sections.append((current, header, rows))
    return sections


def col(header, name):
    return header.index(name) if name in header else None


def normalize(src: str, dst: str) -> None:
    # Content keys per table, applied identically to every dump.
    fkey = {}   # files.id -> key
    skey = {}   # symbols.id -> key
    dupes = defaultdict(int)

    def ordinal(key):
        n = dupes[key]
        dupes[key] += 1
        return key if n == 0 else f"{key}#{n}"

    sections = load_sections(src)
    by_table = {t: (h, r) for t, h, r in sections}
    if "files" in by_table:
        h, rows = by_table["files"]
        for r in rows:
            fkey[r[0]] = ordinal("@F|" + r[col(h, "repo_id")] + "|" + r[col(h, "path")])
    if "symbols" in by_table:
        h, rows = by_table["symbols"]
        for r in rows:
            skey[r[0]] = ordinal(
                "@S|" + fkey.get(r[col(h, "file_id")], r[col(h, "file_id")])
                + "|" + r[col(h, "name")] + "|" + r[col(h, "kind")]
                + "|" + r[col(h, "line_start")] + "|" + r[col(h, "line_end")]
                + "|" + r[col(h, "column_start")] + "|" + r[col(h, "column_end")]
            )

    def ref(table, header, r, name):
        i = col(header, name)
        if i is None:
            return r
        raw = r[i]
        if raw == "\\N":
            return r
        mapped = (skey if name in ("source_id", "target_id", "symbol_id") else fkey).get(raw, raw)
        r[i] = mapped
        return r

    out = ["# cairn-canonical-dump 1"]
    dupes.clear()
    for table, header, rows in sections:
        if table in DROP_TABLES:
            continue
        out.append(f"[table {table}]")
        out.append("# columns: " + "\t".join(header))
        if not header:
            out.extend("\t".join(r) for r in sorted(rows))
            continue
        stamped = col(header, "indexed_at")
        if stamped is None:
            stamped = col(header, "recorded_at")
        if stamped is None:
            stamped = col(header, "timestamp")
        # mtimes are checkout state, not content: the same bytes stat differently
        # on every clone.
        mtime = col(header, "mtime")
        trace = col(header, "stack_trace")
        idcol = col(header, "id")
        rewritten = []
        for r in rows:
            r = list(r)
            if stamped is not None:
                r[stamped] = TS
            if mtime is not None:
                r[mtime] = TS
            if trace is not None:
                r[trace] = "<TRACE>"
            if table == "files":
                r[idcol] = fkey.get(r[idcol], r[idcol])
            elif table == "symbols":
                r = ref(table, header, r, "file_id")
                r[idcol] = skey.get(r[idcol], r[idcol])
            elif table == "edges":
                raw_target = r[col(header, "target_id")]
                tname = r[col(header, "target_name")]
                r = ref(table, header, r, "source_id")
                r = ref(table, header, r, "target_id")
                target = (
                    skey.get(raw_target, raw_target) if raw_target != "\\N"
                    else ("n|" + tname if tname != "\\N" else "nn")
                )
                r[idcol] = ordinal(
                    "@E|" + r[col(header, "source_id")] + "|" + r[col(header, "kind")]
                    + "|" + r[col(header, "line")] + "|" + r[col(header, "column")] + "|" + target
                    + "".join(
                        "|" + r[col(header, c)]
                        for c in ("receiver_type", "call_arity")
                        if col(header, c) is not None
                    )
                )
            elif table == "imports":
                r = ref(table, header, r, "file_id")
                r[idcol] = ordinal(
                    "@I|" + r[col(header, "file_id")] + "|" + r[col(header, "imported_path")]
                    + "|" + r[col(header, "line")] + "|" + r[col(header, "local_alias")]
                )
            elif table == "parse_errors":
                r[idcol] = ordinal("@P|" + r[col(header, "repo_id")] + "|" + r[col(header, "file_path")])
            elif table == "skipped_files":
                r[idcol] = ordinal(
                    "@SK|" + r[col(header, "repo_id")] + "|" + r[col(header, "path")]
                    + "|" + r[col(header, "reason")]
                )
            elif table == "rationale":
                r = ref(table, header, r, "file_id")
                r = ref(table, header, r, "symbol_id")
                r[idcol] = ordinal(
                    "@R|" + r[col(header, "file_id")] + "|" + r[col(header, "line")]
                    + "|" + r[col(header, "kind")] + "|" + r[col(header, "text")]
                )
            rewritten.append("\t".join(r))
        out.extend(sorted(rewritten))
    text = "\n".join(out) + "\n"
    # kg_dump maps the scratch prefix to <WORKSPACE>, but symlink-resolved
    # paths (macOS /tmp -> /private/tmp) keep a leading /private past the
    # mapped marker; collapse it so dumps normalize identically per host.
    text = text.replace("/private<WORKSPACE>", "<WORKSPACE>")
    with open(dst, "w", encoding="utf-8") as fh:
        fh.write(text)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(f"usage: {argv[0]} <dump-in> <normalized-out>", file=sys.stderr)
        return 2
    normalize(argv[1], argv[2])
    print(f"normalized {argv[1]} -> {argv[2]}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

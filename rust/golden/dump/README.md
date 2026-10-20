# Canonical-dump parity fixtures

- `corner-store.kg` — corner-case store built by
  `scripts/rust_migration/make_corner_store.py` (all cell types incl. NUL text,
  inf floats, blobs, a section-header-lookalike row, quoted identifiers, an
  fts5 and a vec0 virtual table with their shadow tables).
- `corner-store.canonical.txt` — `kg_dump.py` output over that store; the Rust
  canonical dump must reproduce it byte-for-byte.
- `corner-store.mapped.canonical.txt` — same with
  `--map-path '/Users/x=<HOME>'`.

Regenerate all three with:

```sh
.venv/bin/python scripts/rust_migration/make_corner_store.py rust/golden/dump/corner-store.kg
.venv/bin/python scripts/rust_migration/kg_dump.py rust/golden/dump/corner-store.kg -o rust/golden/dump/corner-store.canonical.txt
.venv/bin/python scripts/rust_migration/kg_dump.py rust/golden/dump/corner-store.kg --map-path '/Users/x=<HOME>' -o rust/golden/dump/corner-store.mapped.canonical.txt
```

The Rust side is `crates/cairn-store/tests/canonical_dump_golden.rs`; the Python
`kg_dump.py` remains the arbiter (P0-3). Cross-build content diffs run
`kg_diff.py` over two dumps.

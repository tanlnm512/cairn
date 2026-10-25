# Canonical-dump parity fixtures

- `corner-store.kg` — corner-case store built by
  `scripts/rust_migration/make_corner_store.py` (all cell types incl. NUL text,
  inf floats, blobs, a section-header-lookalike row, quoted identifiers, an
  fts5 and a vec0 virtual table with their shadow tables).
- `corner-store.canonical.txt` — `kg_dump.py` output over that store; the Rust
  canonical dump must reproduce it byte-for-byte.
- `corner-store.mapped.canonical.txt` — same with
  `--map-path '/Users/x=<HOME>'`.
- `self-corpus.norm.txt` — the G2 base-leg reference: `kg_dump.py --map-path
  <workspace>=<WORKSPACE>` + `kg_normalize.py` output over a `build-graph`
  store of the pinned parity corpus (`rust/golden/stores/self/corpus`).
  Frozen; the Rust `build-graph` dump of the same corpus (staged, marker file
  restored) must be content-equal to it (`g2_check.sh`). Regenerate by
  replaying those steps over a staged corpus copy and writing the normalized
  dump here.

Regenerate the corner-store trio with:

```sh
.venv/bin/python scripts/rust_migration/make_corner_store.py rust/golden/dump/corner-store.kg
.venv/bin/python scripts/rust_migration/kg_dump.py rust/golden/dump/corner-store.kg -o rust/golden/dump/corner-store.canonical.txt
.venv/bin/python scripts/rust_migration/kg_dump.py rust/golden/dump/corner-store.kg --map-path '/Users/x=<HOME>' -o rust/golden/dump/corner-store.mapped.canonical.txt
```

The Rust side is `crates/cairn-store/tests/canonical_dump_golden.rs`; the Python
`kg_dump.py` remains the arbiter (P0-3). Cross-build content diffs run
`kg_diff.py` over two dumps.

# Canonical-dump parity fixtures

- `corner-store.kg` — corner-case store built by `cargo xtask corner-store`
  (all cell types incl. NUL text, inf floats, blobs, a section-header-lookalike
  row, quoted identifiers, an fts5 and a vec0 virtual table with their shadow
  tables).
- `corner-store.canonical.txt` — `cargo xtask kg-dump` output over that store;
  the canonical dump must reproduce it byte-for-byte.
- `corner-store.mapped.canonical.txt` — same with
  `--map-path '/Users/x=<HOME>'`.
- `self-corpus.norm.txt` — the G2 base-leg reference: `kg-dump --map-path
  <workspace>=<WORKSPACE>` + `kg-normalize` output over a `build-graph` store
  of the pinned parity corpus (`rust/golden/stores/self/corpus`). Frozen; the
  `build-graph` dump of the same corpus (staged, marker file restored) must be
  content-equal to it (`g2_check.sh`).

Regenerate the corner-store trio with:

```sh
cargo xtask corner-store rust/golden/dump/corner-store.kg
cargo xtask kg-dump rust/golden/dump/corner-store.kg -o rust/golden/dump/corner-store.canonical.txt
cargo xtask kg-dump rust/golden/dump/corner-store.kg --map-path '/Users/x=<HOME>' -o rust/golden/dump/corner-store.mapped.canonical.txt
```

The check is `crates/cairn-store/tests/canonical_dump_golden.rs`.
Cross-build content diffs run `cargo xtask kg-diff` over two dumps.

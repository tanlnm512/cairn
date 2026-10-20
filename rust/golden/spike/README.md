# P0-5 spike fixtures

- `search-store.kg` — the cairn self-store corpus, capped by `SUBSET_FILE_CAP`
  in `scripts/rust_migration/spike_golden.py` to an evenly spaced sample of
  indexable files, built by `scripts/rust_migration/spike_golden.py`, pruned to
  the tables `search_symbols` reads (`repos`, `files`, `symbols`,
  `symbols_fts`) and response-verified query-for-query against the full scratch
  store before check-in.
- `../mcp/search_symbols.json` — `spike_queries` holds the >=5 captured query /
  response pairs this store must serve golden-equal; entry 0 duplicates the
  committed golden capture.

Regenerate both with `.venv/bin/python scripts/rust_migration/spike_golden.py`
(a corpus change can reorder bm25 ties; see the golden diff message, and pass
`--record` to re-baseline). The Rust integration test
(`crates/cairn-daemon/tests/spike_golden.rs`) reads only these checked-in bytes.

# P0-5 spike fixtures

- `search-store.kg` — the cairn self-store corpus, capped to an evenly spaced
  sample of indexable files, pruned to
  the tables `search_symbols` reads (`repos`, `files`, `symbols`,
  `symbols_fts`) and response-verified query-for-query against the full scratch
  store before check-in.
- `../mcp/search_symbols.json` — `spike_queries` holds the >=5 captured query /
  response pairs this store must serve golden-equal; entry 0 duplicates the
  committed golden capture.

Frozen: the bytes are the spec of record; nothing regenerates them.
Verify with `cargo test --manifest-path rust/Cargo.toml -p cairn-daemon
--test spike_golden`, which reads only these checked-in bytes.

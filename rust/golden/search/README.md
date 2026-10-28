# P1b search fixtures

- `semantic-store.kg` -- the cairn self-store corpus, capped to an
  evenly spaced sample of indexable files, built and hash-embedded,
  pruned to the tables `semantic_search` reads
  (`repos`, `files`, `symbols`, `symbols_fts*`, `embeddings`) and
  response-verified query-for-query against the full scratch store
  before check-in.
- `../mcp/semantic_search.json` -- `spike_queries` holds the captured
  query / response pairs this store must serve golden-equal; entry 0
  duplicates the committed golden capture.

Frozen: the bytes are the spec of record; nothing regenerates them.
Verify with `cargo test --manifest-path rust/Cargo.toml -p cairn-query
--test golden_search`, which reads only these checked-in bytes.

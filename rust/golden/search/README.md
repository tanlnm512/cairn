# P1b search fixtures

- `semantic-store.kg` -- the cairn self-store corpus built and
  hash-embedded by this harness, pruned to the tables
  `semantic_search` reads (`repos`, `files`, `symbols`,
  `symbols_fts*`, `embeddings`) and response-verified
  query-for-query against the full store before check-in.
- `../mcp/semantic_search.json` -- `spike_queries` holds the captured
  query / response pairs this store must serve golden-equal; entry 0
  duplicates the committed golden capture.

Regenerate with `.venv/bin/python scripts/rust_migration/semantic_golden.py`
(a corpus change can move the nearest neighbors; see the golden diff
message). The Rust integration test reads only these checked-in bytes.

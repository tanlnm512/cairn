# P1b search fixtures

- `semantic-store.kg` -- the cairn self-store corpus, capped by
  `SUBSET_FILE_CAP` in `scripts/rust_migration/spike_golden.py` to an
  evenly spaced sample of indexable files, built and hash-embedded by
  this harness, pruned to the tables `semantic_search` reads
  (`repos`, `files`, `symbols`, `symbols_fts*`, `embeddings`) and
  response-verified query-for-query against the full scratch store
  before check-in.
- `../mcp/semantic_search.json` -- `spike_queries` holds the captured
  query / response pairs this store must serve golden-equal; entry 0
  duplicates the committed golden capture.

Regenerate with `.venv/bin/python scripts/rust_migration/semantic_golden.py`
(a corpus change can move the nearest neighbors; see the golden diff
message, and pass `--record` to re-baseline). The Rust integration
test reads only these checked-in bytes.

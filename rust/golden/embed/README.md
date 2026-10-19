# 2f embeddings-build fixtures

- `embed-corpus.kg` -- pre-embed store over the `corpus/` fixture
  (built by the Python build path; carries two orphan 'ghost-sym'
  rows the embed pass must reap).
- `embed-golden.json` -- the `cairn embed` result over that store
  (hash backend): summary counts, per-row embeddings/embeddings_mv/
  term_df content (embedded_at excluded), per-model vec0 row counts,
  and sample vec0 KNN hits for the fixed probe queries.

Regenerate with `.venv/bin/python scripts/rust_migration/embed_golden.py`.
The Rust side is `crates/cairn-build/tests/embeddings_parity.rs`; it
embeds a copy of `embed-corpus.kg` and must reproduce every exported
value ((floats within 1e-6).

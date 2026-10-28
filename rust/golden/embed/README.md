# 2f embeddings-build fixtures

- `embed-corpus.kg` -- pre-embed store over the `corpus/` fixture
  (carries two orphan 'ghost-sym' rows the embed pass must reap).
- `embed-golden.json` -- the `cairn embed` result over that store
  (hash backend): summary counts, per-row embeddings/embeddings_mv/
  term_df content (embedded_at excluded), per-model vec0 row counts,
  and sample vec0 KNN hits for the fixed probe queries.

Frozen: the bytes are the spec of record; nothing regenerates them.
Verify with `cargo test --manifest-path rust/Cargo.toml -p cairn-build
--test embeddings_parity`; it embeds a copy of `embed-corpus.kg` and must
reproduce every exported value (floats within 1e-6).

# Flow-family parity fixtures

Frozen fixtures: the spec of record — the Rust implementation must keep
matching them.

- `corpus/` — the deterministic two-repo-namespace demo corpus the five flow
  tools are captured against.
- `flow-store.kg` — the store built from `corpus/` (closure index included,
  never embedded: explore stays lexical-only).
- `{explore,trace_flow,cross_repo_deps,visualize_graph,taint}.json` — the
  five tool fixtures, each carrying its per-case captures in a `cases` array.
  They live here, not under `../mcp/`: the two harnesses own disjoint
  fixture paths.

Verify with `cargo test --manifest-path rust/Cargo.toml -p cairn-query
--test flow_golden`.

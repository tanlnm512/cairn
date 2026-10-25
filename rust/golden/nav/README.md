# P1b nav fixtures

- `nav-store.kg` — the cairn self-store corpus built by
  the retired Python recorder, pruned to the tables the four nav
  tools read (`repos`, `files`, `symbols`, `edges`, `imports`; no FTS, no
  embeddings, no closure) and verified response-equal to the full store for
  the four nav-tool golden queries before check-in.

- `../mcp/{find_definition,get_callers,get_callees,impact_analysis}.json` —
  the committed goldens the Rust nav port must reproduce; the harness
  re-verifies golden-equality on every regeneration.

Frozen: the bytes are the spec of record (the Python recorder is retired).
Verify with `cargo test --manifest-path rust/Cargo.toml -p cairn-query
--test nav_golden`, which reads only these checked-in bytes.

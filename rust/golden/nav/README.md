# P1b nav fixtures

- `nav-store.kg` — the cairn self-store corpus built by
  `scripts/rust_migration/nav_golden.py`, pruned to the tables the four nav
  tools read (`repos`, `files`, `symbols`, `edges`, `imports`; no FTS, no
  embeddings, no closure) and verified response-equal to the full store for
  the four nav-tool golden queries before check-in.

- `../mcp/{find_definition,get_callers,get_callees,impact_analysis}.json` —
  the committed goldens the Rust nav port must reproduce; the harness
  re-verifies golden-equality on every regeneration.

Regenerate with `.venv/bin/python scripts/rust_migration/nav_golden.py`.
The Rust integration test (`crates/cairn-query/tests/nav_golden.rs`) reads
only these checked-in bytes.

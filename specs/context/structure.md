# Code structure: cairn

Module map of the Rust engine, its entry points and the development-only
Python trees. Every path below was read from the tree; re-verify with
`rg --files rust/crates` before citing.

## Distribution
- One engine binary, `cairn`, built from `rust/crates/cairn-cli` and versioned
  by `Cargo.toml` `[workspace.package]`.
- Channels: GitHub release tarballs, a Homebrew tap, and crates.io/binstall
  (`.github/workflows/release-rust.yml`).

## Cargo workspace (`rust/`)
| Crate | Role |
|---|---|
| `cairn-store` | SQLite schema mirror and migrations (`schema.rs`), connection factory (`conn.rs`), build lock and atomic swap (`swap.rs`), sqlite-vec helpers (`vec0.rs`), canonical dump (`dump.rs`) |
| `cairn-parse` | tree-sitter registry and per-language extraction (`parser_{script,web,apple,jvm,sys}.rs`), generic tier, symbol/edge/import model, rationale comments |
| `cairn-build` | scanner, rayon builder, resolver (exact/ambiguous/unresolved triage, repair), incremental update and watcher, transitive closure, dataflow, embeddings, routes and service calls, SCIP import and overlay (`scip/`) |
| `cairn-query` | read path and knowledge layer: search (FTS5 + vector + RRF), navigation and impact, flow and taint, federation, KM/memory/compass (`tools_km.rs`), wiki and task queue (`tools_wiki.rs`, `tasks.rs`), review engine, ingest, skill generation, Python sidecar resolution (`sidecar.rs`) |
| `cairn-daemon` | rmcp Streamable HTTP server for the 26 MCP tools (`server.rs`, `tools.rs`), bearer auth, healthz, daemon lifecycle (launchd plist on macOS, detached process elsewhere) |
| `cairn-cli` | the `cairn` binary: clap tree in `cli.rs` (Core / Knowledge / System groups dispatched to `cmds_*.rs`), agent install and hooks, doctor, dashboard (`dashboard/`, minijinja templates), pack |

Vendored: `rust/vendor/tree-sitter-dart` (workspace member) and
`rust/vendor/sqlite-vec` (patched crate).

## Entry points
- `rust/crates/cairn-cli/src/main.rs`: migration-harness shims
  (`canonical-dump`, `build-graph`, `closure`), then `cli::run`.
- MCP: `cairn serve` (foreground) and `cairn connect` (ensures the shared
  daemon on loopback).
- Dashboard: `cairn dashboard`.

## Development tooling
- `rust/crates/xtask/`: repo gates, replay oracles, canonical-dump tooling
  and the measured gates (`cargo xtask` lists them).
- `scripts/`: installers and `rust_migration/` (`g2_check.sh`).
- `tests/fixtures/`: SCIP-indexing fixture repos.
- `packaging/`: the Homebrew formula template.

## Tests and oracles
- Rust: unit tests in each crate, integration suites under
  `rust/crates/*/tests/`.
- Golden fixtures under `rust/golden/` (`mcp`, `cli`, `flow`, `nav`,
  `search`, `km`, `dump`, `embed`, `spike`, and pinned stores in `stores/`).

## Other trees
- `extensions/vscode/`: VS Code extension (npm-confined, separate release
  workflow).
- `docs/`: user and contributor documentation; `docs/cli-reference.md` is
  the CLI reference.
- `.cairn-knowledge/`: repository seed for the knowledge bundle.

# cairn — rust workspace

Six crates mirroring the python package (`cairn-store`, `cairn-query`,
`cairn-parse`, `cairn-build`, `cairn-daemon`, `cairn-cli`) plus the vendored
dart grammar. The workspace version tracks `pyproject.toml` until the flip.

- Spec and phased plan: `specs/rust-migration/` (`spec.md`, `plan.md`)
- Campaign ledger: `specs/rust-migration/PROGRESS.md`
- Frozen contracts: the `.kg` schema (C-1) and the 26-tool MCP surface (C-2)
- Parity harnesses: `scripts/rust_migration/` (golden recorder, canonical
  dump/diff, parity runner, cli goldens, g2 dump-parity check)
- Gates: `cargo fmt --all -- --check`, `cargo clippy --workspace --all-targets
  -- -D warnings`, `cargo test --workspace` (CI leg `.github/workflows/rust.yml`)

The engine defaults to python; `CAIRN_DAEMON_IMPL=rust` opts into the rust
daemon.

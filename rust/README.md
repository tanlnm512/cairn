# cairn — rust workspace

Seven crates (`cairn-store`, `cairn-query`, `cairn-parse`, `cairn-build`,
`cairn-daemon`, `cairn-cli`, `xtask`) plus the vendored `tree-sitter-dart`
and `sqlite-vec` crates build the `cairn` engine binary that every install
channel ships. The workspace version is bumped at release time; cut the
`v*` tag from a tree whose workspace version already equals it.

- Frozen contracts: the `.kg` schema (C-1) and the 26-tool MCP surface (C-2),
  pinned by the goldens under `golden/`
- Oracle replay: `cargo xtask` (canonical dump/diff, parity runner, cli
  goldens) plus `scripts/g2_check.sh` (g2 dump-parity check)
- Gates: `cargo fmt --all -- --check`, `cargo clippy --workspace --all-targets
  -- -D warnings`, `cargo test --workspace` (CI leg `.github/workflows/rust.yml`)
- Dependency and pin rulings (D-###): the migration spec, campaign ledger,
  and decision log are archived in git history under `specs/rust-migration/`
  (last readable at any pre-removal tag); every runtime crate is an exact
  `=` pin gated by cargo-deny

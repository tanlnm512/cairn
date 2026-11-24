# Tech stack & conventions: cairn

Stack, build and test runners, and gates. Cited from `Cargo.toml`,
`pyproject.toml`, `.pre-commit-config.yaml` and `.github/workflows/`.

## Stack
- Rust, edition 2024, one cargo workspace (manifest at the repo root, crates
  under `rust/`) with exact-pinned
  dependencies and a cargo-deny license/advisory gate. Key crates: rmcp
  (MCP server), rusqlite with bundled SQLite and FTS5, sqlite-vec (vendored
  patch), tree-sitter with 15 grammar crates, rayon, clap, notify, prost,
  minijinja.
- Release profile strips symbols (`[profile.release] strip = true`).
- Distribution: the same `cairn` binary ships as PyPI bin wheels + sdist
  (maturin, `pyproject.toml`; Python is the packaging channel only) and as
  GitHub Release tarballs.

## Build and test
- `cargo build --release --manifest-path Cargo.toml -p cairn-cli`.
- `cargo test --workspace --no-fail-fast`; `cargo clippy --workspace
  --all-targets -- -D warnings`; `cargo fmt --all -- --check`.
- Oracles: `cargo xtask parity --store {self,yarl,attrs}`,
  `cargo xtask cli-golden`, `g2_check.sh` (`G2_LEGS=base,rebuild,staging`).
  They reuse whatever release binary the target dir holds;
  rebuild it first.

## Gates
- Hooks (prek, config in `.pre-commit-config.yaml`): gitleaks,
  YAML/TOML/merge checks, large files, the Rust comment/doc-block length
  ratchet (`check-comment-lengths`, shrink-only baseline), cargo fmt, cargo
  clippy.
- CI workflows:

| Workflow | Trigger | Jobs |
|---|---|---|
| `ci.yml` | push, PR, merge queue | PR title, pre-commit, quality ratchet, dependency review, container |
| `rust.yml` | push, PR, merge queue | fmt, clippy, test, deny, windows leg (clippy + test + release build) |
| `rust-parity.yml` | PR, nightly, dispatch | parity (three stores + cli golden + G2 base); G2 rebuild/staging legs on nightly and dispatch |
| `release.yml` | tag | PyPI wheels + sdist, GitHub release |
| `release-rust.yml` | tag | binary tarballs attached to the GitHub Release |
| `release-dryrun.yml` | nightly, dispatch | release build per target (when main changed in the last day) |
| `weekly-smoke.yml` | weekly, dispatch | smoke |
| `review.yml` | PR | advisory review |
| `extension-release.yml` | tag | VS Code extension |

## Conventions
- Shipping workflow, test-first, dependency decisions and test isolation:
  `specs/CONSTITUTION.md` (C-01–C-04).
- Comment, doc and log style and the reuse-first rule: `AGENTS.md`.

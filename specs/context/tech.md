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
- Python ≥ 3.10 only for dev tooling (pre-commit, commitizen, the wheel
  build). The wheel has no runtime dependencies and no Python code.
- Wheel build: maturin (`[tool.maturin] bindings = "bin"` over
  `rust/crates/cairn-cli/Cargo.toml`) packages the engine binary as the
  `cairn` executable, tagged `py3-none-<platform>` with no Python module
  inside; the sdist carries the cargo workspace including the vendored
  sqlite-vec and grammar sources.

## Build and test
- `cargo build --release -p cairn-cli` (from `rust/`, or pass
  `--manifest-path Cargo.toml`).
- `cargo test --workspace --no-fail-fast`; `cargo clippy --workspace
  --all-targets -- -D warnings`; `cargo fmt --all -- --check`.
- Oracles: `cargo xtask parity --store {self,yarl,attrs}`,
  `cargo xtask cli-golden`, `g2_check.sh` (`G2_LEGS=base,rebuild,staging`),
  `wheel_smoke.sh`. They reuse whatever release binary the target dir holds;
  rebuild it first.

## Gates
- Pre-commit (`.pre-commit-config.yaml`): gitleaks, YAML/TOML/merge
  checks, large files, the comment/docstring length
  ratchet (`check-comment-lengths`, shrink-only baseline), cargo fmt, cargo
  clippy.
- CI workflows:

| Workflow | Trigger | Jobs |
|---|---|---|
| `ci.yml` | push, PR, merge queue | PR title, pre-commit, quality ratchet, dependency review, build, container |
| `rust.yml` | push, PR, merge queue | fmt, clippy, test, deny, release build matrix (macOS and Linux gnu/musl targets) |
| `rust-parity.yml` | PR (cheap subset), nightly, dispatch | parity, parity-stores, g2-legs, wheel-smoke |
| `release.yml` | tag | wheels + sdist, PyPI publish, GitHub release |
| `release-rust.yml` | tag | binaries, GitHub release, Homebrew tap, crates.io |
| `weekly-smoke.yml` | weekly, dispatch | smoke |
| `review.yml` | PR | advisory review |
| `extension-release.yml` | tag | VS Code extension |

## Conventions
- Shipping workflow, test-first, dependency decisions and test isolation:
  `specs/CONSTITUTION.md` (C-01–C-04).
- Comment, doc and log style and the reuse-first rule: `AGENTS.md`.
- Local quirk: `cmds_doctor::tests::daemon_check_reports_the_serving_impl`
  asserts against whatever daemon is live on the machine and fails while a
  developer daemon runs.

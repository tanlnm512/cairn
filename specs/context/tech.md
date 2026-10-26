# Tech stack & conventions: cairn

Stack, build and test runners, and gates. Cited from `rust/Cargo.toml`,
`pyproject.toml`, `.pre-commit-config.yaml` and `.github/workflows/`.

## Stack
- Rust, edition 2024, one cargo workspace under `rust/` with exact-pinned
  dependencies and a cargo-deny license/advisory gate. Key crates: rmcp
  (MCP server), rusqlite with bundled SQLite and FTS5, sqlite-vec (vendored
  patch), tree-sitter with 15 grammar crates, rayon, clap, notify, prost,
  minijinja.
- Release profile strips symbols (`[profile.release] strip = true`).
- Python ≥ 3.10 only for the PyPI launcher, the two sidecars and dev
  tooling. The package has no runtime dependencies; optional extras:
  `ingest` (pymupdf4llm, mammoth, markdownify) and `graph-analytics`
  (networkx).
- Wheel build: setuptools with `setup.py` forcing a platform tag
  (`py3-none-<platform>`) around the injected binary; the sdist carries no
  binary.

## Build and test
- `cargo build --release -p cairn-cli` (from `rust/`, or pass
  `--manifest-path rust/Cargo.toml`).
- `cargo test --workspace --no-fail-fast`; `cargo clippy --workspace
  --all-targets -- -D warnings`; `cargo fmt --all -- --check`.
- Python: `uv sync --extra dev`, then `uv run pytest`.
- Oracles: `scripts/rust_migration/parity.py --store {self,yarl,attrs}`,
  `cli_golden.py`, `g2_check.sh` (`G2_LEGS=base,rebuild,staging`),
  `wheel_smoke.sh`. They reuse whatever release binary the target dir holds;
  rebuild it first.

## Gates
- Pre-commit (`.pre-commit-config.yaml`): ruff, gitleaks, YAML/TOML/merge
  checks, large files, debug statements, the comment/docstring length
  ratchet (`check-comment-lengths`, shrink-only baseline), cargo fmt, cargo
  clippy.
- CI workflows:

| Workflow | Trigger | Jobs |
|---|---|---|
| `ci.yml` | push, PR, merge queue | security (pip-audit, bandit), typecheck (mypy), PR title, pre-commit, quality ratchet, dependency review, test, build, container |
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

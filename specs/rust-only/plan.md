# Plan: rust-only

**Spec**: [spec.md](spec.md) | **Created**: 2026-10-26

## Milestones
| Phase | Milestone | Delivers (demoable) | Requirements | Depends on |
|-------|-----------|---------------------|--------------|------------|
| 1 | Coverage on the current pipeline | macOS x86_64 and musllinux x86_64/aarch64 wheels published by today's `release.yml` | FR-005 (partial) | — |
| 2 | Native communities | `cairn communities` runs in the engine; networkx sidecar unused | FR-001 | — |
| 3 | Native document conversion | docx and PDF ingest without Python; optional PDF converter package | FR-002, FR-003 | — |
| 4 | Bin-only wheels | maturin builds every wheel and a source sdist; launcher, `setup.py` and `_bin/` gone | FR-004, FR-005, FR-006, NFR-001 | 2, 3 |
| 5 | Windows | engine compiles, tests and runs on Windows x86_64; `win_amd64` wheel | FR-005, FR-007 | 4 |
| 6 | Performance gates and retired flags | daemon connection reuse; closure gate re-ruled and measured; `--lsp` resolved | FR-008, FR-009, FR-012 | — |
| 7 | Python retired | `src/cairn/` and Python-only gates removed; docs swept | FR-010, FR-011 | 4, 5 |

## Phase detail

### Phase 1 — Coverage on the current pipeline
- macOS x86_64: add `x86_64` to `CIBW_ARCHS_MACOS`, build the
  `x86_64-apple-darwin` engine (already produced by `release-rust.yml`)
  and copy it to `src/cairn/_bin/cairn` for that leg.
- musllinux: drop `*musllinux*` from `CIBW_SKIP` and reuse the static musl
  binaries already copied for the manylinux legs.
- Interim Windows notice: the launcher's no-engine message and the README
  point Windows users at WSL until Phase 5 ships.
- Tests: extend `tests/test_release_distribution.py` to pin the platform
  set; `wheel_smoke.sh` per leg through `CIBW_TEST_COMMAND`.

### Phase 2 — Native communities
- Record a decision superseding the "Louvain stays a sidecar" ruling.
  Acceptance is property-based: the same seed always gives the same
  partition, modularity on the fixtures is at least the networkx result
  within a tolerance, and the label and hub rules hold.
- Implement Louvain in `cairn-build` over the compact graph the closure
  already builds; no new dependency. Port the edge kinds and weights, hub
  labelling and dominant-segment rule from `src/cairn/communities.py`.
- Wire `CoreCommand::Communities` in `cmds_core.rs` to it; remove the
  `communities` routing from `console.py`.
- Tests: Rust owner tests for determinism and modularity on fixed fixtures;
  a `cli_golden.py` case for the output shape.

### Phase 3 — Native document conversion
- docx: read `word/document.xml` (and numbering, relationships) with `zip`
  and `quick-xml`, emit markdown in `cairn-query` beside `ingest.rs`.
  Record the two new dependencies as a decision.
- PDF: pure-Rust text extraction as the default (candidate crates evaluated
  in the decision record on quality, license and binary size).
- Optional high-fidelity PDF: move `ingest_convert.py` to a separate
  package (`cairn-intel-ingest`) that the engine invokes through the
  existing `sidecar.rs` resolution when installed; the AGPL dependency
  stays in that package.
- Tests: fixture `.docx` and `.pdf` files with expected markdown;
  `knowledge ingest --file` end to end without Python on PATH.

### Phase 4 — Bin-only wheels
- `pyproject.toml`: build backend `maturin`, `[tool.maturin]` with
  `bindings = "bin"` and `manifest-path =
  "rust/crates/cairn-cli/Cargo.toml"`. Delete `setup.py`, `MANIFEST.in`'s
  `_bin` rule, `src/cairn/_bin/`, `console.py` and `paths.py`.
- `release.yml`: replace cibuildwheel with `PyO3/maturin-action` over the
  target matrix:

| Wheel platform | Rust target | Runner |
|---|---|---|
| manylinux x86_64 / aarch64 | `x86_64-unknown-linux-gnu`, `aarch64-unknown-linux-gnu` | ubuntu (zig cross) |
| musllinux x86_64 / aarch64 | `x86_64-unknown-linux-musl`, `aarch64-unknown-linux-musl` | ubuntu (zig cross) |
| macOS x86_64 / arm64 | `x86_64-apple-darwin`, `aarch64-apple-darwin` | macos |
| Windows x86_64 | `x86_64-pc-windows-msvc` | windows (Phase 5) |

- sdist: `maturin sdist`; verify it contains the vendored grammars and
  sqlite-vec, and that `pip install --no-binary cairn-intel` builds.
- Size: measure each wheel; add `lto = "thin"` and `codegen-units = 1` to
  the release profile if a wheel nears the limit.
- Smoke: rewrite `wheel_smoke.sh` for the bin wheel (fresh venv, `cairn
  --version`, `config --json` contract, the executable is native) and run
  it on each platform's own runner before publish; Trusted Publishing
  unchanged.
- Tests: retire `test_wheel_binary_discovery.py`; update
  `test_package_inventory.py` and `test_release_distribution.py` for the
  new layout.
- Record a decision superseding the exec-shim binding.

### Phase 5 — Windows
- Add `x86_64-pc-windows-msvc` to `rust.yml` (build + test) to surface
  every failure.
- Gate or replace Unix-only code: `std::os::unix` permission and exec calls
  in `targz.rs`, `agent_install.rs`, `cmds_core.rs`, `scip/indexers.rs`,
  `lifecycle.rs`; the `flock` paths in `tools_km.rs` and the build lock in
  `swap.rs` (std `File::try_lock` is cross-platform).
- Daemon lifecycle: detached process plus pidfile on Windows (no launchd),
  `connect` reuse and stop paths; home and config paths through the
  platform home directory.
- Agent install: Windows config locations per client.
- Ship the `win_amd64` wheel through the Phase 4 matrix and a Windows
  release tarball in `release-rust.yml`. Record a decision superseding the
  Windows ruling.

### Phase 6 — Performance gates
- Daemon connection reuse in `cairn-daemon` (per worker, read and write
  connections), invalidated when the store's inode changes after a build
  swap; measure with `baseline.py` on self, yarl and attrs.
- Closure: record the re-ruled gate (peak WAL and wall time at the
  measured insert bound) and measure with `closure_bench.py`.
- `--lsp`: decide between an engine LSP pass (spawn a language server,
  upgrade ambiguous edges to exact, keep `ref_name` NULL on upgraded edges
  so incremental repair leaves them alone) and a hard error naming the
  retirement; a regression test pins whichever is chosen.

### Phase 7 — Python retired
- Delete `src/cairn/` and the `[project.optional-dependencies]` runtime
  extras; keep `pyproject.toml` as the maturin manifest.
- CI: remove pip-audit, bandit and mypy legs that scanned the deleted
  package, or scope them to `scripts/`; keep ruff for the dev scripts.
- Oracles: `cli_golden.py` and `parity.py` stay replay-only; drop
  Python-engine comparison code paths that can no longer run.
- Docs: `README.md` install section, `docs/cli-reference.md`,
  `docs/release-checklist.md`, `docs/deployment.md`, `AGENTS.md` CLI
  fallback; archive this spec.

## Dependencies
Phases 1, 2, 3 and 6 are independent. Phase 4 needs 2 and 3, because a
bin-only wheel cannot carry the Python sidecars. Phase 5's compile and
lifecycle work can start any time; its wheel ships through Phase 4's
matrix. Phase 7 closes after 4 and 5.

## Parallelization map
- Independent: Phase 1 (`release.yml`) ∥ Phase 2 (`cairn-build`,
  `cmds_core.rs`) ∥ Phase 3 (`cairn-query` ingest) ∥ Phase 6
  (`cairn-daemon`) — disjoint files.
- Independent: Phase 5 portability fixes ∥ Phases 2–3, except
  `cmds_core.rs`, which Phase 2 also edits (sequence those two commits).
- Strictly ordered: Phases 2 + 3 → Phase 4 → Windows wheel → Phase 7.

## Checkpoints
- **After Phase 1**: a tag dry-run produces six wheels; each passes
  `wheel_smoke.sh` on its runner.
- **After Phase 2**: `cairn communities` from a binary with no Python on
  PATH writes both tables; determinism and modularity tests green;
  `cli_golden.py` green.
- **After Phase 3**: `cairn knowledge ingest --file` stages fixture docx
  and PDF with no Python on PATH; with the optional package installed the
  PDF goes through it.
- **After Phase 4**: `release.yml` dry-run builds six wheels and the sdist
  with maturin; `pip install --no-binary cairn-intel` builds; no `.py`
  file in any wheel (`unzip -l`).
- **After Phase 5**: `rust.yml` Windows leg green; `win_amd64` wheel smoke
  green on `windows-latest`.
- **After Phase 6**: baseline p95 within the recorded tolerance on all 26
  tools; the closure gate measured against its new definition; `cairn
  build --lsp` behaves as the recorded decision says.
- **After Phase 7**: `git ls-files src/cairn` is empty; CI green; doc link
  check green.

## Decisions to record
- Native Louvain and its property-based acceptance (supersedes the
  communities-sidecar ruling).
- docx and PDF crates, and the optional converter package boundary.
- maturin bin packaging (supersedes the exec-shim binding).
- Windows support (supersedes the Windows-stays-on-Python ruling, whose
  premise ended with the Python engine).
- Re-ruled closure gate.
- `--lsp`: engine LSP pass or retirement.
- `prs`, `bench` and `eval` are retired: the Python engine they measured is
  gone and the binary carries no arm for them.

## Gates per change
`cargo fmt --check`, `cargo clippy --workspace --all-targets -D warnings`,
`cargo test --workspace --no-fail-fast`, targeted pytest while Python
remains, `parity.py` self/yarl/attrs, `cli_golden.py`, `g2_check.sh`
(`G2_LEGS=base,rebuild,staging`), and `wheel_smoke.sh` for packaging
changes. Rebuild the release binary before the oracles.

## Risks & mitigations
- Risk: Windows stays broken longer than Phase 1–4 take → mitigation: the
  interim notice in Phase 1, and Phase 5 portability work starts in
  parallel.
- Risk: a maturin wheel installs differently under pipx or uv tool →
  mitigation: the smoke test covers pip, pipx and `uv tool install` on one
  runner per OS.
- Risk: dropping the sidecars breaks users who rely on PDF table fidelity
  → mitigation: the optional converter package and a CHANGELOG note.

## Delivery
All phases land on one working branch, with one commit per completed
workstream and the gates above green before each commit; per C-01, the
branch reaches `main` through a PR with the audit checklist filled.

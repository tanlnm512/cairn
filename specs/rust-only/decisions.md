# Decisions: rust-only

Stable-id registry for the rust-only spec. One entry per decision the plan's
"Decisions to record" section lists, recorded in the phase that makes it.

## D-R01 — Native Louvain in the engine (Phase 2)

Supersedes the communities-sidecar ruling (D-013: `communities` runs the
networkx sidecar in-process).

**Decision**: `cairn communities` is computed natively in `cairn-build`
(`rust/crates/cairn-build/src/communities.rs`) over the compact structural
graph the closure already builds; no new dependency. The networkx sidecar and
its console routing are retired.

**Why**: the Python engine is gone; keeping a networkx sidecar forces every
install to carry a Python interpreter for one command and leaves US2-AC1
(binary-only `communities` writing both tables) unsatisfiable.

**Acceptance**: property-based, not byte-parity with networkx — random
streams differ, so partitions may legitimately differ. A fixed seed always
yields the same partition; modularity on the fixed fixtures is at least the
networkx reference within a pinned tolerance; the label and hub rules port
from `src/cairn/communities.py` (edge kinds/weights, dominant-segment labels,
hub fallback, sort orders, CLI flags and output shape).

**Cost**: one more module in `cairn-build` and per-command maintenance of the
Louvain core; partitions observed by users can differ from the sidecar's at
equal-or-better modularity.

## D-R02 — Cargo workspace manifest at the repo root (Phase 2)

**Decision**: the cargo workspace manifest and lockfile live at the repo root
(`Cargo.toml`, `Cargo.lock`), with members under `rust/crates/`,
`rust/vendor/`, and `.cargo/config.toml` pinning `build.target-dir =
"rust/target"` so every build-artifact path (scripts, workflows, golden
binaries, the Dockerfile aside) keeps resolving.

**Why**: the rust-only gates run bare `cargo fmt` / `cargo clippy` /
`cargo test --workspace` / `cargo build --release` from the repo root; with
the manifest nested under `rust/` those commands fail with "could not find
Cargo.toml" (cargo never searches downward). A root symlink cannot substitute
(cargo resolves member paths against the manifest's parent, not the link
target), and nested workspaces are unsupported.

**Cost**: every explicit `--manifest-path rust/Cargo.toml` reference had to
move (workflows, pre-commit, scripts, docs, Dockerfile); the Dockerfile builds
into `/src/target` since it does not carry the repo's `.cargo` pin.

## D-R02 — docx via `zip` + `quick-xml` in `cairn-query` (Phase 3)

docx→markdown is implemented natively in `rust/crates/cairn-query/src/convert.rs`
beside `ingest.rs`, reading `word/document.xml` (plus `styles.xml`,
`numbering.xml`, `word/_rels/document.xml.rels`) with the `zip` crate
(=9.1.2, `deflate-flate2` feature only) and `quick-xml` (=0.42.0). Both are
MIT and land together as one reader; recorded as one decision.

**Why**: FR-002 requires markdown conversion (headings, paragraphs, emphasis,
lists, tables, links) with no Python present; mammoth + markdownify pulled
two Python libraries and an interpreter hop for the same block model.

**Cost**: two new transitive trees (licenses verified against
rust/deny.toml's allowlist with cargo-deny); the binary-size cost is measured
together with the PDF crate in D-R03.

## D-R03 — native PDF text extraction via `pdf-extract` (=0.12.1) (Phase 3)

The engine's default `.pdf` path links `pdf-extract` 0.12.1 (MIT) and
normalizes its output into markdown paragraphs. No AGPL code is linked into
the engine (FR-003).

Candidates evaluated on quality / license / size:

- `pdf-extract` 0.12.1 — MIT, pure Rust; extracted the fixture PDF's
  paragraphs verbatim in local probes; errors and empty output map cleanly
  onto the existing skip-reason vocabulary. **Chosen.**
- raw `pdf` / `lopdf` — MIT/Apache, but content-stream extraction (font
  encodings, CMaps, Flate) would be hand-rolled for lower fidelity.
- `pdfium-render` (Pdfium) — permissive but a C++ binary blob and a
  cross-build burden, not pure Rust.
- PyMuPDF bindings — AGPL; forbidden in the engine by FR-003.

**Cost**: new transitive tree (cargo-deny clean against the allowlist) and
paragraph-text-only quality (no table or multi-column fidelity) — users who
need that fidelity install the converter package (D-R04). Release-binary
size for D-R02 + D-R03 together (aarch64-apple-darwin, `strip = true`):
56,478,128 → 58,088,544 bytes (+1,610,416, +2.85%), far under the 100 MB
wheel budget (NFR-001).

## D-R04 — optional converter package `cairn-intel-ingest` (Phase 3)

The Python converter moved out of `cairn-intel` into a standalone package at
`packaging/cairn-intel-ingest` (module `cairn_intel_ingest`, invoked as
`python -m cairn_intel_ingest <path>` through the existing `sidecar.rs`
resolution: `CAIRN_SIDECAR_PYTHON`, interpreter beside the binary, PATH). The
package is PDF-only and carries `pymupdf4llm` (AGPL) as a dependency; the
AGPL surface exists only there, never in the engine.

**Why**: FR-003 requires a separately-installable higher-fidelity PDF
converter while the default engine stays AGPL-free; a standalone package is
the only install shape that does both.

**Routing contract**: `.docx` always converts natively; `.pdf` prefers the
sidecar when `import cairn_intel_ingest` succeeds on a resolvable
interpreter, else converts natively; a present-but-failing sidecar skips with
the sidecar's reason (no silent fallback).

**Cost**: the `cairn-intel[ingest]` extra is retired (CI installs the
package from `packaging/` so pip-audit keeps auditing the pymupdf4llm
surface); publishing the package to PyPI is release engineering outside this
phase's code.

## D-R05 — Daemon connection reuse with store-swap invalidation (Phase 6)

**Requirement**: FR-008 (US4).

**Decision**: tool dispatch parks SQLite connections per flavor (read;
primary = writable unless read-only) instead of opening one per call. Every
checkout revalidates the store's identity — `(dev, inode)` on Unix — and
reopens when a build swap replaced the file, so a parked connection can
never serve the unlinked old store
(`rust/crates/cairn-daemon/src/conn_pool.rs`; leases park on drop, at most 8
idle per flavor, a connection left mid-transaction is dropped, not parked).

**Why**: the Python-era baseline harness showed per-tool p95 matching Python
on only 21/26 tools because the daemon opened a fresh SQLite connection
every call; the open cost (pragmas, busy timeout, file stat) dominated
small-store tools.

**Regression pins**: `tools::tests::sequential_conns_reuse_one_connection`
(reuse) and `tools::tests::store_swap_invalidates_the_reused_connection`
(post-swap reopen) in `rust/crates/cairn-daemon/src/tools.rs`.

**Measured** (`scripts/rust_migration/baseline.py` — authored this phase; 25
rounds + 2 warmup, all 26 tools, pinned stores, macOS arm64): the Python
daemon no longer exists to race against, so the recorded absolute p95 below
is the baseline and the gate is `baseline.py --store <s> --compare <json>`
at 1.5× per tool (default). Connection-reuse speedup vs the per-call-open
tree: self up to 3.50× (find_definition 0.983→0.281 ms), yarl up to 2.95×,
attrs up to 3.15×; compute-bound tools (ask_compass, explore, path)
unchanged within noise; no tool regressed beyond noise (worst 0.88× on
federated_search, a sub-ms tool that opens no store connection).

**Recorded baseline p95 (post-reuse, ms)** — self: ask_compass 200.474,
explore 125.384, path 129.644, recall_memory 56.810, search_knowledge
52.145, record_memory 42.225, wiki_generate 21.328, semantic_search 5.686,
knowledge_search 5.815, repo_map 4.746, get_compass 2.678, search_symbols
1.250, cross_repo_deps 1.355, knowledge_delete 0.750, impact_analysis
0.930, trace_flow 0.433, generate_flow 0.344, get_callers 0.374,
find_definition 0.281, knowledge_status 0.771, file_api 0.316, get_callees
0.305, visualize_graph 0.339, knowledge_add 0.730, trace_workflow 0.358,
federated_search 0.259. yarl: ask_compass 185.692, explore 108.067, path
81.022, recall_memory 54.475, search_knowledge 47.355, record_memory
42.428, wiki_generate 9.426, knowledge_search 6.228, semantic_search 3.529,
generate_flow 4.095, impact_analysis 3.291, repo_map 2.432, get_compass
2.905, trace_flow 1.428, knowledge_delete 0.754, search_symbols 0.717,
cross_repo_deps 0.479, get_callers 0.386, visualize_graph 0.313, file_api
0.368, find_definition 0.274, get_callees 0.285, knowledge_status 0.781,
knowledge_add 0.742, trace_workflow 0.424, federated_search 0.332. attrs:
ask_compass 194.672, explore 119.016, path 90.079, recall_memory 52.152,
search_knowledge 49.679, record_memory 41.722, wiki_generate 13.249,
knowledge_search 5.950, semantic_search 5.113, repo_map 4.001,
impact_analysis 3.482, generate_flow 2.323, get_compass 2.834,
search_symbols 1.744, knowledge_delete 0.705, trace_flow 0.551,
cross_repo_deps 0.667, get_callers 0.461, get_callees 0.429,
visualize_graph 0.309, find_definition 0.294, file_api 0.298,
knowledge_status 0.743, knowledge_add 0.719, trace_workflow 0.351,
federated_search 0.301.

## D-R06 — Re-ruled closure gate: insert-bound absolute numbers (Phase 6)

**Requirement**: FR-009. Supersedes the "closure ≥ 5× vs Python" migration
gate (which measured 1.14×).

**Decision**: the closure streams `INSERT`s into `transitive_edges`; its
cost is SQLite-insert-bound and identical in any engine, so an
engine-relative speedup ratio is the wrong ruler. The gate is re-ruled on
absolute re-measurable numbers: wall time at the measured insert bound, and
peak WAL during the rebuild.

**Measured** (`scripts/rust_migration/closure_bench.py` — authored this
phase; 5 iterations on fresh pinned-store copies, macOS arm64, `cairn
closure --db`):

| store | rows | median closure ms | rows/s | peak WAL MiB | single-tx WAL gain |
|---|---:|---:|---:|---:|---:|
| self | 26,807 | 91.0 | 294,582 | 5.4 | 1.00× |
| yarl | 12,467 | 37.8 | 329,815 | 2.8 | 0.99× |
| attrs | 12,539 | 40.6 | 308,842 | 2.8 | 0.99× |

Insert-bound evidence: replaying the same rows in one transaction with
`wal_autocheckpoint = 0` peaks at the same WAL size (gain ≈ 1.0×) — every
pinned-store closure fits under the 50,000-row commit bound, so the WAL
profile equals the unavoidable row volume and batched commits add no
headroom to exploit at this scale.

**New gate** (executable: `closure_bench.py --store self --compare <json>`,
default tolerance 3×): on the self pinned store, median closure wall time
≤ 275 ms (3× the measured 91 ms, absorbing CI machine variance) and peak
WAL during rebuild ≤ 11 MiB (2× the measured 5.4 MiB, catching
unbounded-WAL regressions).

## D-R07 — `cairn build --lsp` retired with a hard error (Phase 6)

**Requirement**: FR-012.

**Decision**: hard error over an engine LSP pass. The pyright upgrade pass
died with the Python engine; an engine-side LSP pass (spawn language
servers, upgrade ambiguous edges to exact) is a feature build outside this
spec's scope, and silently accepting the flag hides the loss from every
caller.

`--lsp` (`Some(true)`) exits 1 before any build work with a message naming
the retirement and pointing at the indexer's edge resolution;
`--lsp=false` remains a no-op equal to the default (it requests nothing).
Pinned by `rust/crates/cairn-cli/tests/build_lsp_retired.rs`
(`build_lsp_refuses_and_names_the_retirement`).

## D-R08 — maturin bin packaging for the PyPI wheel (Phase 4)

**Requirement**: FR-004, FR-005, FR-006, NFR-001 (US1, US3). Supersedes the
exec-shim binding (D-011: the wheel is a Python console script that execs a
binary injected into `src/cairn/_bin/`).

**Decision**: `cairn-intel` is built by maturin with `bindings = "bin"` over
`rust/crates/cairn-cli/Cargo.toml` (`[tool.maturin]` in `pyproject.toml`).
The wheel's only payload is the engine binary, installed as the `cairn`
executable and tagged `py3-none-<platform>`; no Python module ships. The
launcher (`console.py`, `paths.py`), `setup.py`, `MANIFEST.in` and the `_bin/`
injection are deleted. `release.yml` runs `PyO3/maturin-action` over the
plan's seven targets — the four linux legs cross-build on one ubuntu host
through zig (`--zig`, manylinux `auto` / `musllinux_1_2`), macOS x86_64 on
`macos-15-intel`, arm64 on `macos-latest`, Windows on `windows-latest` as a
non-blocking leg until the engine builds there — plus one `maturin sdist`
job; Trusted Publishing is unchanged. The sdist includes
`rust/vendor/sqlite-vec/**/*` explicitly: maturin's sdist walker follows
workspace members and path dependencies but not `[patch.crates-io]` paths,
so without the include the sdist's manifest pointed at a missing crate.

**Why**: every `cairn` run from a PyPI install paid an interpreter start to
`execv` the binary; platforms without an injected binary installed a launcher
that exited "no engine binary"; and one artifact needed two toolchains
(setuptools platform-tag shim + cibuildwheel + per-leg binary copy). With the
sidecars native (D-R01, D-R02–D-R04) the wheel has nothing Python left to
carry. The shape is the one `ruff` ships on PyPI, so pip, pipx and `uv tool`
all install it; `wheel_smoke.sh` probes all three.

**Cost**: maturin is a build-time dependency only (`[build-system]`; no
runtime dependency). Installing the project now means compiling the engine,
so `[tool.uv] package = false` keeps `uv sync` from building it and pytest
reaches the remaining `src/cairn` modules through `pythonpath`; the ci.yml
packaging canary compiles the engine (40-minute budget, rust-cache). Only the
legs whose runner can install their own wheel (x86_64 linux-gnu, both macOS)
run the installed-product smoke; the cross-built aarch64 and musl wheels are
not smoked on their runner. Measured host wheel (macOS arm64): 12,697,546
bytes, payload the 58,088,704-byte stripped engine — far under the 100 MB
limit (NFR-001), so no `lto`/`codegen-units` tuning.

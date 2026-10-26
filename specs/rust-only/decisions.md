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

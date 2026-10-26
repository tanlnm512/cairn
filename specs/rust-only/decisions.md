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

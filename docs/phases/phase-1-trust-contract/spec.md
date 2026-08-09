# Phase 1 — Trust Contract (Specification)

> **Roadmap milestone:** Month 1 — Make the contract real.
> **Goal:** cairn stops *claiming* verifiability and starts *proving* it.
> **Theme:** turn the three promises from the vision into machine-checked
> guarantees.

## Context

cairn's positioning is **verifiable codebase memory** — not "code graph,"
which is commoditized, and not "agent memory," which is ungrounded. The entire
positioning rests on a three-promise verification contract:

1. Every `exact` edge is actually resolved (`target_id IS NOT NULL`).
2. Every symbol in a compass / wiki / memory doc exists in the graph (critic gate).
3. Every answer is re-derivable from local data (LLM never in the query path).

As of v0.6.1, **promise #1's bug is fixed in code (`scip_importer.py:540-542`)
and guarded by an invariant test (`tests/test_invariants.py:213`), but the
BUGS.md entry still asserts both the fix and the test are "not yet
implemented/future" — it is doubly stale.** The invariant test itself seeds
only hand-built rows, not a real SCIP import, so it cannot catch an importer
regression directly. Promise #2 is enforced by the critic in code (`critic.py`)
and has partial test coverage (`tests/test_compass_critic.py`,
`tests/test_core_smoke.py`): the file-ref-rejects and real-refs-pass cases are
covered, but no test asserts the symbol-ref-warns path. The README leads with
"resolution-labeled edges" — the commoditized axis — rather than the contract
that is the actual differentiator.

Phase 1 closes those gaps. This phase adds **no new user-facing capability**.
Its output is *guarantees*: a SCIP-fixture-driven test, a closed bug entry,
critic tests, and a repositioned narrative. (The importer rewrite the
roadmap originally listed is already done — see tech-guide § "verify before
implementing.")

## In-scope items

| ID | Item | Strengthens promise | Done when |
|----|------|---------------------|-----------|
| 1.1 | Close the SCIP importer resolution loop: the importer fix is already in code — add a SCIP-fixture-driven test that exercises it end-to-end, and rewrite the doubly-stale BUGS.md entry (`scip-importer-fake-resolution`). | **#1** | A fixture-driven test (real protobuf `Index`, not hand-seeded SQL) asserts resolved edges are `exact` with `target_id IS NOT NULL` and external refs are `unresolved`; BUGS.md retracts both stale claims. (A full `cairn build` of cairn's own repo via a real SCIP index is deferred — it requires the scip-python indexer installed, an environment dependency too heavy for a unit test.) |
| 1.2 | Close the invariant-test gap: the existing `test_invariant_exact_resolution_has_target_id` only seeds hand-built rows. Pair it with a fixture-driven test that runs the invariant SQL over importer-populated data. (The test already runs in CI's full-suite step; it is intentionally not in `-m core`.) | **#1** | Test runs in CI and fails the build on violation. The hand-seeded invariant (`test_invariants.py`) and the importer-driven invariant (`test_scip_importer.py::test_invariant_exact_implies_target_id_holds_on_importer_data`, which runs the `exact ⟹ target_id IS NOT NULL` SQL over a DB populated by the SCIP importer) both pass. |
| 1.3 | Critic invariant tests: assert the critic's documented behavior — unknown **file refs block** (error), unknown **symbol refs warn** (non-blocking). File-ref-rejects (1.3.1) and real-refs-pass (1.3.3) are already covered in `tests/test_compass_critic.py` + `tests/test_core_smoke.py`; only **symbol-ref-warns (1.3.2)** is missing. | **#2** | Unit test asserts: symbol-ref-not-in-graph → warning present (non-blocking). (file-rejects and real-pass already covered.) |
| 1.4 | Reposition README + `architecture.md` "Why cairn?" to lead with the 3-promise contract, then the 5 layers, then resolution labels as evidence. | *(narrative for all three)* | Front page reads as "verifiable codebase memory," not "the resolution-label graph." |

Item 1.1 is the only item with a live (if doubly-stale) bug entry behind it;
1.2–1.3 close the gaps the audits found; 1.4 makes the contract the headline
so the guarantees are legible. The original roadmap wording ("rewrite the
importer") is already done — see tech-guide § "verify before implementing."

## Out of scope

- **New languages / indexers.** SCIP consumption (C/C++, Rust, C# wiring) is
  Phase 4. Phase 1 only makes the *existing* importer correct.
- **Performance work.** If 1.1's importer rewrite regresses build time, it is
  noted but not optimized here — that is a Phase 2 (benchmarks) concern.
- **Visible-critic UX** (2.2) and benchmark fills (2.1). Phase 2.
- **Memory grounding** (3.1/3.2). Phase 3.
- **Rewriting the resolver tiers.** The tree-sitter 5-tier resolver is
  untouched; this phase concerns only the SCIP path.

## Dependencies

- 1.1 is a prerequisite for the Phase 2/4 SCIP work — a wrong importer makes
  every downstream SCIP claim wrong.
- 1.2 protects 1.1 against regression; ship together.
- 1.3 is independent of 1.1/1.2 (different code path).
- 1.4 is independent and can proceed in parallel with 1.1–1.3.

## Risks

- **1.1 changes imported data.** Existing DBs built against the buggy importer
  will have stale `exact`/NULL rows. Mitigation: a rebuild is already required
  after 0.6.1's portable-paths change; document the rebuild in the changelog.
- **SCIP `enclosing_range` semantics vary across indexers.** scip-swift's
  opaque USRs (BUGS.md, scip.md) are the known hard case. Mitigation: the
  rewrite must handle "target not found in index" gracefully (label
  `unresolved`, not `exact`) rather than special-casing per indexer.
- **1.4 narrative risk.** Repositioning away from "resolution labels" can read
  as retreat. Mitigation: the labels stay as *evidence under* the contract,
  not deleted; the methodology (Phase 2) backfills the proof.

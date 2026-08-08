# Phase 1 — Trust Contract (Plan)

> **Roadmap milestone:** Month 1 — Make the contract real.
> **Read alongside:** [`spec.md`](spec.md) (what & why), [`tech-guide.md`](tech-guide.md) (how it works).

## Sequencing

The four items have a soft dependency: **1.1 → 1.2** (the fixture-driven test
in 1.1 is what lets the 1.2 invariant cover the importer path). 1.3 and 1.4
are independent and can run in parallel with the 1.1/1.2 track. Recommended
order:

```
Week 1:  1.1 (importer rewrite)  ──►  1.2 (invariant test)
Week 2:  1.3 (critic invariant test)  ║  1.4 (narrative repositioning)
Week 3:  integration validation, changelog, release
```

Two parallel tracks minimize blocked time. The 1.1/1.2 track is the
critical path because 1.2's test is what makes 1.1's fix *durable*.

## Tasks

### 1.1 — Close the SCIP importer resolution loop
**Done when:** a pure-SCIP build of cairn's own repo has zero `exact` rows with NULL `target_id`, asserted by a fixture-driven test.

*Status at v0.6.1: the importer already resolves correctly (`scip_importer.py:541-542`). The remaining work is regression coverage and closing the stale BUGS.md entry.*

The sub-tasks (tracked in [`task.md`](task.md) § 1.1):
1. Add `tests/test_scip_importer_resolution.py` with a checked-in minimal `.scip` fixture (caller + target + an external ref) and assert the resulting `edges` rows: resolved call → `exact` with `target_id`; external ref → `unresolved` with NULL.
2. Run `cairn import-scip` on a real scip-java index; spot-check 5 cross-file call edges by hand against source.
3. Close `docs/BUGS.md#scip-importer-fake-resolution` with a "Fixed in <version>; invariant test in `tests/test_invariants.py`; fixture-driven test in `tests/test_scip_importer_resolution.py`" line.
4. Document the resolution rule in `docs/scip.md` § importer behavior (one paragraph).

**Risk:** none for imported data shape (no code change). The only risk is a fixture that drifts from real indexer output — keep the fixture minimal and protobuf-format (the default).

### 1.2 — Close the invariant-test gap
**Done when:** the test runs on every CI build and fails on violation, covering both the hand-seeded and importer-driven paths.

*Status at v0.6.1: `test_invariant_exact_resolution_has_target_id` exists but only seeds hand-built rows; its docstring admits it can't catch the importer's path directly.*

The sub-tasks (tracked in [`task.md`](task.md) § 1.2):
1. Pair the existing hand-seeded invariant with the 1.1 fixture-driven test so the importer path is exercised end-to-end (the fixture test builds a real DB via the importer; the invariant test queries it).
2. Confirm the test runs in `ci.yml` — including under `-m core` (the smoke subset). If `test_invariants.py` is skipped under `-m core`, decide deliberately: either include it (invariants are core) or run the full suite separately.
3. Verify the test fails RED on the pre-fix importer (git-checkout the old importer, run the fixture test, confirm RED) — once, to prove the guard bites.

**Risk:** if the fixture-driven test asserts against a checked-in DB rather than one it builds via the importer, historical `exact`/NULL rows fail it. Build via the importer, do not check in a DB.

### 1.3 — Critic invariant tests
**Done when:** unit tests assert the critic's documented behavior — file refs block, symbol refs warn, real refs pass.

*Status at v0.6.1: the critic is partially covered — `tests/test_compass_critic.py` (`test_hallucinated_file_ref_flagged_as_error`, `test_real_qualified_symbol_ref_not_flagged`) and `tests/test_core_smoke.py` (`test_compass_critic_flags_hallucinated_file_ref`, `test_compass_critic_passes_real_qualified_symbol`) already assert the file-ref-rejects (1.3.1) and real-refs-pass (1.3.3) cases, including under `-m core`. The only remaining gap is 1.3.2 (symbol-ref → warning, non-blocking). The behavior itself is asymmetric (see tech-guide § 1.3).*

The sub-tasks (tracked in [`task.md`](task.md) § 1.3):
1. ~~Add `test_critic_rejects_unknown_file_ref`~~ — already covered in `tests/test_compass_critic.py:134` and `tests/test_core_smoke.py:308`. Skip.
2. Add `test_critic_warns_unknown_symbol_ref` — concept body cites a fake `Symbol(...)` → a warning string is present in `result.warnings` (do NOT assert `passed is False` — symbols warn, they don't block). **This is the only genuinely missing critic test.**
3. ~~Add `test_critic_passes_real_refs`~~ — real-refs-pass half already covered in `tests/test_compass_critic.py:145` and `tests/test_core_smoke.py:319`. Optionally extend to a combined real file+symbol body.
4. Run at the `critic_concept` level only — it is the entry point for compass and wiki (memory does NOT call `critic_concept`; verified by grep of `src/cairn/memory/`).

**Risk:** over-asserting. The implemented contract treats unknown symbols as warnings, not rejections (architecture.md confirms `wiki generate` writes-but-surfaces). If you believe symbols should block, that is a spec change for a later phase — file an issue, do not tighten the critic silently.

### 1.4 — Narrative repositioning
**Done when:** the front page reads as "verifiable codebase memory," not "the resolution-label graph."

The sub-tasks (tracked in [`task.md`](task.md) § 1.4):
1. Rewrite the README "Why cairn?" / opening section to lead with the 3-promise contract.
2. Move the "resolution-labeled edges" content below the contract, framed as evidence for promise #1.
3. Update `docs/architecture.md` § "What cairn is" to mirror the new framing (currently leads with "structural, agent-first").
4. Keep the "Quick start" and install paths unchanged — this is a narrative change, not an API change.
5. Add a one-paragraph "verification contract" anchor that the roadmap and future docs can link to.

**Risk:** narrative drift between README and architecture.md. Mitigation: one
shared "3-promise" block, quoted in both places.

## Definition of done (phase level)

Phase 1 lands when **all four** hold:

1. The `scip-importer-fake-resolution` BUGS.md entry is closed with a
   "fixed in <version>" line pointing to both the invariant test and the new
   fixture-driven importer test.
2. `tests/test_invariants.py` runs green in CI, and the 1.1 fixture-driven
   test was verified RED once against the pre-fix importer (proving the guard
   bites).
3. The critic tests (file-reject, symbol-warn, real-pass) are green at the
   `critic_concept` level.
4. README + architecture.md lead with the verification contract, identically.

## Scope guard (anti-creep)

If mid-phase a task surfaces something tempting but out-of-scope, it goes to
a later phase, not into Phase 1:

| Tempting addition | Belongs in |
|-------------------|------------|
| Wire scip-clang / rust-analyzer into `cairn.json` | Phase 4 |
| Fill the benchmark tables | Phase 2 (2.1) |
| Expose critic verdicts in MCP / `cairn verify` | Phase 2 (2.2) |
| Optimize importer for large indexes | Phase 2 (benchmarks first) |
| Graph-grounded memory recall | Phase 3 (3.1) |

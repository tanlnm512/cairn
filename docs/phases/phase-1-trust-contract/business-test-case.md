# Phase 1 — Trust Contract (Business Test Case)

> **Roadmap milestone:** Month 1 — Make the contract real.
> **Read alongside:** [`spec.md`](spec.md), [`plan.md`](plan.md).
> **Purpose:** prove, in user-facing scenarios, that the phase delivered value.
> Each scenario maps to a "Done when" from the spec and a real command a user
> (or a CI job) can run.

## The user this phase serves

**Persona:** an AI-coding-agent user evaluating cairn's core claim — "every
output is verifiable." They have heard the pitch (verifiable codebase memory,
not just a code graph) and are looking for proof. They are skeptical: every
code-graph tool *claims* precision. What they want from Phase 1 is **a story
they can re-run themselves** that shows cairn's precision is *enforced*, not
advertised.

**Value delivered by Phase 1:** when this user asks "can I trust cairn's
`exact` edges and its compass docs?", the answer is no longer "read our
marketing" — it is "here are the invariants cairn checks on every build, and
here is the contract they enforce."

## Acceptance scenarios

Each scenario carries the spec's "Done when" verbatim, the user-facing command
that demonstrates it, and the pass condition.

### Scenario A — "A SCIP-built graph has no fake exact edges"
**Maps to:** 1.1. **Strengthens:** promise #1.
**Spec "Done when":** a pure-SCIP build of cairn's own repo has zero `exact`
rows with NULL `target_id`.

```
GIVEN a repo with a SCIP index declared in cairn.json (e.g. a Kotlin repo)
 WHEN cairn build runs (consuming the SCIP index)
 THEN the edges table contains zero rows where resolution='exact' AND target_id IS NULL
  AND the build summary reports resolved-edge counts the user can see
```

**Demonstrating command (a user / CI job):**
```bash
sqlite3 ~/.cairn/<workspace-key>/.kg \
  "SELECT COUNT(*) FROM edges WHERE resolution='exact' AND target_id IS NULL"
# expected: 0
```

**Why this matters to the user:** an `exact` edge is what precise-by-default
queries (`get_callers`, `impact_analysis`) trust blindly. A fake `exact` row
silently pollutes blast-radius analysis — the exact failure mode BUGS.md
documents. Zero such rows is the proof that promise #1 holds.

### Scenario B — "The build fails if a code path ever emits a fake exact edge"
**Maps to:** 1.2. **Strengthens:** promise #1.
**Spec "Done when":** the test runs on every CI build and fails the build on
violation.

```
GIVEN the CI pipeline
 WHEN a change introduces a code path that writes resolution='exact' with NULL target_id
 THEN the invariant test (tests/test_invariants.py) fails
  AND the CI build is red
```

**Demonstrating command:**
```bash
pytest tests/test_invariants.py::test_invariant_exact_resolution_has_target_id -v
# green on main; red if the invariant is violated
```

**Why this matters to the user:** a guarantee that holds only until someone
breaks it is not a guarantee. The test makes promise #1 a *continuous*
guarantee — the user does not have to trust the team to remember; CI enforces
it on every change.

### Scenario C — "A compass doc naming a file that doesn't exist is rejected"
**Maps to:** 1.3. **Strengthens:** promise #2.
**Spec "Done when":** a unit test submitting a doc referencing a fake symbol
is rejected by the critic.

```
GIVEN a generated compass/wiki/memory concept whose body cites a file path not in the graph
 WHEN the concept is submitted through the critic
 THEN the critic returns an error (file ref unknown)
  AND the concept is not silently accepted as verified
```

**Demonstrating command (developer-facing, the tests that encode the scenario):**
```bash
# file-ref-rejects (1.3.1) — already green:
pytest tests/test_compass_critic.py -k "hallucinated_file_ref_flagged" -v
pytest tests/test_core_smoke.py -k "compass_critic_flags_hallucinated" -v  # runs under -m core
# symbol-ref-warns (1.3.2) — the test Phase 1 adds:
pytest tests/ -k "critic_warns_unknown_symbol" -v
```

**Why this matters to the user:** this is the critic — the mechanism behind
"every symbol/file in a doc exists in the graph." The file-ref-rejects and
real-refs-pass paths are already tested (`test_compass_critic.py`,
`test_core_smoke.py`); Phase 1 adds the missing symbol-ref-warns test so the
guarantee survives refactors across all three cases. (The implemented contract
is asymmetric — file refs block, symbol refs warn. The test asserts the
documented behavior; tightening symbol refs to block is a deliberate spec
change, not a silent one.)

### Scenario D — "A new user reads the README and understands the contract"
**Maps to:** 1.4. **Strengthens:** narrative for all three promises.
**Spec "Done when":** the front page reads as "verifiable codebase memory,"
not "the resolution-label graph."

```
GIVEN a first-time visitor to the cairn README
 WHEN they read the opening "Why cairn?" section
 THEN they encounter the 3-promise verification contract first
  AND resolution-labeled edges appear as evidence under promise #1, not as the headline
  AND the same contract wording appears in docs/architecture.md (no drift)
```

**Demonstrating check (a reviewer runs this):**
```bash
# The 3-promise contract block should appear in both files, identically.
grep -c "Every .exact. edge is actually resolved" README.md docs/architecture.md
# expect: 1 in each
```

**Why this matters to the user:** the strategic finding (roadmap § findings)
is that resolution labels are commoditized — a 38k-star competitor ships the
same scheme. The contract is the differentiator. If the README leads with the
commodity, the user comparison-shops and leaves. If it leads with the
contract, the user sees the thing only cairn offers.

## Phase exit criteria (all four required)

- [ ] Scenario A passes on a real SCIP-built repo (zero fake `exact` rows).
- [ ] Scenario B passes in CI and was verified RED once against the pre-fix code.
- [ ] Scenario C's three critic tests (file-reject, symbol-warn, real-pass) are green.
- [ ] Scenario D: the 3-promise contract leads README + architecture.md, identically.

## What this phase does NOT prove (so expectations are honest)

- It does **not** prove the *tree-sitter* resolver is correct — only the SCIP
  path and the invariant that guards it. Tree-sitter resolution quality is
  Phase 2's benchmark concern (2.1).
- It does **not** fill benchmark numbers or publish a methodology — that is
  Phase 2 (2.1, 2.4).
- It does **not** make the critic user-visible in the MCP surface — that is
  Phase 2 (2.2).
- It does **not** add memory grounding — that is Phase 3.

Phase 1's job is narrow: make the contract real and provable. Everything else
builds on it.

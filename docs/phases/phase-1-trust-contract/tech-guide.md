# Phase 1 — Trust Contract (Technical Guide)

> **Roadmap milestone:** Month 1 — Make the contract real.
> **Read alongside:** [`spec.md`](spec.md), [`plan.md`](plan.md).
> **Rule:** every file/symbol reference below is real and verified against the
> tree at authoring time. Paths marked `// new` are the only exceptions.

## Important: verify before implementing

When this phase was first scoped, items 1.1 and 1.2 were assumed undone on the
strength of `docs/BUGS.md#scip-importer-fake-resolution`. **The bug was
already fixed in code AND an invariant test exists, but BUGS.md's `Fix:` line
still says "the rewrite (not yet implemented)" and its `Prevention:` line
still calls the invariant test "future" — both are wrong against the current
tree.** The bug entry is doubly stale. Before doing any work, confirm the
current state:

```bash
# 1.1 check — the importer MUST resolve target_id before labeling 'exact'
grep -n "resolution = \"exact\"" src/cairn/parsers/scip_importer.py
# expect to see it guarded by: target_id = target[0] if target else None

# 1.2 check — the invariant test exists
grep -n "test_invariant_exact_resolution_has_target_id" tests/test_invariants.py
```

If both are present (they are at v0.6.1), the *remaining* Phase 1 work is
narrower than the roadmap implies — see each item's "Actual gap" below.

---

## 1.1 — SCIP importer resolution

**Files:** `src/cairn/parsers/scip_importer.py` — the proto path is
`_import_protobuf` (line 409; public entry points `import_scip_file` :749 /
`import_scip_data` :626). The pass-2 loop body runs ~484–599.

**Current state (already correct):**
- A Pass-1 map `defs: {sym_descriptor → (sym_id, file_id, line, col)}` is built
  from definition occurrences (role bit `_SCIP_ROLE_DEFINITION = 1`).
- For each non-definition occurrence, `target = defs.get(sym_descriptor)` →
  `target_id = target[0] if target else None`, then
  `resolution = "exact" if target else "unresolved"` (lines 540–542).
- `source_id` is derived from `_enclosing_range(occ)` with a
  nearest-preceding-definition fallback in line order (lines ~546–557).
- Edges with `source_id IS NULL` (file-level occurrences with no enclosing def)
  are skipped — `edges.source_id` is `NOT NULL` and a NULL would crash the
  import (documented in the inline comment).

**Actual gap (small):**
1. **No SCIP-fixture-driven test.** The importer's resolution logic is
   exercised only through full builds against real `.scip` files; there is no
   checked-in fixture that asserts "this call edge resolves to that target."
   Add `tests/test_scip_importer_resolution.py` with a minimal hand-built
   `.scip` (or protobuf message) containing a known caller→target pair and
   assert the resulting `edges` row.
2. **BUGS.md is doubly stale.** Its `Fix:` line says "the rewrite (not yet
   implemented)" and its `Prevention:` line calls the invariant test "future" —
   but both the fix (`scip_importer.py:540-542`) and the test
   (`tests/test_invariants.py:213`) exist. Rewrite the entry to retract both
   claims and record "fixed in <version>; invariant test in
   `tests/test_invariants.py`; fixture-driven test in
   `tests/test_scip_importer_resolution.py`."

**Pitfalls:**
- `enclosing_range` semantics vary by indexer. scip-swift emits opaque USRs
  (see `docs/scip.md` § "Swift notes") — the `defs` map still keys on
  descriptor, so resolution works, but the symbol *names* won't merge with
  tree-sitter. This is the known per-language-fallback case; do not try to
  "fix" it in the importer.
- SCIP `SymbolRole` has **no call bit** (see `docs/scip.md`). Call sites are
  marked `ReadAccess`. The importer classifies kind from the access mask
  (lines ~571–577): `import` → `import`, access-masked → `reference`, else
  `call`. Do not conflate this with resolution — a `reference`-kind edge can
  still be `resolution='exact'`.
- The merge path (`_merge_ts_into_scip`, ~line 294+) `DELETE`s tree-sitter
  `calls` edges for SCIP-covered symbols and replaces them. A regression here
  shows up as duplicate or missing edges, not as a resolution bug — different
  invariant.

## 1.2 — Graph invariant test

**File:** `tests/test_invariants.py:213` — `test_invariant_exact_resolution_has_target_id`.

**Current state (exists, with a known limitation):**
- The test seeds a `fresh_db` with hand-constructed rows: one `exact` edge
  with a `target_id`, one `unresolved` edge with NULL `target_id`, then
  asserts `SELECT COUNT(*) FROM edges WHERE resolution='exact' AND target_id
  IS NULL` is 0.
- Its own docstring (lines 219–221) admits: *"this can't catch the SCIP
  importer's bug directly (that would need a SCIP import), but it documents
  the invariant and would catch any code path that violates it for data
  inserted through normal paths."*

**Actual gap:**
- The test is a **documentation invariant**, not a **regression guard** for
  the importer. Pair it with the SCIP-fixture test from 1.1 so the importer
  path is actually exercised end-to-end.
- CI coverage is already correct: `tests/test_invariants.py` has no `core`
  marker and runs in ci.yml's full-suite step (the `-m core` smoke subset in
  `test_core_smoke.py:4-5` is intentionally a separate, smaller path). No
  action needed on the `-m core` question — it is settled by design.

**Pitfall:** if the test asserts against a checked-in fixture DB rather than a
freshly built one, historical `exact`/NULL rows from the pre-fix importer will
fail it immediately. The current test correctly builds its own rows — keep it
that way.

## 1.3 — Critic invariant test

**File:** `src/cairn/compass/critic.py` — `critic_concept(concept, conn, llm_judge=None)`.

**Current behavior (read carefully — it is asymmetric):**
- **File references** (backtick-quoted paths): unknown → appended to `errors`
  (line 51). `passed = len(errors) == 0 and ...` (line 95), so an unknown file
  ref **blocks** (rejects the concept).
- **Symbol references** (`Symbol(...)` or backtick Capitalized tokens):
  unknown → appended to `warnings` (line 57). Warnings do **not** block;
  they raise the quality threshold from 0.5 to 0.7 (line 94).
- **Prose-heavy / low-ref** draft: also a warning, not an error (line 62).

**Actual gap (narrow):**
- File-ref-rejects (1.3.1) is already covered — `test_compass_critic.py:134`
  (`test_hallucinated_file_ref_flagged_as_error`) and `test_core_smoke.py:308`
  (`test_compass_critic_flags_hallucinated_file_ref`, runs under `-m core`).
- Real-refs-pass (1.3.3) is already covered — `test_compass_critic.py:145`
  (`test_real_qualified_symbol_ref_not_flagged`) and `test_core_smoke.py:319`.
- **The only genuinely missing test is 1.3.2**: `test_critic_warns_unknown_symbol_ref`
  — a concept body referencing a fake `Symbol(DoesNotExist)` → a warning
  string is present in `result.warnings` (do NOT assert `passed is False`).

**Pitfall — do not over-assert.** The spec says "no doc can name a symbol
absent from the graph," but the *implemented* contract treats unknown symbols
as **warnings, not rejections** (architecture.md § LLM boundary says `wiki
generate` "runs the critic and surfaces its verdict but still writes the
concept"). The test must assert the **documented** behavior. If you believe
unknown symbols should block, that is a spec change for a later phase, not a
test-time decision. File it as an issue; do not silently tighten the critic.

**Coverage note:** compass and wiki flow through `critic_concept`
(`compass/generator.py:303,320`; wiki via `wiki/generator.py:41` inside
`generate_wiki_with_critic` defined at `wiki/generator.py:31`). **Memory does
NOT call `critic_concept`** — verified by grep of `src/cairn/memory/`. Tests
at the `critic_concept` level cover compass + wiki; memory is out of scope
here.

## 1.4 — Narrative repositioning

**Files:** `README.md` (the "Why cairn?" / opening section), `docs/architecture.md` § "What cairn is" (heading at line 7, content from line 9).

**Current state:**
- README leads with "resolution-labeled edges" as the headline
  differentiator ("cairn is the one that tells you whether to trust the
  answer").
- `architecture.md` § "What cairn is" leads with "local, structural,
  agent-first."

**Target state:**
- Lead with the **3-promise verification contract** (from the roadmap vision),
  verbatim in both files.
- Resolution labels move *below* the contract as evidence for promise #1.
- Keep all Quick Start / install content unchanged.

**Pitfall — narrative drift.** The same 3-promise block must appear, quoted,
in README and architecture.md. If they diverge, the contract has two
definitions. Consider an include or a single canonical block in the roadmap
that both docs link to.

**Cross-check:** the AGENTS.md workspace file and `docs/mcp-tools.md` also
describe cairn's value prop. They do not need the full repositioning, but scan
them for claims that contradict the contract (e.g., framing resolution labels
as unique — the strategic-finding section of the roadmap says they no longer
are).

---

## Quick verification commands (run before starting each item)

```bash
# Confirm the importer already resolves before labeling 'exact'
grep -n "resolution = " src/cairn/parsers/scip_importer.py

# Confirm the invariant test exists and what it covers
sed -n '209,275p' tests/test_invariants.py

# Confirm the critic's error-vs-warning split
sed -n '46,65p' src/cairn/compass/critic.py

# Run the existing invariant + critic tests to establish a green baseline
pytest tests/test_invariants.py -v
pytest tests/ -k critic -v
```

If any of these show a state different from what this guide describes, the
guide is wrong (not the code) — update the guide, then the spec.

# Plan: remove-scip-exact-rate

**Spec**: [spec.md](spec.md) | **Created**: 2026-09-11

## Milestones

| Phase | Milestone | Delivers (demoable) | FRs | Depends on |
|-------|-----------|---------------------|-----|------------|
| 1 | Baseline pinned | ds2 exact-share baseline recorded under the pinned counter definition (spec.md:49), self-repo numbers re-confirmed; recipe re-runnable at the pre-change commit; protect-set green. Touches no repo file (throwaway scripts stay in /tmp — build workers spawn-reimport, S-11). | — | — |
| 2 | SCIP removal — code | AC1–AC3 hold: every language indexes via tree-sitter only, a stale `scip` key is ignored as any unknown key, `import-scip` / `[scip]` extra / stub + regen script + indexers orchestrator are gone, and the test suite collects zero SCIP paths. | FR-001, FR-002, FR-003 | Phase 1 |
| 3 | Living-docs cleanup | docs/, README, diagram sources + regenerated PNG twins, and the shipped `agent_integration` skill doc describe the tree-sitter-only pipeline with no SCIP references; CHANGELOG history untouched. | FR-004 | Phase 2 |
| 4 | Parser signal enrichment | All fourteen golden-fixture languages emit the signals their grammars expose — receiver types in the nine gap languages, import aliases where the grammar has them; goldens regenerated; failing-test-first per language (C-02). | FR-007 | Phase 2 |
| 5 | Resolver consumption & tier refinement | Import-aware tier consumes aliases; tier refinement converts previously-ambiguous shapes (import-alias targets, same-name overloads) to exact; tier contract unchanged; abstain preserved. | FR-006, FR-008 | Phase 4 |
| 6 | Before/after proof & closeout | Both corpora re-measured: strictly higher exact share, zero false-exact, wall-time within materiality; exactly one new CHANGELOG entry. | FR-005, FR-009, FR-010 | Phases 3–5 |

Phase 1 deliberately owns no FR row: it is the evidence infrastructure that FR-005's
acceptance (AC4) is proved against; the FR-005 row lives here in Phase 6 where the
strictly-higher share is demonstrated. The baseline must be recorded before any code
change lands — see Dependencies.

## Dependencies

```
P1 baseline -> P2 removal-code -> P3 living-docs --------------------\
                          |                                            >-> P6 proof + closeout
                          +-> P4 parser-signals -> P5 resolver-consume-/
```

- **P1 → P2**: FR-005's "before" side is only provable against pre-change code. The
  self-repo numbers are already pinned in the spec (call-kind share 0.2236 = 192488
  exact / 668424 ambiguous at dc9882b); P1's new measurement is the ds2 equivalent
  under the same definition, using the mandatory copy + `.git`-marker idiom (ds2 has
  no `.git` marker in place — a direct build yields files=0, S-11). Once any Phase-2
  deletion lands, "before" is no longer reproducible from the working tree.
- **P2 → P3**: living docs describe the tree-sitter-only pipeline; editing them before
  the code lands would document a pipeline that does not exist yet.
- **P2 → P4**: shared file `src/cairn/parsers/base.py` — Phase 2 refreshes the Edge
  docstring comment (S-05), Phase 4 may add signal substrate (an Import alias field is
  tech-spec's call); serializing avoids the collision. Enrichment also reasons about a
  parser package that no longer contains scip modules.
- **P4 → P5**: produce → consume. The resolver's `_import_aware_candidates`
  (resolver.py:248) matches `imported_path` tails only; alias bindings stay invisible
  (S-10) until parsers emit them. The receiver-type half needs NO resolver change —
  it feeds the existing Tier 0 dispatch (`resolve_edge`, resolver.py:200-214).
- **P5 → P6** (with P3, P4 closed): the before/after proof needs the final mix; the
  CHANGELOG entry needs every other deliverable landed.
- **P3 ∥ P4**: cross-phase parallel — disjoint files (docs area vs parser area).

## Parallelization map

Parallel is the default; each `[P]` group lists the files that prove disjointness.

- **[P] P2 wave 1** (import-site removal, comment refreshes, test deletions):
  - Builder hybrid path + crash-window re-anchor: `src/cairn/graph/builder.py`
    (scip_languages resolution/autogen 410-453, scan-event comment 406-408, import +
    merge + rollback 558-599, revert-to-pure-scip 601-653, `_language_extensions`
    helper whose only consumer is the revert block, summary folding 673-701) — paired
    in-task with the surviving-test rewords that describe the hook ordering:
    `tests/test_workflow_audit_fixes.py` (marker test at :313 patches `_resolve_all`;
    logic survives, wording re-anchored), `tests/test_doctor.py` (:411 comment),
    `tests/test_invariants.py` (:18-19, :217 docstrings — the invariant test itself is
    protect-set, only the stale BUGS.md citation drops, S-09).
  - CLI surface: `src/cairn/cli/hooks_viz.py` (`import-scip` command 86-99),
    `src/cairn/cli/core.py` (config echo 241-251), `src/cairn/cli/system.py`
    (docstring :1).
  - Peripheral comment refreshes: `src/cairn/graph/schema.py` (:449-450 provenance
    comment; the `symbols.source` column stays), `src/cairn/graph/traversal.py` (:15),
    `src/cairn/parsers/base.py` (:45), `src/cairn/knowledge/ingest/identity.py` (:33 —
    comment reworded, `_HASH_LEN` stays). `src/cairn/graph/incremental.py` needs zero
    edits (no scip code, S-05).
  - Test deletions: `tests/test_scip_importer.py`, `tests/test_scip_indexers.py`,
    `tests/test_build_scip_hybrid.py`, `tests/test_scip_incremental.py` (wholesale);
    orphaned scaffolding stripped from `tests/test_parser_audit_fixes.py` (import :40,
    pytestmark + helpers 275-296, two test classes), `tests/test_audit_remediation.py`
    (test :402 + imports :405), `tests/test_big_tech_improvements.py` (test :11, import
    :5 — F401 gate — and stale docstring :18-19, S-09).
- **[P] P2 wave 2** (after wave 1 — consumes the removed import sites):
  - Module + packaging deletion: `src/cairn/parsers/scip_importer.py`,
    `src/cairn/parsers/scip_indexers.py`, `src/cairn/parsers/_scip_pb2.py`,
    `scripts/regen_scip_pb2.sh`, `pyproject.toml` (`scip` extra 126-127 + comment
    block 119-125; `grpcio-tools` dev dep :87 exists solely for stub regen) + `uv.lock`
    relock.
  - Config key: `src/cairn/graph/config.py` (field :54, docstring :43-45, `is_default`
    :62, `_SCIP_KEY` :71, parse line :107, constructor :113 — a 4-line delta, S-03).
  Both are file-disjoint; both require wave 1 first: `scip_importer`'s only non-test
  importers are builder.py:561 and hooks_viz.py:95; `scip_indexers`'s only importer is
  builder.py:430 (S-01); the config field is only removable after builder's
  `if cfg.scip:` consumer (:419) is gone; test deletions precede module deletion so
  collection never sees a dangling import.
- **[P] P3 ∥ P4** (cross-phase): docs area — `docs/indexing.md` (§8 + renumber),
  `docs/configuration.md` (:16, :211), `docs/cli-reference.md` (:103),
  `docs/architecture.md` (:84), `README.md` (:103-109, :318), diagram sources
  `docs/diagrams/indexing-pipeline.{html,svg,-dark.html}` + the four c4 label files
  (`c4.html`, `c4-dark.html`, `c4-context.html`, `c4-context-dark.html` — this
  session's grep; c4-components/c4-containers twins are clean) + six PNG twins
  regenerated, `src/cairn/agent_integration/skill/references/tools.md` (:9) — versus
  parser area `src/cairn/parsers/*.py` + `tests/fixtures/golden/`. No shared file.
- **[P] P3 internal**: markdown docs ∥ diagram edits + PNG regen — disjoint files.
- **[P] P4 per-language**: one task per language (parser module + its golden fixture +
  its failing-first test), mutually file-disjoint. Two pairs share a module and are
  single tasks: c+cpp (`c_family.py`), typescript+javascript (`typescript.py`). Any
  shared substrate change in `src/cairn/parsers/base.py` (signal fields) lands as its
  own task before the per-language wave.
- **Strictly ordered** (the exceptions that justify serialization):
  - P1 → P2 → P4 → P5 → P6 along the spine above (baseline reproducibility; base.py
    collision; produce → consume; proof last).
  - P2 wave 1 → wave 2 (import-site → module deletion, above).
  - Within P5: resolver-core work is single-file (`src/cairn/graph/resolver.py`) — one
    owner, internally sequential; its new test files are separate but chain after the
    behavior they pin (C-02 failing-test-first: alias-resolution and overload tests are
    authored failing, then made green by the tier change).
  - CHANGELOG entry lands in P6 only — writing it earlier claims unbuilt work.

## Checkpoints

Standing gate, every phase — protect-set green (S-13; 39 passed at baseline):

```
CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_type_tier.py tests/test_resolver_type_scoped_fallback.py tests/test_reindex_resolution_invariant.py tests/test_pointer_ambiguity.py tests/test_search_edge_expansion.py tests/test_invariants.py -q
```

- **After Phase 1**: ds2 baseline numbers recorded under the spec.md:49 definition
  (copy `benchmarks/datasource/ds2/second-corpus/attrs-26.1.0` to a scratch dir, add a
  `.git` marker, build, then SQL over the built edges table for resolver-resolved
  `call`/`references` kinds); self-repo call-kind share re-confirmed at 0.2236;
  standing gate green. Recipe per S-11; no script lands in the repo.
- **After Phase 2**: `rg -n 'scip' src/cairn/graph/builder.py` → 0;
  `rg -n 'scip|_SCIP_KEY' src/cairn/graph/config.py` → 0;
  `rg -n -i 'scip' src/cairn/cli/` → 0;
  `rg -l 'scip' src/cairn/parsers/ scripts/ pyproject.toml` → only
  `scripts/fetch_t3_corpus.py` ("discipline" false positive, S-01);
  `rg -n -i 'scip' src/cairn/graph/schema.py src/cairn/graph/traversal.py src/cairn/parsers/base.py src/cairn/knowledge/ingest/identity.py src/cairn/graph/incremental.py` → 0;
  `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_workflow_audit_fixes.py tests/test_doctor.py tests/test_invariants.py tests/test_parser_audit_fixes.py tests/test_audit_remediation.py tests/test_big_tech_improvements.py -q`
  green; the four scip test files no longer exist and `pytest --collect-only` shows no
  scip path (AC3); a scratch-workspace `cairn build` succeeds both with and without a
  stale `scip` key in cairn.json (AC1/AC2).
- **After Phase 3**: `rg -n -i 'scip' docs/ README.md src/cairn/agent_integration/`
  → 0 (CHANGELOG.md excluded by the living-docs ruling, spec.md:65); the six PNG twins
  regenerated from the edited diagram sources (grep cannot see into PNGs, S-08).
- **After Phase 4**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_golden_parsers.py -q`
  green with goldens regenerated via `tests/fixtures/golden/regenerate.py`;
  `rg -n 'receiver_type=' src/cairn/parsers/` shows emitters beyond the baseline five
  (go/java/kotlin/php/ruby were the only assignment sites at dc9882b, S-10);
  per-language failing-first tests green; standing gate green.
- **After Phase 5**: standing gate green (tier-contract pins for the preserved
  ordering); AC6 shape tests green — import-alias targets and same-name overloads
  resolve exact with regression tests (the resolution-ground-truth vehicle, S-12);
  `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_type_tier.py tests/test_resolver_type_scoped_fallback.py -q`.
- **After Phase 6**: `uv run python benchmarks/datasource/ds2/verify_dataset.py` →
  558/558, exit 0; `uv run python scripts/verify_ground_truth.py` → exit 0 (AC5 zero
  regressions); the Phase-1 recipe re-run on both corpora shows a strictly higher
  exact share under the pinned definition (AC4) with the exact-implies-target_id
  invariant green throughout (zero false-exact); resolution-phase wall-time from the
  `build_runs` table within the 15% advisory materiality vs the 198.7 s self-repo
  baseline (FR-010); CHANGELOG.md carries exactly one new entry, history untouched.

## Risks & mitigations

- Risk: heuristic gains smuggle in false-exact conversions → every P4/P5 task is
  failing-test-first (C-02) and bound by the FR-009 abstain rule; backstops:
  `tests/test_invariants.py` exact-implies-target_id invariant,
  `tests/test_type_tier.py` abstain pin, and the AC5 ground-truth gates in Phase 6.
- Risk: crash-window marker breaks when the SCIP hook disappears → the re-anchoring of
  `_clear_repo_build_state` to the new last-write point and the reword of its pinning
  test land in ONE task (S-02 comment 655-662; S-07 test at :313 patches `_resolve_all`
  and stays logically valid).
- Risk: baseline drift — one baseline DB yields three plausible shares (0.3737
  SQL-all-kinds, 0.2237 resolve-phase, 0.2236 call-kind) → the compared counter is
  pinned verbatim in spec.md:49 and both Phase-1 numbers are recorded under it before
  any code lands.
- Risk: removal degrades exact rate where SCIP genuinely worked → the measurement
  corpus was never SCIP-indexed (`summary['scip'] = None` at baseline, S-11), so
  removal cannot move this baseline; the net effect is re-proven in Phase 6 regardless.
- Risk: golden churn masks parser regressions → goldens regenerate per language and
  the failing-first tests are authored before the parser change they pin.
- Risk: richer signals regress resolution wall-time → Phase-6 FR-010 gate
  (`build_runs` phase timings vs the 198.7 s baseline; 15% advisory threshold, S-12).
- Gaps this plan does not resolve (owned elsewhere, per guardrails):
  - The imports-table WRITE path for an alias column is unmapped — survey covers the
    read side only (`build_import_index`, resolver.py:39); the storage decision
    (additive-ALTER precedent near schema.py:449-450; spec.md:63 assumes no DB
    migration) is tech-spec's to pin.
  - The PNG regeneration workflow is unpinned (S-08 PARTIAL residue) — tech-spec or
    the Phase-3 task pins the renderer used for the twins.

## Delivery

Branch `refactor/remove-scip-exact-rate` (spec.md:9). Single end-of-plan commit —
code + docs together, never per task; conventional-commit title in the `refactor`
scope; `uv run pre-commit run --all-files` green before committing; push the feature
branch, open the PR with the audit checklist filled, and watch CI through the test
matrix and ds2-seal (C-01).

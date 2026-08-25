# Plan: kotlin-grammar-fwcd

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-25
Milestones and ordering only — task-level detail lives in [task.md](task.md).
Every code citation below is verbatim from [survey.md](survey.md) or a
`cairn`/grep run in the planning session (2026-08-25, graph built at
8,614 nodes / 37,344 edges via `cairn init && cairn update`).

## Milestones
<!-- Each milestone = a phase in task.md. -->
| Phase | Milestone | Delivers (demoable) | FRs | Depends on |
|-------|-----------|---------------------|-----|------------|
| 1 | Vendored grammar builds & loads (no cutover) | fwcd's generated parser is vendored in-tree, regenerates against the tree-sitter 0.26 ABI, compiles during dev builds, and is loadable through an additive registry seam; the default `"kotlin"` path is untouched, so every existing test stays green | — (vendoring infrastructure for the cutover) | — |
| 2 | Cutover + extraction parity | `"kotlin"` loads from the vendored grammar by default and the KotlinParser walk is ported to the new node shapes; the full existing corpus (golden, operator-invoke, audit-fixes, scip-hybrid, self-demo) passes with `expected.json` byte-identical | FR-001, FR-002, FR-003 | Phase 1 |
| 3 | Modern syntax parses ERROR-free | A new fixture set (KEEP-0438 destructuring, `when` guards, multi-dollar interpolation, trailing commas) parses with zero ERROR/MISSING nodes, enforced by a new scan test — the machinery survey item 3 shows does not exist | FR-004 | Phase 1 |
| 4 | Toolchain-free wheels, dep removal, release hygiene | Platform wheels carry the compiled Kotlin extension; the `tree-sitter-kotlin==1.1.0` dependency is gone repo-wide; the scip-java hybrid path is verified end-to-end on the new grammar; changelog entries land under `[Unreleased]` | FR-005, FR-006, FR-007, FR-008 | Phase 1 (wheel-matrix work); Phases 2+3 (final gate) |

Phase 1 exists to retire the spec's riskiest assumption first — "fwcd's
generated parser regenerates cleanly against the tree-sitter 0.26 ABI"
(spec Assumptions; fwcd's own packaging pins `tree-sitter~=0.22`) — before
any port or CI work spends effort on top of it. It is deliberately additive:
no behavior change, so a PR-per-feature merge keeps main green.

Phases 2 and 3 are separable because FR-004 is parse-level only (spec Scope
puts semantic extraction of the new constructs out of scope). Phase 3's
scan needs a loadable fwcd grammar but not the ported extractor.

## Dependencies
What blocks what, grounded in this session's `cairn` queries:

- **The registry seam is deep-impact and must flip atomically with the
  port.** `cairn impact _load_language_module` (session): total impacted
  128, chain `_load_language_capsule` → `get_parser` (both
  `src/cairn/parsers/_registry.py`) → `_parse_file_worker` → `_parse_all` →
  `_build_graph_impl` → `build_graph` (`src/cairn/graph/builder.py`), with
  71 test/build sites at depth 6. Flipping the seam without the kotlin.py
  port turns the 13 kotlin-marked tests and the golden snapshot red
  (survey items 2, 7) — so the flip is Phase 2, never Phase 1.
- **Phase 1 → Phase 2**: the port targets the new grammar's actual node
  shapes (the 7 mismatches in spec FR-002/FR-003) and consumes the loader
  seam Phase 1 lands (`_SPECIAL_LOADERS` precedent at `_registry.py:43-52`;
  mapping at `_registry.py:119-138` — survey Supporting evidence).
- **Phase 1 → Phase 3**: the ERROR/MISSING scan parses with the fwcd
  grammar; it can run against the Phase-1 seam pre-cutover. No dependency
  on Phase 2 (extraction is not asserted).
- **Phase 1 → Phase 4 (wheel matrix)**: cibuildwheel packages the vendored
  extension and build config Phase 1 lands. Nothing in Phases 2-3 is
  needed to build and matrix-test the wheels.
- **Phase 2 → Phase 4 (dep removal + final gate)**: FR-006 removes
  `pyproject.toml:30 "tree-sitter-kotlin==1.1.0"` (survey item 5) — safe
  only after the cutover proves the package unused; the removal also
  shares `pyproject.toml` with the wheel work (one PR avoids the conflict).
- **SCIP is a gate, not a work area**: `cairn callers
  _merge_scip_defs_into_tree_sitter` (session) → sole caller
  `_import_protobuf` (`src/cairn/parsers/scip_importer.py:631`); the merge
  couples to the grammar only through DB rows (survey item 6 gap note), so
  FR-007 needs no new code — it is a Phase 4 exit condition whose tests
  already run from Phase 2 onward.
- **KotlinParser is contained**: sole construction site is the
  `{"kotlin": KotlinParser}` map at `src/cairn/graph/builder.py:52`
  (session grep); all port work lands in `src/cairn/parsers/kotlin.py`
  (683 lines, every node-type literal mapped in survey item 2).

Graph:

```
P1 vendor+seam ──┬─→ P2 cutover+parity ──┬─→ P4 gate (full suite, dep removal,
                 │                       │     changelog, hybrid e2e, wheels land)
                 ├─→ P3 error-gate ──────┘
                 └─→ P4a wheel matrix (starts early, lands with P4)
```

## Parallelization map
<!-- Which work areas are independent (different files/subsystems, no shared
     state) and can be developed concurrently, and which are strictly
     sequential. The task-breaker turns this into [P] markers per task. -->
Parallel is the default; every serialization below names its proof.

- **Independent — after Phase 1 merges, three streams run concurrently:**
  - **PARITY** (Phase 2): touches `src/cairn/parsers/kotlin.py` only.
    Disjointness: survey item 2 inventories every node-type literal in that
    one file; no other stream edits `src/`.
  - **ERROR-GATE** (Phase 3): touches new files only — modern-syntax
    fixtures plus a new test module. The existing golden corpus must NOT
    move (`tests/fixtures/golden/kotlin/expected.json` stays
    byte-identical — that is the parity definition), so no overlap with
    PARITY's file set. Parse-level assertions only (FR-004 wording).
  - **WHEELS** (Phase 4a): touches `.github/workflows/release.yml` (today
    `uv build` on ubuntu-latest producing a py3-none-any wheel — survey
    item 4), `.github/workflows/ci.yml` (build job at ci.yml:269-272),
    `pyproject.toml` build sections, and a possible `setup.py`. Zero
    overlap with `kotlin.py`, `tests/fixtures/`, or new test files.
  - Disjointness is checkable by file list; the only shared resource is
    the pytest run itself.
- **Strictly ordered:**
  - **Phase 1 → Phase 2**: Phase 2 consumes what Phase 1 produces (the
    loadable vendored grammar + seam) AND the seam flip must land in the
    same PR as the port or main's 13 kotlin tests go red mid-sequence —
    the 128-site impact chain above is why the flip is never standalone.
  - **Phase 1 → Phase 4a**: the wheel matrix packages the extension and
    build wiring Phase 1 lands; before that exists there is nothing to
    matrix-build.
  - **Phases 2+4a → Phase 4 finalization**: FR-006's removal edits the same
    `pyproject.toml` the wheel work owns, and must post-date the cutover;
    the final gate needs Phases 2 and 3 merged.
  - **Changelog (FR-008) last**: `CHANGELOG.md` `## [Unreleased]`
    (CHANGELOG.md:13, survey item 8) records what actually shipped.
- **Merge order**: P1 → P2 → P3 → P4. CI-wise P2 and P3 are order-independent
  (both green alone); P2-first is the natural demo order (parity before
  new-syntax fanfare).

## Checkpoints
<!-- Exit condition per phase; verify before starting the next. -->
- **After Phase 1**: the vendored grammar loads on tree-sitter 0.26 and the
  default path is untouched. Verify: a direct probe of the new seam (the
  additive loader entry, exact symbol per tech-spec) parses a multi-dollar
  interpolation snippet the old grammar cannot and reports `has_error` →
  `False` — a discriminating probe, since `get_parser("kotlin")` still
  serves the old grammar until Phase 2; default-path suites unchanged:
  `uv run --extra dev --extra test pytest -m core -q` → 26 passed, 2105
  deselected and `uv run --extra dev --extra test pytest tests -q -k kotlin`
  → 13 passed, 2118 deselected (both commands and counts from survey
  item 7).
- **After Phase 2**: cutover complete with zero extraction drift. Verify:
  `uv run --extra dev --extra test pytest tests/test_build_scip_hybrid.py tests/test_golden_parsers.py tests/test_kotlin_operator_invoke.py tests/test_self_demo.py -q`
  → 30 passed (survey item 6); `pytest tests -q -k kotlin` → 13 passed;
  `git diff --exit-code tests/fixtures/golden/kotlin/` → clean (the golden
  did not move).
- **After Phase 3**: modern syntax is mechanically ERROR-free. Verify: the
  new scan test passes and reports zero nodes of type `ERROR`/`MISSING`
  across every fixture (KEEP-0438 destructuring, `when` guards,
  multi-dollar interpolation, trailing commas — the FR-004 list);
  `uv run --extra dev --extra test pytest tests -q -k kotlin` grows beyond
  13 and stays green.
- **After Phase 4**: ship-ready. Verify: `grep -rn "tree-sitter-kotlin"
  pyproject.toml uv.lock NOTICE src/` → no hits (inventory of removal sites:
  survey item 5); fresh `uv sync && uv pip show tree-sitter-kotlin` → not
  installed; full suite `uv run --extra dev --extra test pytest -q` green
  (the CI path, README.md:333-334 per survey item 7); scip-java hybrid
  demo on the new grammar (FR-007 — the 30-passed command re-run plus full
  suite); `CHANGELOG.md` `## [Unreleased]` carries Added/Changed entries;
  wheel-matrix dry run produces artifacts for macOS arm64/x64, manylinux
  x86_64/aarch64, musllinux, and Windows (FR-005 list), and the CI build
  job's clean-runner import check (`python -c "import cairn; print('ok')'`,
  ci.yml:269-272 pattern) passes without a C toolchain.

## Risks & mitigations
- Risk: the generated parser does not regenerate cleanly against the
  tree-sitter 0.26 ABI (spec assumption; fwcd pins `tree-sitter~=0.22`) →
  Phase 1 exists to surface this before any port or CI work; nothing else
  starts until its checkpoint passes.
- Risk: node-type drift beyond the 7 researched mismatches (spec risk) →
  Phase 2's golden-JSON equality and operator-invoke shape table are
  exactly the mechanical catchers survey item 3 identifies as the only
  things that notice grammar regressions today.
- Risk: hidden-rule differences silently drop edges (`_class_parameters`
  etc., spec risk) → edge-level assertions live in the operator-invoke and
  scip-hybrid suites (survey items 6, 7); Phase 4's gate re-runs them
  end-to-end.
- Risk: mid-sequence merge turns main red (cutover breaks the corpus
  before the port lands) → the seam flip is bound into Phase 2's PR;
  Phase 1 is purely additive.
- Risk: in-tree compilation breaks the pure-python install story (spec
  risk) → the cibuildwheel matrix is Phase 4a, developable in parallel
  from Phase 1 onward; sdist keeps source builds possible where a toolchain
  exists.
- Risk: CI gates reject the swap (pip-audit/bandit/mypy/dependency-review —
  team context) → vendored C sources and the dependency removal go through
  the same PR audit checklist as everything else; no gate bypass.

## Delivery
Branch `feat/kotlin-grammar-fwcd` (spec header). One PR per phase (4 PRs)
on the solo PR-per-feature cadence, merged in the order above; code +
tests + changelog entry travel together in each PR. Conventional commit
titles (`fix:`, `feat:`, `build:`, `chore:` as fits), pre-commit
`--all-files` green, PR audit checklist filled — per
`docs/contribution-workflow.md` (AGENTS.md mandatory workflow). Phase 4's
PR carries the changelog entries (FR-008); the version bump itself happens
at tag time per the repo's release discipline (survey item 8).

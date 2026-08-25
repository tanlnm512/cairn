# Tasks: kotlin-grammar-fwcd

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Tech spec**: [tech-spec.md](tech-spec.md)
Status reflects code state per [survey.md](survey.md), not intent.
**Before-audit**: passed @ 48d65cb0359419d9b343503fe2531dd6a6d04509 (2026-08-25 — preconditions/chains verified; baseline green 26 core + 13 kotlin; clean tree post-docs-commit; 0 pre-done tasks; branch cut; constitution C-01..C-05 judged compliant)
**Branch**: `feat/kotlin-grammar-fwcd`

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
| Phase | Total | Done |
|-------|-------|------|
| 1     | 2     | 0    |
| 2     | 4     | 0    |
| 3     | 2     | 0    |
| 4     | 4     | 0    |
| **Σ** | 12    | 0    |

## Phase 1: Vendored grammar builds & loads, no cutover (FR-001)
<!-- Checkpoint (plan, After Phase 1): a direct probe of the new seam parses a multi-dollar interpolation snippet the old grammar cannot and reports has_error -> False; default-path suites unchanged: pytest -m core -q -> 26 passed, 2105 deselected; pytest tests -q -k kotlin -> 13 passed, 2118 deselected. -->

- [ ] T001 [P] Vendor the pinned fwcd generated parser — add the new tree `vendor/tree-sitter-kotlin/` holding fwcd's generated output only (`src/parser.c`, `src/scanner.c`, `src/tree_sitter/` headers, `src/grammar.json`, `src/node-types.json`) plus its MIT `LICENSE`, at a pinned fwcd tag; add a NOTICE "Vendored content (Kotlin grammar)" block recording origin URL, pinned commit, license, and in-tree path — mirroring the yarl precedent at NOTICE:84-100 (FR-001; D-001, D-004, D-009)
  - Generated output only: never vendor fwcd's own packaging — root `setup.py`, `pyproject.toml`, `bindings/`, `Cargo.toml`, `package.json` — their pyproject pins `tree-sitter~=0.22` vs cairn's `tree-sitter==0.26.0` (pyproject.toml:29, survey item 1), and the PyPI name `tree-sitter-kotlin` is owned by the dormant build (tech-spec § Solution).
  - `src/grammar.json` + `src/node-types.json` ride along as regeneration input and the Phase-2 port-verification reference; the NOTICE pin is the reference point for D-004's regeneration procedure on future grammar bumps.
  - Proof anchor: pinned commit recorded in the NOTICE block; vendored file list matches the tech-spec's fetched fwcd `src/` listing (parser.c, scanner.c, grammar.json, node-types.json, folder tree_sitter).

- [ ] T002 Build the abi3 extension `cairn._tree_sitter_kotlin` — new `setup.py` declaring `Extension("cairn._tree_sitter_kotlin", sources=[vendored parser.c, scanner.c, cairn-owned shim], py_limited_api=True, define_macros=[Py_LIMITED_API=0x030A0000, PY_SSIZEET_CLEAN], include_dirs=["vendor/tree-sitter-kotlin/src"])` so wheels tag cp310-abi3; new shim `src/cairn/_tree_sitter_kotlin_binding.c` (~40 lines) whose `language()` returns the PyCapsule; new `MANIFEST.in` (`recursive-include vendor *.c *.h *.json LICENSE`) so the sdist stays source-buildable; bump `pyproject.toml:2` `setuptools>=61.0` → `>=64.0` so PEP 660 editable installs compile the extension in-place (after T001) (FR-001, FR-005; D-003)
  - Serial reason: consumes T001's output — the exact files `vendor/tree-sitter-kotlin/src/parser.c`, `vendor/tree-sitter-kotlin/src/scanner.c`, and the headers under `vendor/tree-sitter-kotlin/src/tree_sitter/` (parser.h) are the extension's sources.
  - The shim exposes exactly one function, `language() -> PyCapsule` — the interface the default loader already calls (`return lang_mod.language()`, `_registry.py:66`, survey item 1). No `_registry.py` edit in this phase: `"kotlin": "tree_sitter_kotlin"` at `_registry.py:121` stays until Phase 2 — the seam is additive, default path untouched.
  - Verify before implementing (tech-spec area 1): `uv run python -c "import tree_sitter; print(tree_sitter.__version__)"` → `0.26.0`. After: `uv run python -c "import cairn._tree_sitter_kotlin as m; from tree_sitter import Language, Parser; p = Parser(Language(m.language())); assert not p.parse(b'class Foo').root_node.has_error"` plus the Phase-1 discriminating probe — a multi-dollar interpolation snippet the old grammar cannot parse reports `has_error` → `False` on the new seam (plan, After Phase 1).
  - ABI pitfall (D-004): the 0.26 runtime accepts language versions 13-15 (`tree_sitter.LANGUAGE_VERSION` → 15, `tree_sitter.MIN_COMPATIBLE_LANGUAGE_VERSION` → 13); if fwcd's shipped `parser.c` was generated outside that range, `Language()` fails at load — regenerate per D-004. `scanner.c` is plain C, so MSVC/musl builds are fine.
  - Default-path proof (unchanged baselines, survey item 7): `uv run --extra dev --extra test pytest -m core -q` → 26 passed, 2105 deselected; `uv run --extra dev --extra test pytest tests -q -k kotlin` → 13 passed, 2118 deselected.

## Phase 2: Atomic cutover + extraction parity (FR-001, FR-002, FR-003)
<!-- Checkpoint (plan, After Phase 2): the 30-passed command green; pytest tests -q -k kotlin -> 13 passed; git diff --exit-code tests/fixtures/golden/kotlin/ clean. T003-T006 merge as ONE PR on feat/kotlin-grammar-fwcd — the seam flip is never standalone. -->

- [ ] T003 Port the prescan + type-decl shapes — `src/cairn/parsers/kotlin.py`: in `_prescan_field_types` (kotlin.py:86-111) add `"enum_class_body"` alongside `"class_body"` in the prescan branch (kotlin.py:102) so enum class-body properties still reach `_field_types` (the operator-invoke rewrites depend on it); under fwcd `primary_constructor` children are `class_parameter`s directly — match `class_parameter` at that level, dropping the `class_parameters` container hop (kotlin.py:96) (after T002) (FR-002, FR-003; port items 1-2 of the tech-spec table)
  - Serial reasons: consumes T002's loadable `cairn._tree_sitter_kotlin` (shape reference: `vendor/tree-sitter-kotlin/src/node-types.json`); same file as T004/T005 — serialize commits on the Phase-2 branch.
  - `_classify_type_decl` (kotlin.py:192-215) already returns interface/enum/class from `class_declaration` keyword children (kotlin.py:198-207) — no edit; `interface_declaration`/`enum_declaration` entries in TYPE_DECL_NODES (kotlin.py:28-29) become inert (D-006: the port switches outright, no compatibility branches).
  - Pitfall (tech-spec area 3): a missed prescan hop yields fewer `_field_types` rows and quietly degrades the operator-invoke rewrite tests rather than failing loudly — run the full `-k kotlin` set after this task.

- [ ] T004 Port the inheritance walk — `src/cairn/parsers/kotlin.py`: `_parse_inheritance` (kotlin.py:381) currently matches `child.type in ("delegation_specifiers", "inheritance_specifier")`; under fwcd the plural `delegation_specifiers` is hidden — collect direct-child `delegation_specifier` nodes; `_collect_modifiers` (kotlin.py:217-229) descends into an `inheritance_modifier` child container so `open`/`sealed`/`abstract` survive in symbol modifiers (sample.kt's "open class" exercises this, survey item 7) (after T002; same file as T003/T005 — serialize on the Phase-2 branch) (FR-003; port items 3-4)
  - `_collect_inheritance_targets` (kotlin.py:399-424) already recurses `constructor_invocation` → extends and `user_type` → implements — no edit expected.

- [ ] T005 Port `_parse_import` — `src/cairn/parsers/kotlin.py:361`: accept `identifier` as the preferred child where the walk matches `child.type == "qualified_identifier"` (fwcd renames `qualified_identifier` → `identifier`); the node-text fallback (strip the `import ` prefix, drop ` as Alias`, kotlin.py:355-358) already covers path splits. No-edit verifications: the type triples at kotlin.py:485/:502/:525 keep matching via `user_type`/`nullable_type` (`type_reference` becomes inert) and kotlin.py:124 already accepts both `("import", "import_header")` (after T002; same file as T003/T004 — serialize on the Phase-2 branch) (FR-003; port items 5-7)

- [ ] T006 Flip the registry seam atomically — `src/cairn/parsers/_registry.py:121`: `"kotlin": "tree_sitter_kotlin"` → `"kotlin": "cairn._tree_sitter_kotlin"`; update the module docstring at `_registry.py:4-7` (it names tree-sitter-kotlin among "per-language wheels"). Lands in the SAME PR as T003-T005 (after T003, T004, T005) (FR-001, FR-002, FR-003)
  - Serial reason (plan Dependencies): flipping the seam without the kotlin.py port turns the 13 kotlin-marked tests and the golden snapshot red mid-sequence — `cairn impact _load_language_module` → total impacted 128, chain `_load_language_capsule` → `get_parser` → `_parse_file_worker` → `_parse_all` → `_build_graph_impl` → `build_graph` (all `src/cairn/parsers/_registry.py` + `src/cairn/graph/builder.py`), with 71 test/build sites at depth 6. The flip is bound into Phase 2's PR, never standalone.
  - No `_SPECIAL_LOADERS` entry and no fallback loader (D-002, FR-006): the vendored module exposes plain `language()`, which the default loader path already calls (`_registry.py:66`, survey item 1).
  - Gate (proof, survey item 6): `uv run --extra dev --extra test pytest tests/test_build_scip_hybrid.py tests/test_golden_parsers.py tests/test_kotlin_operator_invoke.py tests/test_self_demo.py -q` → 30 passed; `uv run --extra dev --extra test pytest tests -q -k kotlin` → 13 passed; `git diff --exit-code tests/fixtures/golden/kotlin/` → clean — `tests/fixtures/golden/kotlin/expected.json` is the parity oracle and must NOT be regenerated to "fix" a diff (a diff there is a port bug; regenerate.py:24 `"kotlin": (KotlinParser, "sample.kt")` exists for deliberate regens only). Also run tests/test_core_smoke.py:54 `test_build_graph_resolves_usecase_bare_call` (tech-spec area 3 pitfall).

## Phase 3: Modern syntax parses ERROR-free (FR-004)
<!-- Checkpoint (plan, After Phase 3): the new scan test passes reporting zero nodes of type ERROR/MISSING across every fixture; pytest tests -q -k kotlin grows beyond 13 and stays green. -->

- [ ] T007 [P] Add the modern-syntax fixture set — new `tests/fixtures/kotlin/modern/` holding one .kt fixture per FR-004 construct: KEEP-0438 destructuring, `when` guard conditions, multi-dollar string interpolation, trailing commas (FR-004)
  - Open with the survey's gap named (survey item 3, PARTIAL): "no code anywhere inspects tree.root_node for ERROR/MISSING nodes; parse_errors tracks Python-level exceptions only" — the ERROR-scan machinery does not exist yet.
  - Parallel by construction (plan Parallelization map, ERROR-GATE stream): new files only, and `tests/fixtures/golden/kotlin/expected.json` stays byte-identical (that is the parity definition), so no overlap with Phase 2's file set.
  - Parse-level fixtures only — semantic extraction of the new constructs is spec Scope-out.

- [ ] T008 Add the ERROR/MISSING scan test — new test module `tests/test_kotlin_modern_syntax.py` that parses each fixture and asserts `not tree.root_node.has_error` plus zero MISSING nodes, enumerating the fixture directory so every fixture is covered mechanically (after T002, T007) (FR-004; D-005)
  - Consumes from T007: the `.kt` fixture files under `tests/fixtures/kotlin/modern/`, one per FR-004 construct (the directory itself is the interface).
  - Parse via `get_parser("kotlin")` (the post-Phase-2 default); developing pre-cutover, parse via the Phase-1 seam directly (`import cairn._tree_sitter_kotlin`; `Parser(Language(m.language()))`) — the scan needs a loadable fwcd grammar but not the ported extractor (plan Dependencies).
  - API verified on tree-sitter 0.26.0 (tech-spec § Solution): `root_node.has_error` is `True` for `class { val broken =` and `False` for `val x = 1`; `Node.has_error` and `Node.is_missing` both exist.
  - Verify before implementing: `grep -rn "has_error" src/` → no output (survey item 3 confirms the check is new, test-side only). Pitfalls (D-005): do NOT add the scan to doctor or `parse_errors` (builder.py:249 counts Python exceptions); do NOT regenerate expected.json.

## Phase 4: Toolchain-free wheels, dep removal, release hygiene (FR-005, FR-006, FR-007, FR-008)
<!-- Checkpoint (plan, After Phase 4): grep -rn "tree-sitter-kotlin" pyproject.toml uv.lock NOTICE src/ -> no hits; fresh uv sync then uv pip show tree-sitter-kotlin -> not installed; full pytest -q green; CHANGELOG [Unreleased] carries Added/Changed; wheel-matrix dry run produces artifacts for macOS arm64/x64, manylinux x86_64/aarch64, musllinux, Windows; CI build-job clean-runner import check passes without a C toolchain. -->

- [ ] T009 Wheel matrix in the release workflow — `.github/workflows/release.yml`: the build job (today `run: uv build` on ubuntu-latest only, producing the pure-python py3-none-any wheel — release.yml:43-46 comment, survey item 4) becomes a `runs-on: [ubuntu-latest, macos-latest, windows-latest]` matrix using exactly-pinned `pypa/cibuildwheel@v4.2.0` with CIBW selectors — linux `cp310-abi3-manylinux_x86_64 manylinux_aarch64 musllinux_x86_64 musllinux_aarch64` (aarch64 via QEMU), `CIBW_ARCHS_MACOS: "x86_64 arm64"`, `CIBW_ARCHS_WINDOWS: "AMD64"` — plus a separate `uv build --sdist` step on ubuntu; set `CIBW_TEST_COMMAND` to import the extension and parse a snippet so every wheel proves its own grammar; update the Makefile:3-5 comment (it documents today's py3-none-any name) (after T002) (FR-005; D-008)
  - Serial reason: consumes T002's output — the `setup.py` abi3 `Extension("cairn._tree_sitter_kotlin", ...)` and `MANIFEST.in` sdist contents are what the matrix packages; before Phase 1 lands there is nothing to matrix-build (plan Dependencies).
  - `publish-pypi`/`github-release` need no edits (they download the dist artifact — release.yml:69-71/:91); the old "Verify the wheel imports" step (release.yml:48-52) is superseded by CIBW_TEST_COMMAND. ci.yml's build job (ci.yml:269-272: `python -m build`, then `python -c "import cairn; print('ok')"`) is behavior-compatible — review only, no edit.
  - Proof anchors: `make dist` → the wheel filename changes from the py3-none-any tag to a cp310-abi3 host-platform tag; the ci.yml import check passes on a clean runner without a C toolchain. Pitfall: pin cibuildwheel exactly (its FAQ mandates exact pins; minor versions add/remove platforms).

- [ ] T010 Remove the `tree-sitter-kotlin==1.1.0` dependency outright — delete `pyproject.toml:30`; regenerate `uv.lock` (`uv lock` — entries at uv.lock:215, uv.lock:298 and the package block, survey item 5); drop `tree-sitter-kotlin` from the NOTICE:26 MIT dependency list (it is in-tree now, not a dep); reword the comment-only mentions at tests/test_parser_audit_fixes.py:18 and :236 to name fwcd's grammar so future readers are not misled (after T006 and T009) (FR-006; D-002)
  - Serial reasons (plan Dependencies): removal is safe only after the cutover proves the package unused (T006), and it co-owns `pyproject.toml` with the wheel stream — landing after T009 in the same PR avoids the conflict. No fallback loader; single Kotlin grammar path (FR-006).
  - Proof anchors (survey item 5's inventory must show zero): `grep -rn "tree-sitter-kotlin" pyproject.toml uv.lock NOTICE src/` → no hits; fresh `uv sync && uv pip show tree-sitter-kotlin` → not installed. A stale uv.lock is easy to miss — relock or CI resolves the deleted pin against a cached lock (tech-spec area 2 pitfall). After this task `grep -rn "tree_sitter_kotlin" src/ pyproject.toml` shows only `cairn._tree_sitter_kotlin` occurrences.

- [ ] T011 Verify the scip-java hybrid end-to-end on the new grammar — no code change (D-007): re-run the hybrid suites as a gate — `uv run --extra dev --extra test pytest tests/test_build_scip_hybrid.py tests/test_golden_parsers.py tests/test_kotlin_operator_invoke.py tests/test_self_demo.py -q` → 30 passed — plus the full suite `uv run --extra dev --extra test pytest -q` green (the CI path, README.md:333-334, survey item 7) (after T006) (FR-007)
  - Stays open even though the suites exist and pass 30 today (survey item 6 verify): FR-007 requires them green with the NEW grammar feeding the substrate — this task is that gate re-run post-cutover, not a claim inherited from the baseline.
  - The merge folds SCIP definitions onto tree-sitter rows (`_merge_scip_defs_into_tree_sitter`, scip_importer.py:329; builder.py:410-413 documents that tree-sitter still parses SCIP-covered files); with extraction parity the join keys (files, symbol rows) are unchanged — a hybrid failure indicts the port, not the merge (D-007).
  - Hybrid tests use synthetic indexes — no JVM and no scip-java CI install (spec Scope: SCIP-side changes are out).

- [ ] T012 Changelog entries — `CHANGELOG.md` under `## [Unreleased]` (CHANGELOG.md:13): Added — modern-Kotlin syntax parses ERROR-free (KEEP-0438, when-guards, multi-dollar interpolation, trailing commas); Changed — Kotlin grammar swapped to the vendored fwcd grammar, `tree-sitter-kotlin` dependency removed, wheels are now per-platform with toolchain-free install preserved (after T009, T010, T011) (FR-008)
  - Serial reason (plan Parallelization map): the changelog lands last — `[Unreleased]` records what actually shipped.
  - Verify before implementing: `sed -n '1,45p' CHANGELOG.md` (section shape: Added at CHANGELOG.md:15, Fixed at :33, Changed at :41; entries are multi-paragraph prose, Keep a Changelog 1.1.0 — survey item 8).
  - Pitfall: do NOT add a version heading — release.yml's awk extraction (release.yml:104-114) keys on `## [X.Y.Z]` headers and a premature one publishes empty notes; the version bump happens only at tag time via `cz bump` (pyproject.toml:215-224, survey item 8).

## Conventions
- `- [ ]` todo · `(in-progress)` claimed · `- [x]` done + proof note in the
      shape `done 2026-08-25 — pytest tests -q -k kotlin: 13 passed`
      (real date, real command)
- Dropped: `- [ ] ~~T004~~ dropped <date> (D-###)` — never delete the line;
  dropped tasks stay visible with the decision that killed them
- `[P]` = parallelizable (default — no shared files, no upstream task);
  chained tasks note `(after T###)` and name the exact interface they
  consume from their upstream — symbols, signatures, file formats; serial
  runs need a reason, parallel runs need none
- Fix rounds append `(fix <n>/5)` to the entry — the cap survives resume
  only if the count lives here, in the status holder
- Every task cites its FR-###; tasks with no FR are scope creep — fix the
  spec first

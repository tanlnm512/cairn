# Test Cases: remove-scip-exact-rate

**Spec**: [spec.md](spec.md) | **Created**: 2026-09-11
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details. Baseline commit
for all before/after comparisons: `dc9882b` (survey.md:3).

## TC-001 — No SCIP ingestion machinery remains anywhere in the shipped code
- **Story**: US1 · **Traces to**: FR-001, AC1
- **Given** the shipped package source after the change
- **When** the indexing code surface — parsers, graph build, command line, knowledge ingestion, and the developer scripts directory — is searched case-insensitively for the compiler-index name, word-bounded so ordinary English words that merely contain the same letters are not false positives
- **Then** nothing is found: the importer, the external-indexer orchestrator, the vendored protocol stub, the regeneration script, and every merge/skip/auto-generation path are gone; every language is indexed through the tree-sitter path only
- **Pass condition**: `grep -rniE --include='*.py' --include='*.sh' '\<scip' src/cairn/parsers src/cairn/graph src/cairn/cli src/cairn/knowledge scripts; test $? -eq 1` — exit 0 with no output (grep exit 1 = zero hits; the word boundary spares the known "discipline"-substring script, per survey S-01; at baseline this same grep hits the 16 source files enumerated in S-01..S-05).

## TC-002 — A workspace with no SCIP configuration builds fully via tree-sitter with no SCIP trace
- **Story**: US1 · **Traces to**: FR-001, AC1
- **Given** a multi-language workspace with no SCIP configuration anywhere (the pinned ds2 attrs corpus, copied to a scratch workspace and given a repository marker, because the committed tree has none and scans as zero files)
- **When** a full build runs
- **Then** the build completes successfully, resolves its edges, and its report mentions the compiler-index name nowhere — not even the empty placeholder field the report carried at baseline
- **Pass condition**: `bash -c 'set -e; tmp=$(mktemp -d); cp -R benchmarks/datasource/ds2/second-corpus/attrs-26.1.0 "$tmp/attrs"; mkdir "$tmp/attrs/.git"; uv run cairn build --workspace "$tmp/attrs" --db "$tmp/g.db" >"$tmp/out.log" 2>&1; ! grep -qiE "\<scip" "$tmp/out.log"'` — exit 0 iff the build succeeded and its log is clean (survey S-11's copy+marker idiom; this 50-file corpus builds in ~3 s; command mechanics verified this session).

## TC-003 — A mid-rebuild crash still leaves the partial-repo marker detectable
- **Story**: US1 (boundary) · **Traces to**: FR-001, AC1
- **Given** a build that crashes partway through a rebuild — a crash window that previously sat just before the now-removed post-resolution compiler-index hook, so the point at which the build-state marker is cleared moves when that hook disappears
- **When** the crash happens before the marker is cleared
- **Then** the marker survives the crash and a later health check can flag the partial repository — the crash-safety guarantee holds at the new anchor exactly as it held at the old one
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_workflow_audit_fixes.py::test_crashed_repo_build_leaves_marker_detectable -q` — passes after the re-anchor and wording refresh (survey S-07).

## TC-004 — A stale `scip` key in cairn.json is ignored exactly like any other unknown key
- **Story**: US1 · **Traces to**: FR-002, AC2
- **Given** a workspace whose cairn.json still carries a `scip` key pointing at a nonexistent index file, with an obviously-nonsense key sitting beside it
- **When** a full build runs
- **Then** the build succeeds via tree-sitter for every file, and the stale key is treated precisely as the nonsense key is — silently ignored, with no SCIP-specific warning, error, deprecation notice, index-file lookup, or auto-generation attempt of any kind
- **Pass condition**: `bash -c 'set -e; tmp=$(mktemp -d); cp -R benchmarks/datasource/ds2/second-corpus/attrs-26.1.0 "$tmp/attrs"; mkdir "$tmp/attrs/.git"; printf "{\"scip\":{\"python\":\"stale.scip\"},\"not_a_real_key\":{}}" >"$tmp/attrs/cairn.json"; uv run cairn build --workspace "$tmp/attrs" --db "$tmp/g.db" >"$tmp/out.log" 2>&1; ! grep -qiE "\<scip" "$tmp/out.log"'` — exit 0 iff the build succeeded on the stale-key workspace and its output names the compiler-index nowhere (complete removal, no compat shim, per the clarify-round ruling in spec Assumptions).

## TC-005 — The CLI no longer offers an import command for compiler indexes
- **Story**: US1 · **Traces to**: FR-003, AC1
- **Given** the installed cairn command line after the change
- **When** the removed import command is invoked, and the command listing is inspected
- **Then** no such command exists — invoking it fails as an unknown command, and the top-level help names nothing of the kind
- **Pass condition**: `uv run cairn import-scip --help; test $? -ne 0` — the invocation must fail (at baseline it exits 0 with the command's help; post-change click rejects the unknown name); corroborate by reading `uv run cairn --help` output, which lists no such entry.

## TC-006 — The package no longer ships the optional index extra or its protocol toolchain
- **Story**: US1 · **Traces to**: FR-003, AC1
- **Given** the package manifest and developer toolchain after the change
- **When** inspected for the optional-dependency group, the protocol runtime it pulled in, the stub-regeneration toolchain that existed only for it, and the artifacts themselves (vendored stub, regeneration script, external-indexer orchestrator)
- **Then** none exist — there is no install path of any kind for the compiler-index subsystem
- **Pass condition**: `grep -niE 'scip|protobuf|grpcio' pyproject.toml; test $? -eq 1` — zero hits (the extra, its protobuf pin, and the dev-only regeneration dependency are all gone; the stub/script/orchestrator deletions are pinned by TC-001's sweep of the parsers and scripts directories; 4 hits at baseline per S-01).

## TC-007 — A fresh install's test suite collects no SCIP path and skips nothing for a missing extra
- **Story**: US1 · **Traces to**: FR-003, AC3
- **Given** a fresh install without the removed optional extra (post-change there is no such extra to install)
- **When** the test tree is searched for any case, helper, import, or skip-gate keyed on the compiler-index subsystem, and the full suite is run
- **Then** nothing is found and nothing related is collected or skipped: the dedicated suites are deleted outright (not skipped-for-missing-extra), the surviving tests that merely mentioned the subsystem in prose are reworded, and the suite is green
- **Pass condition**: `grep -rniE --include='*.py' '\<scip' tests; test $? -eq 1` — zero hits (at baseline: 391 hits across the 10 files inventoried in S-06/S-07; string-absence means no such test path can be collected and no skip machinery can remain); corroborate that the full suite stays green — `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/ -q` (3077 passed, 4 skips at baseline, 208 s — too slow for a bounded auto proof, run it as a human step).

## TC-008 — Living documentation describes a tree-sitter-only pipeline
- **Story**: US1 · **Traces to**: FR-004, AC1
- **Given** the living documentation — the docs tree, the README, and the agent-facing skill reference that ships to users on install
- **When** searched case-insensitively, word-bounded, for compiler-index references
- **Then** none remain: the indexing guide's optional-import section, the configuration and CLI-reference table rows, the architecture overview, the README language-table merge column and extras row, and the shipped skill's bindings claim all describe the tree-sitter-only pipeline (the append-only historical changelog is deliberately outside this gate per the S-08 ruling)
- **Pass condition**: `grep -rniE --include='*.md' --include='*.html' --include='*.svg' '\<scip' docs README.md src/cairn/agent_integration; test $? -eq 1` — zero hits (at baseline this lists exactly the 13 files enumerated in S-05/S-08; binary image twins are covered by TC-010).

## TC-009 — The configuration echo reports no SCIP section
- **Story**: US1 · **Traces to**: FR-004, AC1
- **Given** a workspace whose effective configuration is echoed by the config command
- **When** the command runs
- **Then** its output describes the configuration with no compiler-index line of either flavor — neither a per-language index listing nor the "(none — tree-sitter for all languages)" placeholder that prints at baseline
- **Pass condition**: `bash -c 'uv run cairn config >/tmp/cairn_config.out 2>&1 && ! grep -qi scip /tmp/cairn_config.out'` — exit 0 iff the command succeeded and its output is clean (at baseline the placeholder line prints verbatim, verified this session; both branches of the S-04 echo block must be gone).

## TC-010 — Rendered pipeline diagrams show no compiler-index branch (MANUAL)
- **Story**: US1 · **Traces to**: FR-004, AC1
- **Given** the indexing-pipeline and architecture diagrams, whose editable sources are swept clean by TC-008 but whose rendered image twins cannot be searched by text
- **When** a person views the regenerated images
- **Then** no compiler-index merge branch, decision node, or label is visible in either theme of the pipeline diagram or in the architecture diagrams — the images were actually regenerated from the cleaned sources, not left stale
- **Pass condition** (MANUAL — image content is a human observation, per S-08's note that grep cannot see into the PNG twins): open each regenerated diagram image (light and dark variants of both the indexing pipeline and the architecture diagrams) and confirm no such node or label remains; regeneration itself is evidenced by the images differing from their committed baseline versions after the source edit.

## TC-011 — The historical changelog stays untouched while exactly one new entry arrives
- **Story**: US1 (standing guard) · **Traces to**: FR-004, AC1
- **Given** the append-only historical record, which intentionally still contains past mentions of the removed subsystem
- **When** the change lands
- **Then** no historical entry is edited or deleted, and one new entry describing this change is appended
- **Pass condition**: `git diff dc9882b -- CHANGELOG.md | grep '^-[^-]'; test $? -eq 1` — the diff removes no content line (additions only: the one new entry; the 55 historical hits stay byte-identical per the S-08 ruling).

## TC-012 — The exact share strictly rises on cairn's own repository (MANUAL measurement)
- **Story**: US2 · **Traces to**: FR-005, AC4
- **Given** cairn's own repository (~13.5k files, dominated by Python) built once at the baseline commit and once after the change
- **When** the pinned share — exact/(exact+ambiguous) computed on the built edges table over the resolver-resolved reference kinds (call, references) only, with import-materialization/containment/decoration edges and unresolved edges excluded from both numerator and denominator — is computed for each build
- **Then** the post-change share is strictly higher than the recorded baseline 0.2236 (exact 192488 / ambiguous 668424), subject to the zero-false-exact gates of TC-022..TC-025 staying green
- **Pass condition** (MANUAL — a multi-minute build driven by a throwaway measurement script kept out of the repo, per survey S-11's recorded recipe): `SELECT CAST(SUM(CASE WHEN resolution='exact' THEN 1 ELSE 0 END) AS REAL) / SUM(CASE WHEN resolution IN ('exact','ambiguous') THEN 1 ELSE 0 END) FROM edges WHERE kind IN ('calls','references')` run against each build's database returns a strictly larger value for the post-change build; the recorded before/after pair is the evidence. Note the pinned counter is the edges-table SQL, NOT the build summary or the all-kinds share (S-11's instrument-gap finding: same DB yields 0.2237 resolve-phase vs 0.3737 all-kinds — only the pinned definition above counts).

## TC-013 — The exact share strictly rises on the ds2 corpus (MANUAL measurement)
- **Story**: US2 · **Traces to**: FR-005, AC4
- **Given** the ds2 attrs corpus, which cannot be built in place (the committed tree carries no repository marker and scans as zero files) and whose baseline share under the pinned TC-012 definition is recorded by the baseline-measurement task
- **When** the corpus is copied to a scratch workspace, given the marker, built after the change, and the same pinned share computed
- **Then** the post-change share strictly exceeds the recorded baseline under this same definition
- **Pass condition** (MANUAL — same recipe and throwaway-script discipline as TC-012): `SELECT CAST(SUM(CASE WHEN resolution='exact' THEN 1 ELSE 0 END) AS REAL) / SUM(CASE WHEN resolution IN ('exact','ambiguous') THEN 1 ELSE 0 END) FROM edges WHERE kind IN ('calls','references')` run against the post-change ds2 build's edges table returns a strictly larger value than the baseline-measurement task's recorded ds2 number; the copy+marker idiom is mandatory before building — `cp -R benchmarks/datasource/ds2/second-corpus/attrs-26.1.0 <tmp>/attrs && mkdir <tmp>/attrs/.git` (survey S-11; at baseline this corpus yields 50 files / 6239 edges so the after-build must stay in that shape, pinned exactly by TC-022).

## TC-014 — A corpus with nothing to resolve builds without incident
- **Story**: US2 (boundary) · **Traces to**: FR-005, AC4
- **Given** a minimal workspace whose only call targets are external/standard-library symbols — zero intra-workspace reference edges, so the pinned share's denominator is empty
- **When** a full build runs and the share would be computed
- **Then** the build completes normally; the vacuous share reads as undefined/empty rather than crashing or inventing a number
- **Pass condition**: `bash -c 'set -e; tmp=$(mktemp -d); mkdir -p "$tmp/r/.git"; printf "import json\njson.dumps({})\n" >"$tmp/r/m.py"; uv run cairn build --workspace "$tmp/r" --db "$tmp/g.db"'` — exit 0 (mechanics verified this session; the pinned SQL over this build returns an empty/NULL share, not an error).

## TC-015 — Calls made through an import alias resolve exact
- **Story**: US2 · **Traces to**: FR-006, AC6
- **Given** a workspace where a symbol is imported under a local alias and invoked through it — `import module as m` then `m.func()`, or `from pkg import name as local` then `local()` — a shape that stayed ambiguous at baseline because no parser recorded the alias binding anywhere the resolver could see
- **When** the workspace is built after the change
- **Then** that call edge resolves to exactly the aliased symbol's definition, and a regression test whose name names the import-alias shape proves it, with a fixture that resolved ambiguous at baseline
- **Pass condition**: `grep -rniE 'def test_.*import_?alias' tests --include='*.py'` — exits 0 listing at least one resolver regression test (verified empty at baseline this session, so any hit is new); that test's module must then pass under `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest <module> -q`, and its fixture must be a previously-ambiguous aliased call (survey S-10: alias bindings were invisible; such edges fell through to same-repo/global).

## TC-016 — Calls with a plainly inferrable receiver resolve exact in previously-silent languages (MANUAL)
- **Story**: US2 · **Traces to**: FR-006, FR-007, AC6
- **Given** a call whose receiver's type the language's own syntax makes plain — e.g. a method call on a freshly constructed value — written in one of the languages whose parsers dropped the receiver signal at baseline (the majority of the fourteen)
- **When** the workspace is built after the change
- **Then** the call edge resolves exact via the receiver, and a regression test naming the receiver shape for such a language proves it; where two candidates genuinely tie, the edge still abstains per TC-025
- **Pass condition** (MANUAL — the new test must be distinguished from the pre-existing receiver pins by a human): at least one NEW resolver regression test covering a previously-silent language's receiver-based resolution — a `test_…receiver…` name beyond the five baseline receiver pins of survey S-13 — exists and passes under `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest <module> -q`; discovery aids: `grep -rniE 'def test_.*receiver' tests --include='*.py'` for the tests, `grep -rlE --include='*.py' 'receiver_type=' src/cairn/parsers` for the emitter files (exactly go/java/kotlin/php/ruby at baseline per S-10).

## TC-017 — Same-named overloads distinguished by argument count resolve exact (MANUAL)
- **Story**: US2 · **Traces to**: FR-006, AC6
- **Given** several same-named definitions in one file distinguished only by argument count, and a call site whose argument count matches exactly one of them
- **When** the workspace is built after the change
- **Then** the call resolves to exactly the matching overload — at baseline a same-name collision in a file ended ambiguous — while a call whose count matches two overloads still abstains to ambiguous rather than guessing
- **Pass condition** (MANUAL — baseline tests with `overload` names exist in the to-be-deleted merge suites, so a name grep alone cannot identify the new test): a NEW resolver regression test naming the overload/arity shape exists and passes under `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest <module> -q`, asserting exact resolution for the unambiguous count and continued ambiguity for the tied count; discovery aid: `grep -rniE 'def test_.*(overload|arity)' tests --include='*.py'` (baseline hits there belong to the S-06 deletion set).

## TC-018 — No new runtime dependency arrives with the exact-rate work
- **Story**: US2 (standing guard) · **Traces to**: FR-006
- **Given** the packaged runtime dependency set at the baseline commit
- **When** the post-change manifest is compared against it
- **Then** no runtime dependency was added — the exact-rate gains come from resolver heuristics and richer parsed signals alone (the removals of the compiler-index toolchain are expected; any addition is not)
- **Pass condition**: `git diff dc9882b -- pyproject.toml | grep -E '^\+\s*"'; test $? -eq 1` — no added dependency entry in the manifest (additions would appear as new quoted lines; version bumps and comments do not match).

## TC-019 — All fourteen languages' parser goldens regenerate and hold
- **Story**: US2 · **Traces to**: FR-007
- **Given** the golden parser snapshots for the fourteen supported languages (c, cpp, csharp, dart, go, java, javascript, kotlin, objc, php, python, ruby, swift, typescript)
- **When** parser-side signal enrichment changes what parsers emit and the snapshots are regenerated from the fixtures
- **Then** every language's snapshot matches its parser — no language's parsing regresses, and each grammar's exposed signals are carried
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_golden_parsers.py -q` — all fourteen parametrized cases pass (the golden suite is the update vehicle named in survey S-13).

## TC-020 — No language's exact share gets worse (MANUAL measurement)
- **Story**: US2 (boundary) · **Traces to**: FR-007
- **Given** the per-language exact shares recorded on cairn's own repository at baseline, including languages whose grammar genuinely lacks a signal
- **When** the post-change build's per-language shares are computed the same way on the same corpus
- **Then** no language falls below its baseline share — enrichment degrades a signal-less language to current behavior, never worse
- **Pass condition** (MANUAL — same measurement recipe as TC-012, grouped per language): `SELECT ... share ... GROUP BY language` (the TC-012 share expression joined to the file/language dimension, per survey S-11's per-language recipe) returns, for each of the fourteen, a value ≥ its baseline-table entry (S-11's recorded table, e.g. python 0.3664, javascript 0.4573, ruby 0.4762, kotlin 0.6609).

## TC-021 — The resolver's tie-break priority order is unchanged
- **Story**: US2 (standing guard) · **Traces to**: FR-008
- **Given** a reference edge that more than one resolution tier could claim — a typed receiver, a same-file namesake, an import-aware narrowing, a same-repository namesake, a repo-global singleton
- **When** it is resolved
- **Then** the long-standing priority decides: receiver-type-aware first, then same-file, import-aware, same-repo, global single-candidate, and only then ambiguous — unchanged from baseline unless a recorded decision re-orders it with evidence
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_type_tier.py tests/test_resolver_type_scoped_fallback.py -q` — the two pinning suites (5 + 9 tests) stay green (survey S-13's protect set, run green at baseline).

## TC-022 — The ds2 retrieval ground-truth seal stays perfect
- **Story**: US2 · **Traces to**: FR-009, AC5
- **Given** the 558 sealed retrieval expectations spanning both ds2 corpora, verified against fresh builds with authoring facts and tree hashes cross-checked
- **When** the seal runs after the change
- **Then** every expectation still matches tier-1-exact — zero regressions, zero unresolved, zero aspirational; no false-exact conversion corrupted retrieval
- **Pass condition**: `uv run python benchmarks/datasource/ds2/verify_dataset.py` — exit 0 with the "558/558 expectations tier-1-exact" line and matching build facts (50 files / 1722 symbols / 6239 edges for attrs; survey S-12's run and CI's ds2-seal job).

## TC-023 — The t2 retrieval ground truth stays green
- **Story**: US2 · **Traces to**: FR-009, AC5
- **Given** the t2 ground-truth pair, verified via a fresh copy-and-build of its vendored snapshot
- **When** the verification script runs after the change
- **Then** it exits 0 — no previously-passing expectation regresses
- **Pass condition**: `uv run python scripts/verify_ground_truth.py` — exit 0 (exit 1 = stale verdict, 2 = infrastructure failure; survey S-12).

## TC-024 — No exact-labeled edge lacks a real target
- **Story**: US2 (standing guard) · **Traces to**: FR-009, AC5
- **Given** any database built after the change
- **When** the stored edges are checked for exact-labeled edges that carry no concrete definition pointer
- **Then** none exist — an exact label always carries a real target, so precise queries never trust a fabricated binding
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_invariants.py::test_invariant_exact_resolution_has_target_id -q` — passes (the zero-false-exact backstop, survey S-13).

## TC-025 — A missing receiver signal still abstains instead of guessing
- **Story**: US2 (standing guard) · **Traces to**: FR-009
- **Given** two same-named candidates in one repository and a call whose receiver type is unknown
- **When** the edge is resolved
- **Then** it stays ambiguous — never a guessed exact — precision outranks recall exactly as before
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_type_tier.py::test_receiver_type_none_is_abstain_safe -q` — passes (the FR-009 abstain pin, survey S-13).

## TC-026 — Resolution wall-time stays within the advisory gate (MANUAL)
- **Story**: US2 (boundary) · **Traces to**: FR-010
- **Given** the scaling benchmark's committed and rolling baseline timings, the advisory 15% regression threshold, and the survey-recorded wall-times (self-repo build 198.7 s at baseline)
- **When** the benchmark comparison runs after the change
- **Then** the build's resolution phase does not regress past the threshold — the comparison exits 0; an exit-2 (past 15%) is absorbed by the advisory design but demands explicit justification against the recorded baseline numbers
- **Pass condition** (MANUAL — a machine-sensitive timing gate observed in the CI bench job or a local run; too slow and environment-dependent for a bounded proof command): the bench job's invocation — cairn bench with the perf suite, hash embed backend, three repeats, against the DS-v1 committed baseline or the rolling comparison artifact — exits 0 per the documented exit contract (0 clean / 1 baseline error / 2 past 15%; survey S-12 and the ci.yml bench job).

## Coverage matrix
<!-- Every FR appears; `check.py` fails an FR with no TC. -->
| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001      | TC-001, TC-002, TC-003 | auto |
| FR-002      | TC-004 | auto |
| FR-003      | TC-005, TC-006, TC-007 | auto |
| FR-004      | TC-008, TC-009, TC-010, TC-011 | auto (TC-010 manual) |
| FR-005      | TC-012, TC-013, TC-014 | manual (TC-012, TC-013) · auto (TC-014) |
| FR-006      | TC-015, TC-016, TC-017, TC-018 | auto (TC-015, TC-018) · manual (TC-016, TC-017) |
| FR-007      | TC-016, TC-019, TC-020 | auto (TC-019) · manual (TC-016, TC-020) |
| FR-008      | TC-021 | auto |
| FR-009      | TC-022, TC-023, TC-024, TC-025 | auto |
| FR-010      | TC-026 | manual |

**Untestable**: none — every FR has at least one TC with an observable pass
condition. The MANUAL set is exactly the cases whose pass condition is a
human observation or an unbounded measurement: the image twins (TC-010),
the two corpus share measurements and their per-language cut (TC-012,
TC-013, TC-020 — multi-minute builds with throwaway scripts kept out of
the repo per survey S-11), the two shape regressions whose new tests must
be distinguished from same-named baseline tests by a human (TC-016,
TC-017), and the machine-sensitive timing gate (TC-026). AC coverage:
AC1 → TC-001/TC-002, AC2 → TC-004, AC3 → TC-007, AC4 → TC-012/TC-013/
TC-014, AC5 → TC-022/TC-023/TC-024, AC6 → TC-015/TC-016/TC-017.

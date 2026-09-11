# Tasks: remove-scip-exact-rate

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
Status reflects code state per [survey.md](survey.md), not intent.
**Before-audit**: pending — the orchestrator writes `passed @ <sha>` here

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
| Phase | Total | Done |
|-------|-------|------|
| 1     | 1     | 0    |
| 2     | 6     | 0    |
| 3     | 2     | 0    |
| 4     | 14    | 0    |
| 5     | 2     | 0    |
| 6     | 4     | 0    |
| **Σ** | 29    | 0    |

## Phase 1: Baseline pinned (FR-005 evidence infrastructure)
<!-- Checkpoint: ds2 baseline numbers recorded under the spec.md:49 definition
     (copy + .git marker, build, SQL over resolver-resolved kinds); self-repo
     call-kind share re-confirmed at 0.2236; standing gate green; recipe per
     S-11; no script lands in the repo. -->
- [ ] T001 Record the pre-change exact-share baseline on BOTH corpora under
  the pinned FR-005 counter — exact/(exact+ambiguous) over the built edges
  table restricted to the resolver-resolved reference kinds, excluding
  `imports`/`contains`/`decorates` and `unresolved` — via a throwaway
  `__main__`-guarded script kept in /tmp (D-011; build workers spawn-reimport,
  S-11): self-repo `build_graph(workspace=".")` re-confirming 0.2236 (192488
  exact / 668424 ambiguous) plus resolve-phase wall-time vs the 198.7 s
  baseline, and the ds2 equivalent after
  `cp -R benchmarks/datasource/ds2/second-corpus/attrs-26.1.0 <tmp>/attrs && mkdir <tmp>/attrs/.git`
  (S-11 gap 3 — never build ds2 in place); record both numbers + wall-times
  as done-notes here before any Phase-2 deletion lands ("before" stops being
  reproducible from the working tree the moment one lands). Pinned SQL:
  `SELECT CAST(SUM(CASE WHEN resolution='exact' THEN 1 ELSE 0 END) AS REAL) / SUM(CASE WHEN resolution IN ('exact','ambiguous') THEN 1 ELSE 0 END) FROM edges WHERE kind IN ('call','references')`
  — the edges-table SQL, never the summary counters or
  `scaling_suite._resolve_rate` (S-11's instrument gap: 0.3737 vs 0.2237 vs
  0.2236 on one DB). (FR-005; TC-012/TC-013's recorded baselines, TC-026's
  wall-time baseline)

## Phase 2: SCIP removal — code (FR-001, FR-002, FR-003)
<!-- Checkpoint: `rg -n 'scip' src/cairn/graph/builder.py` → 0;
     `rg -n 'scip|_SCIP_KEY' src/cairn/graph/config.py` → 0;
     `rg -n -i 'scip' src/cairn/cli/` → 0;
     `rg -l 'scip' src/cairn/parsers/ scripts/ pyproject.toml` → only
     scripts/fetch_t3_corpus.py ("discipline" false positive, S-01);
     surviving-test suites green; the four scip test files gone and
     `pytest --collect-only` shows no scip path (AC3); scratch-workspace
     `cairn build` succeeds with and without a stale `scip` key (AC1/AC2). -->
### P2 wave 1 — import-site removal, comment refreshes, test deletions (mutually file-disjoint, [P])
- [ ] T002 [P] Excise the builder hybrid path and re-anchor the crash-window
  marker in `src/cairn/graph/builder.py` (`_build_graph_impl:383`): delete
  the scip_languages resolution/auto-generation block 410-453 (incl.
  `if cfg.scip:` :419 and the `try_generate_index` import :430-431), the
  scan-event comment 406-408, import+merge+rollback 558-599, revert-to-pure-scip
  601-653, the `_language_extensions` helper comment 135-137 (helper's only
  consumer is the revert block — delete the helper too), and summary folding
  673-701 (`scip_repo_count`, `summary["scip"]`); re-anchor
  `_clear_repo_build_state` (comment 655-662) so the marker clear stays the
  LAST write, now after `materialize_import_edges` (builder.py:1262) instead
  of after the SCIP hook; in the SAME task reword the pinned survivors —
  `tests/test_workflow_audit_fixes.py:test_crashed_repo_build_leaves_marker_detectable:313`
  (patches `_resolve_all` at :329; logic survives, docstring/comments :317/:327
  and section header :295 re-anchored), `tests/test_doctor.py:411` comment,
  `tests/test_invariants.py:18-19` and `:217` docstrings (drop the stale
  `BUGS.md#scip-importer-fake-resolution` citation, S-09 — the invariant test
  itself stays, S-13). Proof: `rg -n 'scip' src/cairn/graph/builder.py` → 0;
  `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_workflow_audit_fixes.py::test_crashed_repo_build_leaves_marker_detectable -q`
  (TC-003) plus the Phase-2 checkpoint suite
  `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_workflow_audit_fixes.py tests/test_doctor.py tests/test_invariants.py -q`
  green. (FR-001; TC-002, TC-003)
- [ ] T003 [P] Remove the CLI surface: the `import-scip` command in
  `src/cairn/cli/hooks_viz.py:86-99` (`@main.command(name="import-scip")` +
  `import_scip:93` + the `import_scip_file` import :95), the `cairn config`
  scip echo block `src/cairn/cli/core.py:241-251` (BOTH branches — the
  per-language listing and the "(none — tree-sitter for all languages)"
  placeholder), and the `src/cairn/cli/system.py:1` module docstring
  ("import-scip, metrics, status, …" → no import-scip). Proof:
  `uv run cairn import-scip --help; test $? -ne 0` (TC-005) and
  `bash -c 'uv run cairn config >/tmp/cairn_config.out 2>&1 && ! grep -qi scip /tmp/cairn_config.out'`
  (TC-009); `rg -n -i 'scip' src/cairn/cli/` → 0. (FR-003; TC-005, TC-009)
- [ ] T004 [P] Refresh the peripheral comment sites (code stays, comments
  only): `src/cairn/graph/schema.py:449-450` (provenance comment — the
  `symbols.source` COLUMN stays, values become uniformly `'tree_sitter'`,
  no DB migration per spec.md:63), `src/cairn/graph/traversal.py:15`,
  `src/cairn/parsers/base.py:45` (Edge docstring "tree-sitter SCIP edges" →
  tree-sitter-only wording), `src/cairn/knowledge/ingest/identity.py:33`
  (reword the comment justifying `_HASH_LEN = 8` — it cites
  parsers/scip_importer.py which dies; the constant stays).
  `src/cairn/graph/incremental.py` needs ZERO edits (no scip code, S-05).
  Proof: `rg -n -i 'scip' src/cairn/graph/schema.py src/cairn/graph/traversal.py src/cairn/parsers/base.py src/cairn/knowledge/ingest/identity.py src/cairn/graph/incremental.py`
  → 0 (part of TC-001's sweep). (FR-001; TC-001)
- [ ] T005 [P] Delete the SCIP tests wholesale and strip orphaned
  scaffolding: delete `tests/test_scip_importer.py` (23 tests),
  `tests/test_scip_indexers.py` (16), `tests/test_build_scip_hybrid.py` (6),
  `tests/test_scip_incremental.py` (2); from
  `tests/test_parser_audit_fixes.py` remove the import at :40, the
  `pytestmark_scip` + helpers `_scip_conn`/`_insert_ts_symbol` (275-296),
  and classes `TestScipMergeOverloads:299` + `TestScipHeaderLanguage:402`;
  from `tests/test_audit_remediation.py` remove
  `test_p5_failed_scip_import_rolls_back_pending_writes:402` and its
  `import_scip_bytes, scip_available` import :405; from
  `tests/test_big_tech_improvements.py` remove `test_scip_importer:11`, the
  module import at :5 (ruff F401 gate, S-06), and the stale docstring
  citation :18-19 (S-09). Surviving-test rewords belong to T002 (file
  ownership). Proof: `grep -rniE --include='*.py' '\<scip' tests; test $? -eq 1`
  (TC-007's string gate — the S-07 survivors' rewords land in T002, so this
  task's proof holds after T002) and
  `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_parser_audit_fixes.py tests/test_audit_remediation.py tests/test_big_tech_improvements.py -q`
  green. (FR-003; TC-007)

### P2 wave 2 — module/packaging/config deletion (after T002..T005: consumes the removed import sites)
- [ ] T006 (after T002, T003, T005) Delete the SCIP modules and packaging:
  `src/cairn/parsers/scip_importer.py`, `src/cairn/parsers/scip_indexers.py`,
  `src/cairn/parsers/_scip_pb2.py`, `scripts/regen_scip_pb2.sh`; in
  `pyproject.toml` drop the `scip = […]` extra (126-127) + its comment block
  (119-125) and the dev-only `grpcio-tools>=1.60` (:87 — existed solely for
  stub regen); relock (`uv lock`). Interface consumed: with T002/T003 gone,
  the only non-test importers of `scip_importer` (builder.py:561,
  hooks_viz.py:95) and `scip_indexers` (builder.py:430) no longer exist
  (S-01); test deletions (T005) precede module deletion so collection never
  sees a dangling import. Proof: `grep -rniE --include='*.py' --include='*.sh' '\<scip' src/cairn/parsers src/cairn/graph src/cairn/cli src/cairn/knowledge scripts; test $? -eq 1`
  (TC-001) and `grep -niE 'scip|protobuf|grpcio' pyproject.toml; test $? -eq 1`
  (TC-006). (FR-001, FR-003; TC-001, TC-006)
- [ ] T007 (after T002) Drop the `scip` config key in
  `src/cairn/graph/config.py` (S-03's 4-line delta): field :54, docstring
  paragraph :43-45, `and not self.scip` in `is_default` :62, `_SCIP_KEY` :71,
  parse line :107, constructor arg :113 — `load_config` then reads only the
  four named keys and `scip` becomes an unknown key ignored silently, no
  shim, no deprecation warning (clarify-round ruling, spec.md:66). Interface
  consumed: builder's `if cfg.scip:` consumer (:419) is gone after T002.
  Proof: `rg -n 'scip|_SCIP_KEY' src/cairn/graph/config.py` → 0 and
  `bash -c 'set -e; tmp=$(mktemp -d); cp -R benchmarks/datasource/ds2/second-corpus/attrs-26.1.0 "$tmp/attrs"; mkdir "$tmp/attrs/.git"; printf "{\"scip\":{\"python\":\"stale.scip\"},\"not_a_real_key\":{}}" >"$tmp/attrs/cairn.json"; uv run cairn build --workspace "$tmp/attrs" --db "$tmp/g.db" >"$tmp/out.log" 2>&1; ! grep -qiE "\<scip" "$tmp/out.log"'`
  (TC-004). (FR-002; TC-004)

## Phase 3: Living-docs cleanup (FR-004)
<!-- Checkpoint: `rg -n -i 'scip' docs/ README.md src/cairn/agent_integration/`
     → 0 (CHANGELOG.md excluded by the living-docs ruling, spec.md:65); the
     six PNG twins regenerated from the edited diagram sources. -->
- [ ] T008 [P] Sweep the markdown docs and shipped skill reference:
  `docs/indexing.md` §8 "SCIP import (optional)" (66-72) deleted with §9/§10
  renumbered, `docs/configuration.md:16` config-table row and :211 extras-table
  row, `docs/cli-reference.md:103` import-scip row, `docs/architecture.md:84`
  parsers row, `README.md:103-109` language-table SCIP-merge column entries +
  `README.md:318` "[scip] extra" row, and
  `src/cairn/agent_integration/skill/references/tools.md:9` (shipped to users
  via install-agents package data — drop the "SCIP compiler-grade exact
  bindings" claim). CHANGELOG.md history untouched (D-010; the one new entry
  is T029, Phase 6). Proof: `grep -rniE --include='*.md' --include='*.html' --include='*.svg' '\<scip' docs README.md src/cairn/agent_integration; test $? -eq 1`
  minus the diagram files T009 owns at review time — final gate after T009
  (TC-008). (FR-004; TC-008)
- [ ] T009 [P] Clean the diagram sources and regenerate the PNG twins
  (S-08 PARTIAL residue, pinned by D-010): edit
  `docs/diagrams/indexing-pipeline.html`, `.svg`, `-dark.html` (4 hits each:
  `<desc>` "optional SCIP merge branch", "YES · SCIP KEY" label, "scip key?"
  decision node, "Merge SCIP defs" node) and the four c4 label files
  `c4.html:146`, `c4-dark.html:147`, `c4-context.html:68`,
  `c4-context-dark.html:68` ("tree-sitter · 14 languages · SCIP"); regenerate
  the six PNG twins (`indexing-pipeline.png`, `indexing-pipeline-dark.png`,
  and the four c4 PNGs) from the edited sources — regeneration is part of
  this task, not optional (grep cannot see into PNGs, S-08); pin the renderer
  used in the done-note. Proof: TC-008's grep over the edited sources → the
  diagram files drop out, and TC-010's manual view of each regenerated image
  (light+dark, pipeline+architecture) confirms no merge branch/decision
  node/label remains. (FR-004; TC-008, TC-010)

## Phase 4: Parser signal enrichment (FR-006, FR-007)
<!-- Checkpoint: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest
     tests/test_golden_parsers.py -q` green with goldens regenerated via
     tests/fixtures/golden/regenerate.py; `rg -n 'receiver_type='
     src/cairn/parsers/` shows emitters beyond the baseline five
     (go/java/kotlin/php/ruby, S-10); per-language failing-first tests green;
     standing gate green. -->
- [ ] T010 (after T004 — shared file base.py) Add the signal substrate to
  `src/cairn/parsers/base.py`: `Import.local_alias: Optional[str]` (:60-63),
  `Edge.call_arity: Optional[int]`, `Symbol.arity: Optional[int]`, and the
  generalized kotlin-style scope-ordered var→type tracker (kotlin.py:643
  pattern, scope stacks at 45/72 — S-10) shared by the nine receiver-gap
  languages instead of nine local copies, obeying D-006's boundary (returns
  None on reassignment-to-different-type, intervening shadowing, or any
  ambiguity); reuse `_infer_receiver_type:152`. Degrade-to-`None` everywhere
  (FR-007's "never worse" contract). (FR-006, FR-007; substrate for
  TC-015/TC-016/TC-017)
- [ ] T011 [P] Add the storage + write path (disjoint from parser files):
  additive nullable columns `imports.local_alias` and `symbols.arity` in
  `src/cairn/graph/schema.py` following the `EDGE_RESOLUTION_MIGRATION`
  pattern (schema.py:425; no DB migration — legacy DBs age out, spec.md:63),
  and write them in the parser INSERT paths — builder.py
  `_insert_parsed_file` region and incremental
  `src/cairn/graph/incremental.py:insert_parsed_file`. Signals themselves
  ride in-memory resolve tuples only (builder.py:1156-1162 pattern; call_arity
  becomes the 7th tuple element — landing that wiring is T025's seam, this
  task lands the persisted columns). Proof: standing gate green —
  `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_type_tier.py tests/test_resolver_type_scoped_fallback.py tests/test_reindex_resolution_invariant.py tests/test_pointer_ambiguity.py tests/test_search_edge_expansion.py tests/test_invariants.py -q`
  (39 passed at baseline, S-13). (FR-006; TC-021's protect suite)
- [ ] T012 (after T010) [P] python — `src/cairn/parsers/python_parser.py`:
  author the failing-first tests (C-02) for python's alias/receiver/arity
  signals, then fix `_parse_import:300` to stop storing raw statement text
  (:301-302) and emit a normalized path + `local_alias` (D-003; F2.1
  field-labeled), extract receivers at `_parse_call:304` (S-10: emits none
  today) via the T010 tracker, count arity at def/call sites; regenerate the
  python golden via `tests/fixtures/golden/regenerate.py`. Proof: the new
  failing-first test module passes under
  `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest <module> -q` and
  `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_golden_parsers.py -q`
  (TC-019). (FR-006, FR-007; TC-015, TC-016, TC-019)
- [ ] T013 (after T010) [P] c + cpp — `src/cairn/parsers/c_family.py` (one
  module, one task): failing-first tests, then receiver extraction + arity
  per F2.2 field shapes and D-004; regenerate both goldens. Proof: new test
  module green + TC-019's golden command. (FR-007; TC-016, TC-019)
- [ ] T014 (after T010) [P] csharp — `src/cairn/parsers/csharp.py` (path per
  LANG_CONFIG, S-13): failing-first tests, then alias via the G2 structural
  rule (`using_directive` with a `qualified_name` child ⇒ `name` field is the
  alias), receiver extraction, arity; regenerate golden. Proof: new test
  module green + TC-019's golden command. (FR-006, FR-007; TC-015, TC-016,
  TC-019)
- [ ] T015 (after T010) [P] dart — `src/cairn/parsers/dart.py`: failing-first
  tests, then receiver via the G6 positional prefix-identifier chain and
  positional alias extraction; arity; regenerate golden. Pitfall: dart 0.1.0
  ships no node-types.json at its tag — the pinned parse is the only
  evidence. Proof: new test module green + TC-019's golden command.
  (FR-006, FR-007; TC-016, TC-019)
- [ ] T016 (after T010) [P] go — `src/cairn/parsers/go.py` (receiver already
  emitted at :349): failing-first tests, then `local_alias` emission with
  re-export/blank-import abstention (D-003 — go dot/blank imports record no
  alias) and arity from the already-walked `_parse_signature:198` params
  (:207); regenerate golden. Proof: new test module green + TC-019's golden
  command. (FR-006, FR-007; TC-015, TC-019)
- [ ] T017 (after T010) [P] java — `src/cairn/parsers/java.py` (receiver
  already at :290): failing-first tests, then `local_alias` emission (F2.1)
  and arity; regenerate golden. Proof: new test module green + TC-019's
  golden command. (FR-006, FR-007; TC-015, TC-019)
- [ ] T018 (after T010) [P] javascript + typescript —
  `src/cairn/parsers/typescript.py` (one module, one task; receiver missing
  at `_parse_call:516`): failing-first tests, then alias emission (relative
  specs already resolved at `_parse_import:454` :465-466), receiver
  extraction for both languages, arity; regenerate both goldens. Pitfall: TS
  node-types live at `typescript/src/` (G5). Proof: new test module green +
  TC-019's golden command. (FR-006, FR-007; TC-015, TC-016, TC-019)
- [ ] T019 (after T010) [P] kotlin — `src/cairn/parsers/kotlin.py` (receiver
  already at :498 via `_infer_call_receiver_type:643`): failing-first tests,
  then `local_alias` via kotlin's own parser work against the vendored
  grammar (D-009 — no new pin; the 1.1.0 node shapes do not apply) and
  arity; regenerate golden. Proof: new test module green + TC-019's golden
  command. (FR-006, FR-007; TC-015, TC-019)
- [ ] T020 (after T010) [P] objc — `src/cairn/parsers/objc.py`: failing-first
  tests, then receiver extraction (:336 mentions "receiver" only in an
  array-subscript comment today) and arity; regenerate golden. Proof: new
  test module green + TC-019's golden command. (FR-007; TC-016, TC-019)
- [ ] T021 (after T010) [P] php — `src/cairn/parsers/php.py` (receiver
  already at :364): failing-first tests, then `local_alias` emission (F2.1)
  and arity; regenerate golden. Pitfall: entry point is `language_php()`,
  not `language()` (G3). Proof: new test module green + TC-019's golden
  command. (FR-006, FR-007; TC-015, TC-019)
- [ ] T022 (after T010) [P] ruby — `src/cairn/parsers/ruby.py` (receiver
  already at :242): failing-first tests, then arity counting (ruby is a
  single `call` node with `receiver`+`method` fields, G4 — no member wrapper
  to parse around); regenerate golden. Proof: new test module green +
  TC-019's golden command. (FR-006, FR-007; TC-019)
- [ ] T023 (after T010) [P] swift — `src/cairn/parsers/swift.py`: failing-
  first tests, then receiver via `navigation_expression.target` one level
  down (G1 — swift 0.7.3 `call_expression` has NO field labels; reading
  `call_expression.function` fails silently), positional alias extraction,
  arity; regenerate golden. Proof: new test module green + TC-019's golden
  command. (FR-006, FR-007; TC-015, TC-016, TC-019)

## Phase 5: Resolver consumption & tier refinement (FR-006, FR-008)
<!-- Checkpoint: standing gate green (tier-contract pins); AC6 shape tests
     green — import-alias targets and same-name overloads resolve exact with
     regression tests (S-12's vehicle, no resolution GT exists);
     `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest
     tests/test_type_tier.py tests/test_resolver_type_scoped_fallback.py -q`. -->
- [ ] T024 (after T012..T023 — consumes their emitted signals) Author the
  failing-first AC6 regression tests (C-02; resolution ground truth is the
  per-shape test vehicle, S-12): new resolver tests named for the
  import-alias shape (`import module as m` → `m.func()`; `from pkg import
  name as local` → `local()` — ambiguous at baseline, S-10) asserting exact
  resolution to the aliased definition (TC-015), and for the arity shape —
  same-named overloads distinguished by argument count resolving exact while
  an equal-arity tie stays ambiguous (TC-017) — plus re-export-shaped
  fixtures (`export *`, `__init__` stars) asserting continued ambiguity
  (D-003 abstention). Red before T025. Proof of red: the new module fails
  under `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest <module> -q`;
  `grep -rniE 'def test_.*import_?alias' tests --include='*.py'` lists the
  new tests (verified empty at baseline, TC-015). (FR-006; TC-015, TC-017)
- [ ] T025 (after T024, after T011) Implement the resolver consumption in
  `src/cairn/graph/resolver.py` (single-file, one owner — internally
  sequential per plan): extend `build_import_index:39` to also return
  `{file_id → {alias → imported_path}}` from the `imports.local_alias`
  column T011 persists; in `resolve_edge:177` rewrite a bare `target_name`
  matching a local alias to the imported path's final segment BEFORE the
  tier walk so Tier 2's DIRECT suffix match (resolver.py:284-285) binds it
  (D-003; re-export shapes have no alias recorded → abstain); add the arity
  tiebreak ONLY at branches that already return ambiguous (D-005/D-008) —
  >1 candidates with exactly one arity match via the 7th in-memory
  `call_arity` tuple element (wired through builder.py:1156-1162's append,
  tolerating the 5/6/7-tuple contract at `resolve_repo_edges:338`, read at
  :354) → exact; ≥2 matching or NULL arity → abstain; tier ordering
  UNTOUCHED (D-008). Turns T024 green. Proof: T024's module green;
  `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_type_tier.py tests/test_resolver_type_scoped_fallback.py -q`
  (TC-021) and `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_type_tier.py::test_receiver_type_none_is_abstain_safe -q`
  (TC-025) and `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_invariants.py::test_invariant_exact_resolution_has_target_id -q`
  (TC-024). (FR-006, FR-008; TC-015, TC-017, TC-021, TC-024, TC-025)

## Phase 6: Before/after proof & closeout (FR-005, FR-009, FR-010)
<!-- Checkpoint: `uv run python benchmarks/datasource/ds2/verify_dataset.py`
     → 558/558 exit 0; `uv run python scripts/verify_ground_truth.py` →
     exit 0; Phase-1 recipe re-run on both corpora shows strictly higher
     exact share (AC4) with the exact⇒target_id invariant green throughout;
     wall-time within 15% of 198.7 s (FR-010); CHANGELOG carries exactly one
     new entry, history untouched. -->
- [ ] T026 (after T008, T009, T025) Re-measure BOTH corpora with the pinned
  T001 recipe and throwaway script (same SQL, same copy+marker idiom):
  post-change self-repo share strictly > 0.2236 (TC-012) and post-change ds2
  share strictly > T001's recorded ds2 number (TC-013), with the ds2 shape
  staying 50 files / 6239 edges (pinned by TC-022's facts); run the
  per-language variant —
  `SELECT f.language, ROUND(1.0 * SUM(e.resolution = 'exact') / SUM(e.resolution IN ('exact','ambiguous')), 4) FROM edges e JOIN symbols s ON e.source_id = s.id JOIN files f ON s.file_id = f.id WHERE e.kind IN ('calls','references') GROUP BY f.language`
  — asserting every language ≥ its S-11 baseline entry (python 0.3664,
  javascript 0.4573, ruby 0.4762, kotlin 0.6609, …; TC-020); record both
  before/after pairs as done-notes. Also verify the vacuous-denominator
  boundary: `bash -c 'set -e; tmp=$(mktemp -d); mkdir -p "$tmp/r/.git"; printf "import json\njson.dumps({})\n" >"$tmp/r/m.py"; uv run cairn build --workspace "$tmp/r" --db "$tmp/g.db"'`
  (TC-014). (FR-005; TC-012, TC-013, TC-014, TC-020)
- [ ] T027 (after T025) [P] Run the retrieval ground-truth gates (AC5, zero
  false-exact): `uv run python benchmarks/datasource/ds2/verify_dataset.py`
  → exit 0 with "558/558 expectations tier-1-exact" and attrs build facts
  50 files / 1722 symbols / 6239 edges (TC-022);
  `uv run python scripts/verify_ground_truth.py` → exit 0 (TC-023). Any
  regression is a blocker, not a re-baseline. (FR-009; TC-022, TC-023)
- [ ] T028 (after T026) [P] Verify the FR-010 wall-time gate: resolve-phase
  wall-time from T026's `build_runs` rows (schema.py:350-362) within the
  15% advisory materiality vs the 198.7 s self-repo baseline (S-11/S-12) —
  record the number as the done-note; run/observe the bench comparison
  (cairn bench, perf suite, hash-embed backend, three repeats) expecting
  exit 0 per the 0/1/2 contract; an exit-2 demands explicit justification
  against the recorded baselines before closeout (TC-026, MANUAL). Also
  confirm no new runtime dependency: `git diff dc9882b -- pyproject.toml | grep -E '^\+\s*"'; test $? -eq 1`
  (TC-018). (FR-010; TC-018, TC-026)
- [ ] T029 (after T026, T027, T028) Append exactly ONE new CHANGELOG entry
  describing this change (tree-sitter-only indexing + exact-rate work);
  every historical entry stays byte-identical (D-010, spec.md:65 ruling).
  Proof: `git diff dc9882b -- CHANGELOG.md | grep '^-[^-]'; test $? -eq 1`
  — additions only, no removed content line (TC-011). (FR-004; TC-011)

## Conventions
- `- [ ]` todo · `(in-progress)` claimed · `- [x]` done + proof note:
      `done <date> — <test/command that proves it>`
- Dropped: `- [ ] ~~T004~~ dropped <date> (D-###)` — never delete the line;
  dropped tasks stay visible with the decision that killed them
- `[P]` = parallelizable (default — no shared files, no upstream task);
  chained tasks note `(after T###)` and name the exact interface they
  consume from their upstream — symbols, signatures, file formats; serial
  runs need a reason, parallel runs need none
- Fix rounds append `(fix <n>/5)` to the entry — the cap survives resume
  only if the count lives here, in the status holder. From round 2 on, an
  implementer's scratch note (what was tried, why it failed) may live at
  `notes/T###.md` — the one file an implementer may write under specs/,
  never read by check.py, never counted as status
- Every task cites its FR-###; tasks with no FR are scope creep — fix the
  spec first

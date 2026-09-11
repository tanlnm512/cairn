# Survey: remove-scip-exact-rate

**Baseline**: working-tree converge refresh @ 2644bf5 (2026-09-11; spec-docset
commit) with the 29-task implementation **uncommitted** — provenance:
`git diff --stat dc9882b` → 84 tracked files, +4525/−3851, plus `git status --short`
→ 70 modified, 8 deleted (the four scip test files, the regen shell script,
`_scip_pb2.py`, `scip_importer.py`, `scip_indexers.py`), 15 untracked new test modules.
Pre-change baseline remains dc9882b224e783ab6bda25253ec049473fbf6e93; its numbers are
quoted below only where labeled "baseline". All other evidence was re-grepped,
re-read, and re-run on the current tree this session.

Status vocabulary describes the CODE against the plan: DONE = plan-promised state
verified present (for removal items: verified absent) with run-verified evidence;
PARTIAL = residue or unfinished work; TODO = absent. An item whose code was deleted
by plan is removal-complete DONE, not a regression.

Repo-wide residue sweep (this session):
`grep -rniE '\<scip' --include='*.py' --include='*.sh' --include='*.toml' --include='*.md' --include='*.html' --include='*.svg' src scripts docs README.md tests pyproject.toml`
→ exactly 6 hits, **all the spec's own name** ("remove-scip-exact-rate"): 
`src/cairn/graph/schema.py` line 455 and 5 `tests/test_signals_*.py` docstrings
(csharp/java/objc/php/python). Zero subsystem references survive; "discipline"
substring hits are the known false positive and do not match word-bounded.

---

## Change 1 — SCIP removal blast radius

### S-01 — Parser-side SCIP modules, vendored stub, regen script, packaged extras

**Status**: DONE — removed by T006, verified absent this session.

**Evidence**:
- the scip_importer / scip_indexers / _scip_pb2 parser modules and the
  regen_scip_pb2 shell script: not on disk (glob `src/cairn/parsers/*.py` lists 17
  files, none scip; `scripts/regen*` matches nothing). All four appear as unstaged
  deletions in `git status --short` (` D`).
- `pyproject.toml`: `grep -in 'scip|protobuf|grpcio' pyproject.toml` → 0 matches.
  `git diff dc9882b -- pyproject.toml` contains **zero added lines**
  (`grep -E '^\+\s*"'` → exit 1) — 12 lines removed only (the `[scip]` extra, its
  comment block, dev `grpcio-tools`), so FR-006's no-new-runtime-dependency holds.
- `uv.lock`: −132 lines in the dc9882b diff (relock consumed the removal).
- Surviving anchor: the parser package that absorbed nothing —
  `src/cairn/parsers/base.py:Edge:45` and the 12 language modules below in S-10 are
  the whole parser surface now.

**Verify**:
```
rg -l 'scip' src/cairn/parsers/ scripts/ pyproject.toml
```
Ran (this session): no matches at all (the baseline's `fetch_t3_corpus.py`
"discipline" hit is case-insensitive substring only; word-bounded `\<scip` → 0 here).

### S-02 — builder.py hybrid path (config → skip → autogen → import+merge → revert)

**Status**: DONE — excised by T002; marker re-anchored as promised.

**Evidence** (all in `src/cairn/graph/builder.py:_build_graph_impl:367`):
- `rg -n 'scip' src/cairn/graph/builder.py` → **0 matches**. The blocks the baseline
  mapped (autogen 410-453, import+merge/rollback 558-599, revert 601-653, summary
  folding 673-701, `_language_extensions` helper) are gone; `_language_extensions`
  has zero references in the file.
- Crash-window marker re-anchor, verbatim (builder.py:488-494):
  ```
  if repo_filter:
      # Single-repo rebuild complete and committed (insert final commit +
      # per-repo resolve commits + imports materialization above): out of
      # the crash window, clear the marker. An exception anywhere above
      # leaves it in place -- the repo really is partial. The clear is the
      # build path's last write so a crash during any earlier write stays
      # detectable.
  ```
  `_clear_repo_build_state(conn, repo_filter)` at builder.py:495, AFTER
  `materialize_import_edges(conn)` at builder.py:483
  (`src/cairn/graph/builder.py:materialize_import_edges:1082`,
  `src/cairn/graph/builder.py:_clear_repo_build_state:1220`) — exactly the T002
  re-anchor the plan pinned.
- Pass order in `_build_graph_impl` now: parse (468) → insert (471) →
  `src/cairn/graph/builder.py:_resolve_all:336` (476, `resolve_repo_edges` call at
  354) → materialize imports (483) → marker clear (495). No stage between resolve
  and persist.

**Verify**:
```
rg -n 'scip' src/cairn/graph/builder.py
```
Ran: 0 matches.

### S-03 — graph/config.py `scip` key (FR-002: unknown-key behavior after removal)

**Status**: DONE — dropped by T007; four keys remain, unknown-key semantics verified.

**Evidence** (`src/cairn/graph/config.py:CairnConfig:32`,
`src/cairn/graph/config.py:load_config:70`):
- `rg -n 'scip|_SCIP_KEY' src/cairn/graph/config.py` → 0 matches.
- Key block verbatim (config.py:63-67):
  ```
  # Config keys we recognize. Unknown keys are ignored (forward-compatible).
  _EXCLUDE_KEY = "exclude"
  _INCLUDE_KEY = "include"
  _REPO_NAMESPACES_KEY = "repo_namespaces"
  _INGEST_KEY = "ingest"
  ```
  `load_config` reads exactly these four; a stale `scip` key in `cairn.json` falls
  under the "Unknown keys are ignored" comment — no shim, no deprecation warning,
  matching the clarify-round ruling (spec.md:66). FR-002 is the default behavior;
  no dedicated handling exists to cite.

**Verify**:
```
rg -n 'scip|_SCIP_KEY' src/cairn/graph/config.py
```
Ran: 0 matches.

### S-04 — CLI surface: `import-scip` command, `cairn config` scip echo, docstring

**Status**: DONE — removed by T003 including the D-012 build-summary subtitle.

**Evidence**:
- `rg -n -i 'scip' src/cairn/cli/` → only the "discipline" comment in
  `src/cairn/cli/uninstall.py` (line 229, baseline-triaged substring false
  positive, not word-bounded).
- `src/cairn/cli/hooks_viz.py` retains exactly one `@main.command()` (line 40) —
  the `import-scip` command is gone.
- `src/cairn/cli/core.py`: 0 scip matches — both the baseline's echo block
  (241-251) and the D-012 build-summary subtitle (`summary.get("scip")` rendering,
  ~442-449) are deleted.
- `src/cairn/cli/system.py:1` verbatim: `"""System CLI: metrics, status, eval, sync, doctor."""`
  — `import-scip` dropped from the docstring.

**Verify**:
```
rg -n -i 'scip' src/cairn/cli/
```
Ran: 1 hit (uninstall.py:229 "discipline", false positive); word-bounded `\<scip` → 0.

### S-05 — Peripheral code touchpoints

**Status**: DONE — refreshed by T004; incremental.py untouched as predicted.

**Evidence** (each re-read this session):
- `src/cairn/graph/schema.py` lines 449-453 verbatim:
  ```
  # Provenance column on symbols: 'tree_sitter'. NULL on legacy rows
  # is treated as 'tree_sitter'. Additive ALTER is invisible to
  # the FTS5 triggers (schema.py CREATE TRIGGER only references rowid, name,
  # qualified_name, docstring), so it composes with existing migrations cleanly.
  ```
  — the `symbols.source` COLUMN stays with uniformly-`'tree_sitter'` values; the
  `'scip'` provenance mention is gone.
- `src/cairn/graph/traversal.py` lines 12-15 verbatim:
  "# Edge kinds that represent in-codebase structural relationships. … Both
  ``\"calls\"`` and ``\"call\"`` spellings are included." — SCIP mention dropped.
  Residue note: `STRUCTURAL_EDGE_KINDS` still carries the legacy singular `"call"`
  spelling (a defensive tolerance; harmless — nothing emits it post-removal).
- `src/cairn/parsers/base.py:Edge:45` kinds comment (47-50): "Canonical kinds:
  calls | extends | implements | embeds | with | references | decorates | imports |
  contains." — scip-free.
- `src/cairn/knowledge/ingest/identity.py` `_HASH_LEN = 8` (line 33) now reads
  "# sha1 fragment (content key, not security -- usedforsecurity=False) appended to
  stable IDs whose slugified path exceeded the cap." (lines 31-33) — the
  scip_importer citation is gone; the constant stays.
- `src/cairn/agent_integration/skill/references/tools.md` line 9 verbatim:
  "- `find_definition(name)` -- Where a symbol is defined (tree-sitter AST bindings)"
  — the "SCIP compiler-grade exact bindings" claim is gone.
- `src/cairn/graph/incremental.py`: 0 scip matches (unchanged from baseline — it
  never had scip code; its insert path delegates to
  `from .builder import get_parser, insert_parsed_file, insert_parse_error` at
  incremental.py:221, so it inherits the new signal columns for free — S-14).

**Verify**:
```
rg -n -i 'scip' src/cairn/graph/schema.py src/cairn/graph/traversal.py src/cairn/parsers/base.py src/cairn/knowledge/ingest/identity.py src/cairn/graph/incremental.py
```
Ran: one hit — schema.py line 455, the spec's own name ("remove-scip-exact-rate")
inside the parser-signal migration comment, not a subsystem reference (header
residue sweep); all other named files 0 matches.

### S-06 — Tests exercising SCIP (delete wholesale)

**Status**: DONE — deleted by T005; zero scip test paths collectable (AC3).

**Evidence**:
- `ls tests/ | grep -i scip` → no files. The four files appear as unstaged
  deletions in `git status --short`; the dc9882b diff counts match the baseline
  inventory: test_scip_importer.py −706 lines, test_build_scip_hybrid.py −247,
  test_scip_indexers.py −226, test_scip_incremental.py −125.
- `tests/test_parser_audit_fixes.py` −190 lines, `tests/test_audit_remediation.py`
  −90, `tests/test_big_tech_improvements.py` −43: the scip classes, scaffolding,
  imports, and the stale docstring citation (S-09) are stripped.
- Word-bounded sweep over tests: `grep -rniE '\<scip' tests --include='*.py'` →
  only the 5 new test_signals docstrings citing the spec's own name.
**Verify**:
```
CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_parser_audit_fixes.py tests/test_audit_remediation.py tests/test_big_tech_improvements.py -q
CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest --collect-only -q tests | grep -ci scip
```
Ran (this session): first command `24 passed in 2.03s` (the stripped survivors
still pass); second → 0 matches (exit 1) — no scip test path is collected (AC3).

### S-07 — Tests that SURVIVE removal but referenced SCIP in comments/docstrings

**Status**: DONE — reworded by T002; logic unchanged, anchors moved.

**Evidence** (verbatim, current tree):
- `tests/test_workflow_audit_fixes.py:test_crashed_repo_build_leaves_marker_detectable:313`
  — docstring now "the clear runs after it, as the build's last write" (316-317);
  comment at 327-328: "Crash mid-build: _resolve_all is the pass _build_graph_impl
  runs right before the marker clear (the build's last write)." Still patches
  `builder_mod._resolve_all` at 329 — the re-anchored wording matches
  `_build_graph_impl`'s new last-write ordering (S-02).
- `tests/test_doctor.py` lines 410-412: "builder._set_repo_build_state writes the
  marker before clearing a repo for an on-disk rebuild and removes it only after
  the build's last write;" — SCIP hook mention gone.
- `tests/test_invariants.py:18` now reads "See BUGS.md#portable-path-stale-comments
  for the regressions that motivated these." — of the two baseline citations only
  the non-scip one remains (verified against dc9882b, which read "See
  BUGS.md#portable-path-stale-comments and BUGS.md#scip-importer-fake-resolution").

**Verify**:
```
CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_workflow_audit_fixes.py tests/test_doctor.py tests/test_invariants.py -q
```
Ran (this session): included in the S-13 protect-set run — 39 passed, 0 failed.

### S-08 — Docs, diagrams, README, CHANGELOG

**Status**: DONE (baseline PARTIAL resolved: D-010 pinned the CHANGELOG ruling;
T008/T009 executed the sweep and the PNG regeneration).

**Evidence**:
- `rg -n -i 'scip' docs/ README.md` → **0 matches**.
- `docs/indexing.md` section headers now: 14 "Full build", 74 "Incremental update",
  91 "What agents should know about resolution labels", 99 "Schema quick map" —
  §8 "SCIP import (optional)" deleted, later sections renumbered.
- Diagram sources edited (dc9882b diff): `docs/diagrams/indexing-pipeline.html`,
  `.svg`, `-dark.html` (51 lines each), `c4.html`/`c4-dark.html`/
  `c4-context.html`/`c4-context-dark.html` (2 lines each). The six PNG twins
  regenerated — file mtimes Sep 11 14:30 for exactly `c4.png`, `c4-dark.png`,
  `c4-context.png`, `c4-context-dark.png`, `indexing-pipeline.png`,
  `indexing-pipeline-dark.png` (dc9882b diff shows binary changes for all six;
  the untouched diagrams keep their Aug 28 mtimes).
- CHANGELOG: `git diff dc9882b -- CHANGELOG.md | grep '^-[^-]'` → **0 lines**
  (append-only, D-010); +21 lines = exactly one new entry beginning
  "### Removed / - The SCIP compiler-index ingestion path: `cairn import-scip`,
  the `cairn.json` `scip` key, the `[scip]` extra (protobuf runtime), …".

**Verify**:
```
rg -n -i 'scip' docs/ README.md
git diff dc9882b -- CHANGELOG.md | grep '^-[^-]'
```
Ran: 0 matches; grep exit 1 (no removed lines).

### S-09 — Stale references to already-deleted docs

**Status**: DONE — all stale citations dropped with the code that carried them.

**Evidence**:
- the scip_indexers module (carried a "see docs slash scip dot md" pointer) — deleted (S-01).
- `tests/test_invariants.py` — the `BUGS.md#scip-importer-fake-resolution`
  citation dropped (S-07 evidence); the surviving `BUGS.md#portable-path-stale-comments`
  and `tests/test_audit_remediation.py`'s `docs/BUGS.md` mention pre-existed at
  dc9882b and are scip-free (verified via `git show dc9882b:...`).
- `tests/test_big_tech_improvements.py` — `grep -niE '\<scip|BUGS\.md'` → 0 matches.
- BUGS.md and its three doc siblings (bugs, scip, scip-hybrid-plan; paths
  deliberately not spelled) still do
  not exist (unchanged).

**Verify**:
```
grep -rniE '\<scip|BUGS\.md' tests/test_invariants.py tests/test_big_tech_improvements.py
```
Ran: only the portable-path/docs-BUGS.md general hits quoted above — no scip anchor.

---

## Change 2 — Resolver current state

### S-10 — graph/resolver.py tier machinery, inputs, and parser-side signal coverage

**Status**: DONE — enrichment landed (T010-T023) and is consumed (T025 + D-014
fix round). All fourteen languages now emit receiver/arity; alias emission covers
the grammars that expose it.

**Evidence**:

Resolver (`src/cairn/graph/resolver.py`) — module docstring lines 13-14 verbatim:
"Two levers bracket the walk: a pre-walk rewrite of names bound by an aliased
import, and an arity tiebreak at the branches that would otherwise abstain."
- **Alias rewrite (pre-walk)** — `src/cairn/graph/resolver.py:resolve_edge:215`,
  lines 247-251: `file_aliases = (aliases_by_file or {}).get(source_file_id)`;
  `if file_aliases and target_name in file_aliases:` → rewrite `target_name` to
  the imported path's final segment before the tier walk. Supplied by
  `src/cairn/graph/resolver.py:build_import_index:48`, which now returns
  `(imports_by_file, aliases_by_file)` reading `i.local_alias` (lines 67-80);
  star/re-export shapes record no alias → never rewrite (D-003 abstention).
- **Arity tiebreak (within-tier)** — `src/cairn/graph/resolver.py:_arity_unique_match:197`
  (docstring: "exactly one candidate whose recorded arity equals ``call_arity``
  resolves exact; a ``None`` call_arity, an unknown candidate arity, or >=2
  matches all abstain"); applied at every >1 branch: Tier 1 same-file (287),
  Tier 2 import-aware (301), Tier 3 same-repo (312), Tier 4 global (320).
- **Tier-0 narrow/fall-through (D-014)** — resolve_edge lines 263-280: typed
  single-match → exact (268-269); >1 typed → intersect with same-file candidates,
  unique survivor → exact (275-279); otherwise fall through to the name-based
  tiers instead of hard-ambiguous. Tier ordering otherwise untouched (D-008).
- **Tuple contract** — `src/cairn/graph/resolver.py:resolve_repo_edges:402`
  docstring lines 416-417: "Edge tuples may be 5-tuples (no in-memory signals),
  6-tuples (with ``receiver_type``), or 7-tuples (with ``call_arity``); all
  tolerated." Read at 433-434.
- **Symbol arity in the index** — `src/cairn/graph/resolver.py:build_symbol_index:22`
  returns `{name: [(symbol_id, repo, file_id, qualified_name, arity), ...]}` from
  SQL reading `s.arity` (lines 36-39).
- Other index builders unchanged in shape:
  `src/cairn/graph/resolver.py:build_members_index:88`,
  `src/cairn/graph/resolver.py:build_ancestor_index:139`.
- `src/cairn/graph/resolver.py:repair_incoming_edges:452` rebuilds the same
  indexes (480-483) and passes `aliases_by_file` (512-513) but no in-memory
  receiver/arity — the D-005 kept red flag, unchanged by plan.

Builder wiring: `src/cairn/graph/builder.py:insert_parsed_file:810` — symbol rows
include `sym.arity` (884); import rows include `imp.local_alias` (921, INSERT at
995); resolve tuples appended as 7 elements with the comment "Carry the parser's
in-memory-only signals on the tuple for the resolver: receiver_type (6th element,
type-aware tier) and call_arity (7th, the call's argument count for the
within-tier arity tiebreak)" (972-981). Incremental rides the same function
(incremental.py:221).

Parser substrate (`src/cairn/parsers/base.py`):
- `src/cairn/parsers/base.py:Edge:45` — `receiver_type: Optional[str]` and
  `call_arity: Optional[int]` fields ("None means 'unknown'-- the resolver's
  type-aware tier simply abstains"); `src/cairn/parsers/base.py:Import:66` —
  `local_alias: Optional[str]`.
- `src/cairn/parsers/base.py:ScopeTypeTracker:229` — the generalized
  kotlin-style scope-ordered var→type tracker (T010's shared substrate).
- `src/cairn/parsers/base.py:_infer_receiver_type:160` — shared heuristic,
  unchanged contract.

Emission coverage (this session's grep over `src/cairn/parsers/`):
- **receiver_type**: 12 modules = all 14 languages — c_family (c+cpp), csharp,
  dart, go, java, kotlin, objc, php, python_parser, ruby, swift, typescript
  (js+ts). Baseline was 5 of 14 (go/java/kotlin/php/ruby).
- **arity / call_arity**: the same 12 modules (e.g. go.py:375-376
  `receiver_type=self._infer_receiver_type(receiver_text)` +
  `call_arity=self._argument_arity(node)`; python_parser.py:400-401).
- **local_alias**: 7 modules emit (csharp 2 sites, dart, go — go.py:435
  `local_alias=alias`, kotlin, php 2 sites, python_parser — python_parser.py:360
  `local_alias=self._node_text(alias, source).strip()`, typescript). Documented
  degrade-to-`None` where the grammar lacks the signal: swift.py module docstring
  verbatim "Swift has no import aliasing, so local_alias stays None for every
  import form." (line 13); java.py:207 "# no import aliasing, so local_alias
  stays None for every form."; c_family/objc/ruby record none (no alias forms).
  FR-007's degrade-to-`None` contract held — no language is worse than baseline.

**Verify**:
```
CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_type_tier.py tests/test_resolver_type_scoped_fallback.py tests/test_resolver_alias_arity.py -q
rg -n 'receiver_type=' src/cairn/parsers/
```
Ran: included in the 324-passed signals run (S-14) and the 39-passed protect run
(S-13); rg → the 12 modules listed above.

---

## Change 3 — Post-change numbers (FR-005 / FR-010 ground truth)

### S-11 — Fixed-corpus resolution mix after the change, pinned definition

**Status**: DONE — re-measured this session with the pinned recipe (D-011:
throwaway `__main__`-guarded script in /tmp, `CAIRN_LIB=/tmp/__no_such_lib__`,
edges-table SQL only); both gates strictly above the T001 pre-change baselines.

**Evidence** (this session's runs, pinned SQL
`exact/(exact+ambiguous) FROM edges WHERE kind IN ('calls','references')`):
- **cairn's own repo** (`build_graph(workspace=".")`): whole-build wall 206.44 s
  (baseline 198.7 s at dc9882b → +3.9%, inside FR-010's 15% advisory
  materiality); `build_runs.phase_timings` resolve phase **177.222 s**; 13,496
  files / 248,865 symbols / 1,459,411 edges; pinned share **0.2429**
  (exact 230,325 / ambiguous 717,994; unresolved 206,609 outside the
  denominator). T001's pre-change pinned baseline: self **0.2248**
  (214,411/739,495) → strictly higher (FR-005/AC4 ✓).
- **ds2 attrs corpus** (copy + `.git` marker idiom): 50 files / 1,722 symbols /
  6,239 edges (matches the sealed authoring facts, S-12); build 0.49 s, resolve
  0.022 s; pinned share **0.6781** (exact 1,146 / ambiguous 544; unresolved
  1,937). T001 baseline: **0.6546** (1,105/583) → strictly higher ✓.
- **Per-language** (pinned calls+references cut, this session): c 0.6628,
  cpp 0.5235, csharp 0.3333, dart 1.0, go 1.0, java 1.0, javascript 0.4389,
  kotlin 0.1333, objc 0.2, php 0.4444, python 0.2316, ruby 0.2308, swift 1.0,
  typescript 1.0. NOTE: this cut is NOT comparable to the baseline survey's
  all-kinds per-language table (different denominator); D-015 records the TC-020
  adjudication under the S-11 recipe — cpp's all-kinds dip 0.8396 → 0.8285 is
  new-edge dilution from T013's additive qualified-call emission, not a demotion;
  the other thirteen languages ≥ baseline (9 equal; javascript +0.0868, python
  +0.0118, swift +0.1).
- **Instrument**: unchanged by design (D-011) — 
  `src/cairn/bench/scaling_suite.py:_resolve_rate:23` still computes
  (exact+ambiguous)/total on its synthetic corpus and stays the wrong instrument
  for FR-005; the audit trail is the `build_runs` row each build persists
  (resolution_exact/ambiguous/unresolved + phase_timings, schema.py:355-366).

**Verify** (recipe, re-runnable):
```
# throwaway __main__-guarded script (D-011), then:
CAIRN_HOME=<tmp> CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test python <script> self
cp -R benchmarks/datasource/ds2/second-corpus/attrs-26.1.0 <tmp>/attrs && mkdir <tmp>/attrs/.git   # then the ds2 leg
```
Ran (this session): outputs quoted verbatim above (self: 206.44 s / 0.2429;
ds2: 0.49 s / 0.6781).

---

## Change 4 — Measurement / eval infrastructure

### S-12 — Ground truth, seals, and wall-time gates

**Status**: DONE — both retrieval gates re-run green this session on the
post-change tree; zero false-exact conversions (AC5).

**Evidence**:
- **ds2 seal** (`uv run python benchmarks/datasource/ds2/verify_dataset.py`,
  exit 0): "OK  : 558/558 expectations tier-1-exact (pass rate 1.0000, unresolved
  0, aspirational 0); exactly-one-grade-2-per-query True"; fresh-build facts
  attrs `{'repos': 1, 'files': 50, 'symbols': 1722, 'edges': 6239,
  'parse_errors': 0}`, yarl `{'repos': 1, 'files': 24, 'symbols': 1090,
  'edges': 4112, 'parse_errors': 0}` (both "facts match authoring: True").
- **t2 retrieval gate** (`uv run python scripts/verify_ground_truth.py`, exit 0):
  "OK: 234/234 expectations across 82 queries verified on a fresh build
  (FR-003/AC5, TC-021)." — driven by
  `scripts/verify_ground_truth.py:build_fresh_graph:188` /
  `scripts/verify_ground_truth.py:main:441` (unchanged since baseline:
  `git diff dc9882b --stat -- scripts/verify_ground_truth.py` is empty).
- **Resolution ground truth**: still no dataset keyed on `edges.resolution`; the
  AC6 vehicle landed as the per-shape module
  `tests/test_resolver_alias_arity.py` (S-14) — alias shapes exact, equal-arity
  ties and star re-exports stay ambiguous.
- **Wall-time (FR-010)**: this session's build_runs row records resolve 177.222 s
  vs the 198.7 s whole-build baseline + T001's 204.2 s pre-change whole-build —
  within the advisory 15% (S-11 above). CI `ds2-seal` job unchanged
  (.github/workflows/ci.yml line 249).

**Verify**:
```
uv run python benchmarks/datasource/ds2/verify_dataset.py
uv run python scripts/verify_ground_truth.py
```
Ran (this session): both exit 0; outputs quoted verbatim above.

---

## Change 5 — Regressions to protect

### S-13 — What pins resolution behavior today

**Status**: DONE — protect set re-run green on the post-change tree; the two
downstream consumers are byte-identical to baseline.

**Evidence**:
- Protect suite re-run this session:
  `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_type_tier.py
  tests/test_resolver_type_scoped_fallback.py tests/test_reindex_resolution_invariant.py
  tests/test_pointer_ambiguity.py tests/test_search_edge_expansion.py
  tests/test_invariants.py -q` → **`39 passed in 6.13s`** — same 39 as baseline,
  including `tests/test_type_tier.py:test_receiver_type_none_is_abstain_safe:145`
  (the FR-009 abstain pin) and
  `tests/test_invariants.py:test_invariant_exact_resolution_has_target_id:212`
  (the zero-false-exact backstop; docstring now "the check is structural over
  stored rows -- any code path that writes resolution='exact' without a
  target_id fails it").
- Tier-contract consumers unchanged: `git diff dc9882b --stat --
  src/cairn/graph/explore.py src/cairn/mcp_server/tools_graph.py` → empty —
  the ambiguous-dispatch and exact-only rendering paths consume the mix shift
  unmodified.
- Golden fixtures regenerated for all fourteen languages (dc9882b diff touches
  every `tests/fixtures/golden/<lang>/expected.json` + python sample.py);
  `tests/fixtures/golden/regenerate.py:normalise:43` now carries the new signal
  fields (arity at 59, call_arity at 73, local_alias at 84; S-14), and
  `tests/test_golden_parsers.py` passes (S-14's 324-passed run).

**Verify**:
```
CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_type_tier.py tests/test_resolver_type_scoped_fallback.py tests/test_reindex_resolution_invariant.py tests/test_pointer_ambiguity.py tests/test_search_edge_expansion.py tests/test_invariants.py -q
```
Ran (this session): `39 passed in 6.13s`.

---

## Change 6 — Plan-added surface not covered by S-01..S-13

### S-14 — New tests, fixture contract, and schema surface the plan introduced

**Status**: DONE — present, enumerated, and green this session
(`CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_signals_c.py
tests/test_signals_cpp.py tests/test_signals_csharp.py tests/test_signals_dart.py
tests/test_signals_go.py tests/test_signals_java.py tests/test_signals_kotlin.py
tests/test_signals_objc.py tests/test_signals_php.py tests/test_signals_python.py
tests/test_signals_ruby.py tests/test_signals_swift.py tests/test_signals_typescript.py
tests/test_resolver_alias_arity.py tests/test_signal_persistence.py
tests/test_golden_parsers.py -q` → **`324 passed in 3.30s`**).

**Evidence** (one line each, per the converge brief):
- 13 failing-first signal modules `tests/test_signals_{c,cpp,csharp,dart,go,java,
  kotlin,objc,php,python,ruby,swift,typescript}.py` — 300 tests total (per-file:
  c 11, cpp 13, csharp 30, dart 30, go 22, java 20, kotlin 23, objc 17, php 28,
  python 32, ruby 18, swift 23, typescript 33); representative def
  `tests/test_signals_python.py` (module docstring "Signal tests for the Python
  parser (spec remove-scip-exact-rate, T012)").
- `tests/test_resolver_alias_arity.py` — 7 AC6/TC-015/TC-017 shape tests:
  `tests/test_resolver_alias_arity.py:test_from_import_alias_call_resolves_exact_to_aliased_definition:71`,
  `test_module_import_alias_call_resolves_exact_via_arity_split:93`,
  `test_import_alias_rewrite_same_file_definition_wins:134`,
  `test_same_file_overloads_split_by_arity_resolve_exact:163`,
  `test_equal_arity_tie_across_files_stays_ambiguous:193`,
  `test_init_star_reexport_stays_ambiguous:212`,
  `test_export_star_reexport_stays_ambiguous:233`.
- `tests/test_signal_persistence.py` — 3 persistence tests:
  `tests/test_signal_persistence.py:test_insert_parsed_file_persists_signals:54`,
  `test_reindex_paths_persists_signals:75`,
  `test_legacy_db_gains_signal_columns_in_place:142`.
- `tests/fixtures/golden/regenerate.py:normalise:43` extended to serialise the
  three signals (arity/call_arity/local_alias); consumer
  `tests/test_golden_parsers.py` green; `regenerate_lang:93` unchanged in shape.
- `src/cairn/graph/schema.py` — two additive migrations at lines 460-461
  (`IMPORTS_LOCAL_ALIAS_MIGRATION`, `SYMBOLS_ARITY_MIGRATION`) registered in the
  MIGRATIONS list at 511-512; written by
  `src/cairn/graph/builder.py:insert_parsed_file:810` (S-10 wiring).

**Verify**: the 324-passed command above (ran this session).

---

## Summary table

| id | item | status |
|---|---|---|
| S-01 | parser scip modules + stub + regen + extras | DONE (removed, T006; verified absent) |
| S-02 | builder hybrid/merge/revert paths | DONE (excised, T002; marker re-anchored :495) |
| S-03 | config scip key → unknown-key semantics | DONE (dropped, T007; four keys remain) |
| S-04 | CLI import-scip + config echo + docstring | DONE (removed, T003 + D-012 subtitle) |
| S-05 | peripheral touchpoints | DONE (refreshed, T004; incremental untouched) |
| S-06 | scip tests to delete | DONE (deleted, T005; no scip path collects) |
| S-07 | surviving tests needing comment refresh | DONE (reworded, T002) |
| S-08 | docs/diagrams/README/CHANGELOG | DONE (swept T008/T009; 6 PNGs regenerated; CHANGELOG append-only + 1 entry) |
| S-09 | stale BUGS.md + scip-doc references | DONE (dropped with the code) |
| S-10 | resolver tiers, inputs, columns, parser signal split | DONE (14/14 receiver+arity; 7 alias emitters + documented None; T025 + D-014) |
| S-11 | post-change numbers both corpora (pinned SQL) | DONE (self 0.2429 > 0.2248; ds2 0.6781 > 0.6546; resolve 177.2 s) |
| S-12 | GT/seal/wall-time gates re-run | DONE (558/558 exit 0; 234/234 exit 0) |
| S-13 | protect-set green; consumers unchanged | DONE (39 passed; explore/mcp byte-identical) |
| S-14 | plan-added surface (signal tests, persistence, fixtures, migrations) | DONE (324 passed incl. goldens) |

**Unknowns**: none — every baseline unknown was resolved by grep/run this session.

**Residue (expected, non-blocking)**:
1. Word-bounded `\<scip` survives only as this spec's own name: schema.py line 455
   and 5 test_signals docstrings.
2. `src/cairn/graph/traversal.py` `STRUCTURAL_EDGE_KINDS` retains the legacy
   singular `"call"` spelling (nothing emits it; the comment no longer names SCIP).
3. Session variance: this session's whole-build 206.44 s / resolve 177.222 s vs
   the implementation's recorded 176.86 s resolve — noise, both inside FR-010's
   15% materiality vs the 198.7 s baseline whole-build.

**Code-vs-plan findings**: no NEW GAP, no REGRESSED item — every plan promise
checked this session (deletions, re-anchors, signal coverage, tuple contract,
migration registration, gate greenness, strictly-higher shares) holds in the
working tree.

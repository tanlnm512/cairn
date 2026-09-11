# Survey: remove-scip-exact-rate

**Baseline**: cairn @ dc9882b224e783ab6bda25253ec049473fbf6e93 (HEAD at survey time, 2026-09-11)
`specs/context/structure.md` / `tech.md` existed (last re-verified @ 7663989, 2026-09-04);
drift found and refreshed this session (version 0.18.0 → 0.20.0, MCP tool count 28 → 22).

Repo-wide sweep for this survey: `rg -li scip --glob '!.git'` → 68 files (code, tests,
docs, diagrams, benchmarks, specs). All 68 triaged below or explicitly excluded
(`specs/*` = this spec's own files + INDEX entry; `benchmarks/datasource/**/provenance.json`,
`benchmarks/quality/ablation.*`, `scripts/fetch_t3_corpus.py`, and 14 src/test files are
`discipline`-substring false positives, verified line-by-line).

Status vocabulary here describes the CODE: DONE = exists and fully mapped with
run-verified evidence; PARTIAL = exists but incomplete/conditional; TODO = absent.

---

## Change 1 — SCIP removal blast radius

### S-01 — Parser-side SCIP modules, vendored stub, regen script, packaged extras

**Evidence**:
- `src/cairn/parsers/scip_importer.py` (819 lines, 136 case-insensitive `scip` hits).
  Symbol inventory (this session's rg): `scip_available:60`, `_install_hint:65`,
  `_extract_range:75`, `_enclosing_range:104`, `_resolve_real_file_id:147`,
  `_language_for:168`, `_kind_from_syntax:210`, `_short_name:214`,
  `_resolve_doc_path:227`, `_normalize_project_root:264`,
  `_merge_scip_defs_into_tree_sitter:329`, `_import_protobuf:443`,
  `import_scip_bytes:644`, `import_scip_data:666`, `import_scip_file:789`;
  role constants at lines 36-41, `_TS_ONLY_EDGE_KINDS` 309, `_REPLACEABLE_EDGE_KINDS` 313.
  Verbatim (src/cairn/parsers/scip_importer.py:60-63):
  ```
  def scip_available() -> bool:
      """True iff the protobuf runtime + vendored stub import cleanly."""
      return _PROTOBUF_AVAILABLE
  ```
- `src/cairn/parsers/scip_indexers.py` (410 lines, 69 hits) — orchestrator driving
  external indexers. `try_generate_index:189` with `_INDEX_TIMEOUT_S = 30 * 60` (line 34),
  `_KNOWN_INDEXERS: Dict[str, IndexerSpec]` registry at line 176 (swift, java+kotlin via
  scip-java, typescript, python, go, rust), command builders `_swift_cmd:58` …
  `_scip_rust_cmd:91`, `known_languages:179`, `spec_for:184`.
- `src/cairn/parsers/_scip_pb2.py` — vendored protobuf generated stub (9 hits; generated
  code, no defs worth citing).
- `scripts/regen_scip_pb2.sh` (32 hits) — the dev-only regeneration script.
- `pyproject.toml`: `scip = [` extra at line 126 with `"protobuf>=7.35.1"` (line 127);
  the comment block above (lines 119-125) explains the vendored-stub approach;
  `grpcio-tools>=1.60` at line 87 inside the `dev` extra exists solely to regenerate
  the stub (its comment at line 124: "Regenerating the stub is a dev-only step
  (`scripts/regen_scip_pb2.sh`), never a runtime need").
- Protobuf IS importable in the dev env (`uv run --extra test`): the scip test suite
  runs unskipped (see S-06 "Ran").

**Status**: DONE (all five artifacts exist, self-contained, and are removable; the only
non-test importers of `scip_importer` are builder.py:561 and hooks_viz.py:95 — both
mapped in S-02/S-04; `scip_indexers` is imported only by builder.py:430).

**Verify**:
```
rg -l 'scip' src/cairn/parsers/ scripts/ pyproject.toml
```
Ran (this session): `scip_importer.py scip_indexers.py _scip_pb2.py scripts/regen_scip_pb2.sh pyproject.toml` + `scripts/fetch_t3_corpus.py` (false positive, "discipline" at line 17).

### S-02 — builder.py hybrid path (config → skip → autogen → import+merge → revert)

**Evidence** (all in `src/cairn/graph/builder.py:_build_graph_impl:383`):
- **scip_languages resolution + auto-generation**: lines 410-453. `cfg = load_config(workspace)`
  (418); `if cfg.scip:` (419); per-language existence check with bounded auto-generation
  `from ..parsers.scip_indexers import try_generate_index` (430-431); `scip_languages[lang] = str(idx_path)`
  (435); unknown-language warning at 440-453. Comment at 410-414 verbatim:
  "SCIP coexistence: if cairn.json declares a pre-built SCIP index for a
  language and that index file exists, tree-sitter STILL parses those files
  (providing modifiers, body, inheritance edges, parent_scope that SCIP
  can't emit). The importer then merges SCIP's exact-resolution edges onto
  the tree-sitter rows post-resolve (below). One row per symbol after merge."
- **scan-event comment**: lines 406-408 ("Capture the scanner's real yield before any SCIP
  skip so the 'scan' event reflects what was found, not the post-skip tree-sitter subset").
  NOTE: there is no actual skip today — tree-sitter parses everything; only the comment
  survives from the pre-coexistence model.
- **_language_extensions helper**: comment at 135-137 ("File extensions per scanner
  language, used by the per-language SCIP fallback … Inverse of scanner.EXTENSION_MAP")
  — its only consumer is the revert block's `exts = _language_extensions(lang)` (616).
- **import + merge + rollback**: lines 558-599. `from ..parsers.scip_importer import scip_available, import_scip_file`
  (561); `import_scip_file(conn, scip_idx, repo_id=repo_for_scip, fmt="proto", ws_root=ws_root)`
  (574-577); on exception `conn.rollback()` (593) + "Don't fail the build over a bad SCIP
  index" (596); `except ImportError` fallback message "[scip] extra not installed" (597-599).
  Runs AFTER `_resolve_all` and BEFORE `backup_to` (comment 554-557).
- **revert-to-pure-scip**: lines 601-653. Trigger `added > 0 and merged == 0` (612);
  deletes tree-sitter symbols/edges for the language's files (`AND source != 'scip'` at
  636, `hash != 'scip_imported'` at 623); `s["reverted_to_pure_scip"] = True` (653).
  The spec's "opaque Swift USRs merging at ~0%" failure mode is this block
  (comment 601-608 names scip-swift).
- **crash-window marker ordering**: comment 655-662 — `_clear_repo_build_state` runs
  deliberately AFTER the SCIP hook; test-pinned in S-07.
- **summary folding**: lines 673-701 — `scip_repo_count` (676), counts folded into
  top-level (679-681), `summary["scip"] = scip_import_stats` (701).
- Resolver call site (context): `repo_stats = resolver_mod.resolve_repo_edges(` at
  `src/cairn/graph/builder.py:370` inside `_resolve_all`.

**Status**: DONE (every path exists, located, and is excisable; blocks are contiguous
and interleaved with the crash-window-marker logic at 655-662 which must be re-anchored
to the new last-write point when the SCIP hook disappears).

**Verify**:
```
rg -n 'scip' src/cairn/graph/builder.py
```
Ran: 34 lines listed above (415-701 region plus 135-137).

### S-03 — graph/config.py `scip` key (FR-002: unknown-key behavior after removal)

**Evidence**:
- `src/cairn/graph/config.py:CairnConfig:32` — field `scip: Dict[str, str] = field(default_factory=dict)`
  at line 54; docstring paragraph at 43-45 ("``scip`` maps language -> index file path …");
  `is_default` property includes `and not self.scip` at line 62.
- `_SCIP_KEY = "scip"` at line 71 under the comment at line 67 verbatim:
  "# Config keys we recognize. Unknown keys are ignored (forward-compatible)."
- `src/cairn/graph/config.py:load_config:75` — `scip = _as_string_dict(raw.get(_SCIP_KEY), path, _SCIP_KEY)`
  at line 107, passed into the constructor at 113.
- FR-002 mechanics already exist: `load_config` reads ONLY the five named keys
  (`exclude`, `include`, `repo_namespaces`, `scip`, `ingest` — lines 104-108); any other
  key in `cairn.json` is silently ignored today. Removing `_SCIP_KEY`/field/parse-line
  makes `scip` an unknown key with zero dedicated handling — no shim needed, matching
  the clarify-round ruling (spec.md:65).

**Status**: DONE (mechanism exists and removal is a 4-line delta in one file).

**Verify**:
```
rg -n 'scip|_SCIP_KEY' src/cairn/graph/config.py
```
Ran: 7 hits (43, 44, 54, 62, 71, 107, 113).

### S-04 — CLI surface: `import-scip` command, `cairn config` scip echo, system docstring

**Evidence**:
- The `import-scip` command lives in `src/cairn/cli/hooks_viz.py:import_scip:93`
  (`@main.command(name="import-scip")` at 86; options `--db/--repo/--format` at 88-92;
  body calls `from ..parsers.scip_importer import import_scip_file` at 95 and
  `import_scip_file(conn, scip_file, repo_id=repo, fmt=fmt)` at 99). It is NOT in
  system.py — the brief's "cli/system.py import-scip" is a misattribution; system.py's
  only scip reference is its module docstring, `src/cairn/cli/system.py:1` verbatim:
  `"""System CLI: import-scip, metrics, status, eval, sync, doctor."""`
- `cairn config` scip echo block: `src/cairn/cli/core.py:241-251` — `if cfg.scip:` (243)
  prints `scip (…):` per-language with exists/MISSING marks (244-249); else-branch
  prints `scip: (none — tree-sitter for all languages)` (251). (The brief called this
  "status"; it is the `config` command — `cairn status` in system.py prints no scip lines.)

**Status**: DONE (command + echo block + one docstring line; all located and removable).

**Verify**:
```
rg -n -i 'scip' src/cairn/cli/
```
Ran: hooks_viz.py 86-99 (7 hits), core.py 241-251 (12 hits), system.py:1 (1 hit); all other cli/ files 0.

### S-05 — Peripheral code touchpoints the brief's list missed

**Evidence** (each verified by grep this session; `path:line` + verbatim):
- `src/cairn/graph/schema.py:449-450` — provenance comment verbatim:
  "# Provenance column on symbols: 'tree_sitter' or 'scip'. NULL on legacy rows /
  # (pre-SCIP builds) is treated as 'tree_sitter'. Additive ALTER is invisible to".
  The `symbols.source` COLUMN itself stays (values become `'tree_sitter'`/'merged'-free;
  spec.md:63 assumes no DB migration). Also schema.py:420 comment introducing
  `edges.resolution` values is scip-free.
- `src/cairn/graph/traversal.py:15` — verbatim:
  "# parsers) and ``\"call\"`` (the SCIP importer) are included." (comment on which
  edge kinds traversal walks; the importer's 'call' kind is why it mentions SCIP).
- `src/cairn/parsers/base.py:45` — verbatim:
  "# references | decorates | imports | contains (tree-sitter SCIP edges" (comment in
  the `Edge` dataclass doc, lines 41-57).
- `src/cairn/knowledge/ingest/identity.py:33` — verbatim:
  "# symbol-disambiguation fragment in parsers/scip_importer.py." (comment justifying
  `_HASH_LEN = 8`; the referenced mechanism dies with the importer — comment needs
  rewording, the constant stays).
- `src/cairn/agent_integration/skill/references/tools.md:9` — verbatim:
  "- `find_definition(name)` -- Where a symbol is defined (supports Tree-sitter AST & SCIP compiler-grade exact bindings)"
  — this ships to users via install-agents (package data, structure.md:11-13). The brief
  missed the entire `agent_integration/` surface.
- `src/cairn/graph/incremental.py` — **ZERO scip references** (rg -ci scip → 0). The
  brief's "incremental.py scip handling" does not exist as code; the incremental-SCIP
  behavior ("edited merged file falls back to tree-sitter") lives in
  `src/cairn/graph/incremental.py:reindex_paths:24` only insofar as it re-parses via the
  normal tree-sitter path and the *tests* (S-06) assert the resulting source flips —
  no scip branch to remove. `insert_parsed_file` writes `source='tree_sitter'` rows.

**Status**: DONE (all located; comment-level refreshes + one shipped-skill doc edit).

**Verify**:
```
rg -n -i 'scip' src/cairn/graph/schema.py src/cairn/graph/traversal.py src/cairn/parsers/base.py src/cairn/knowledge/ingest/identity.py src/cairn/agent_integration/ src/cairn/graph/incremental.py
```
Ran: hits as quoted above; incremental.py → 0 matches.

### S-06 — Tests exercising SCIP (delete wholesale)

**Evidence**:
- `tests/test_scip_importer.py` — 23 tests (inventory this session: `def test_` at lines
  54, 87, 109, 165, 177, 199, 218, 243, 291, 360, 386, 405, 439, 464, 511, 537, 554,
  571, 597, 629, 660, 682, 695).
- `tests/test_scip_indexers.py` — 16 tests (`test_registry_covers_supported_languages:28`
  … `test_generate_is_idempotent_when_index_exists:214`).
- `tests/test_build_scip_hybrid.py` — 6 tests (`test_scip_coexists_with_tree_sitter:59` …
  `test_unmatched_indexer_names_revert_to_pure_scip:195`).
- `tests/test_scip_incremental.py` — 2 tests (`test_reindex_of_merged_file_falls_back_to_tree_sitter:47`,
  `test_full_build_restores_merged_after_incremental:92`). 23+16+6+2 = 47 — matches the
  run below.
- `tests/test_parser_audit_fixes.py` — module docstring F4/F5 items (lines 22, 25);
  `from cairn.parsers.scip_importer import scip_available` (40);
  `pytestmark_scip = pytest.mark.skipif(not scip_available(), …)` (275-277);
  helpers `_scip_conn:280`, `_insert_ts_symbol:289`;
  `tests/test_parser_audit_fixes.py:TestScipMergeOverloads:299` (3 tests) and
  `tests/test_parser_audit_fixes.py:TestScipHeaderLanguage:402` (2 tests) — both classes
  carry `@pytestmark_scip`; deletion must also strip the now-orphaned scaffolding
  (275-296) and the import at 40.
- `tests/test_audit_remediation.py:test_p5_failed_scip_import_rolls_back_pending_writes:402`
  (single test; imports `import_scip_bytes, scip_available` at 405, monkeypatches the
  importer module 441-466).
- `tests/test_big_tech_improvements.py:test_scip_importer:11` (single test; module-level
  `from cairn.parsers.scip_importer import import_scip_data` at 5 — removing the test
  must remove the import or pyflakes/ruff F401 fails the gate).

**Status**: DONE (all exist; blast radius exactly enumerated).

**Verify**:
```
CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_scip_importer.py tests/test_scip_indexers.py tests/test_build_scip_hybrid.py tests/test_scip_incremental.py -q
```
Ran (this session): `47 passed in 3.66s` — zero skips, i.e. protobuf present. Second run
(`TestScipMergeOverloads` + `TestScipHeaderLanguage` + `test_p5…` + `test_scip_importer` +
`tests/test_workflow_audit_fixes.py`): `16 passed in 2.34s`. Post-removal, AC3 (spec.md:30)
means these files/classes vanish rather than skip.

### S-07 — Tests that SURVIVE removal but reference SCIP in comments/docstrings

**Evidence**:
- `tests/test_workflow_audit_fixes.py:test_crashed_repo_build_leaves_marker_detectable:313`
  — patches `builder_mod._resolve_all` (line 329, NOT scip code); docstring/comment name
  the "SCIP post-resolve hook (the clear runs AFTER that hook)" (317, 327). Section
  header at 295: "crash-window marker: set before clear, cleared after SCIP, detectable".
  Logic stays valid post-removal (crash before marker-clear still leaves the marker);
  wording needs refresh because the clear's anchor moves.
- `tests/test_doctor.py:411` — comment verbatim: "for an on-disk rebuild and removes it
  only after the SCIP hook; a crash" (doctor's partial-repo check test).
- `tests/test_invariants.py:18-19` and `:217` — docstrings cite
  `BUGS.md#scip-importer-fake-resolution`; see S-09: BUGS.md does not exist at baseline.
  The invariant test itself (`test_invariant_exact_resolution_has_target_id:213`) is a
  PROTECT item (S-13), not a delete.

**Status**: DONE (three files identified; keep-with-reword, zero logic change expected
except the marker-ordering comment's anchor).

**Verify**:
```
CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_workflow_audit_fixes.py -q
```
Ran: passed as part of the 16-passed run in S-06.

### S-08 — Docs, diagrams, README, CHANGELOG

**Evidence**:
- `docs/indexing.md:66-72` — §8 "SCIP import (optional)" (whole numbered section;
  renumbering of §9/§10 follows). The tier table above it (lines ~50-65) is scip-free.
- `docs/configuration.md:16` — config-table row "| `scip` | map | language → SCIP index
  path (relative); auto-generates when possible |"; `docs/configuration.md:211` —
  extras-table row "| `scip` | protobuf | consuming pre-built SCIP indexes |".
- `docs/cli-reference.md:103` — "| `cairn import-scip` | import a SCIP index |".
- `docs/architecture.md:84` — "| `parsers/` | tree-sitter parsers (14 languages) + SCIP
  importer |".
- `README.md:103-109` — language-table SCIP-merge column entries (Kotlin 103, Java 104,
  TypeScript 106, Python 108, Go 109) and `README.md:318` — "[scip] extra" row.
- Diagrams: `docs/diagrams/indexing-pipeline.html` + `.svg` + `-dark.html` — 4 hits each:
  `<desc>` "with an optional SCIP merge branch" (html:58 / svg:4), "YES · SCIP KEY"
  label (html:79 / svg:26), "scip key?" decision node (html:103 / svg:50), "Merge SCIP
  defs" node (html:115 / svg:62). The rendered PNG twins (`indexing-pipeline.png`,
  `indexing-pipeline-dark.png`) must be regenerated from the edited sources — grep can't
  see into them. `docs/diagrams/c4.html:146`, `c4-dark.html:147`, `c4-context.html:68`,
  `c4-context-dark.html:68` — label "tree-sitter · 14 languages · SCIP" (+ their PNG twins).
- `CHANGELOG.md` — 55 case-insensitive hits, all inside historical version entries
  (e.g. 1989-2023: hybrid indexing intro, autogen, P5 rollback fix, `--format` flag).
  History, not living docs; rewriting past entries is a tech-spec decision (FR-004 scope
  question), flagged as a gap below.

**Status**: PARTIAL (all living-doc touchpoints enumerated and DONE-able; CHANGELOG
disposition + PNG regeneration workflow are unpinned decisions).

**Verify**:
```
rg -n -i 'scip' docs/ README.md
```
Ran: hits as listed (indexing.md 6, configuration.md 2, cli-reference.md 1, architecture.md 1,
README.md 9, indexing-pipeline.{html,svg,-dark.html} 4 each, c4*.html 1 each).

### S-09 — Stale references to already-deleted docs (found by this survey)

**Evidence**: `BUGS.md`, `docs/BUGS.md`, `docs/scip.md`, `docs/scip-hybrid-plan.md` —
none exist (`ls` this session: "No such file or directory" for all four). Yet:
- `src/cairn/parsers/scip_indexers.py:103` — "(see docs/scip.md / "Automatic generation"
  for the full table)".
- `tests/test_invariants.py:19` — "BUGS.md#scip-importer-fake-resolution";
  `tests/test_invariants.py:217` — "See BUGS.md#scip-importer-fake-resolution."
- `tests/test_big_tech_improvements.py:18-19` — "see docs/scip-hybrid-plan.md §Bugs and
  BUGS.md#2026-08-06/scip-importer-fake-resolution".

**Status**: DONE (already stale at baseline; removal deletes the referencing code/tests
anyway — no extra work unless test_invariants.py:217 docstring is kept, which then needs
its citation dropped).

**Verify**:
```
ls BUGS.md docs/BUGS.md docs/scip.md docs/scip-hybrid-plan.md
```
Ran: all four "No such file or directory".

---

## Change 2 — Resolver current state

### S-10 — graph/resolver.py tier machinery, inputs, and parser-side signal coverage

**Evidence**:
- **Tier contract** (FR-008's exact ordering, `src/cairn/graph/resolver.py:resolve_edge:177`):
  Tier 0 type-aware receiver dispatch at 200-214 (`if receiver_type:` 205, `typed_ids =
  {c[0] for c in cands} & set(typed)` 209 — consistency guard, abstains to `ambiguous`
  on >1 at 212-213, falls through on 0); Tier 1 same-file 216-221 (`c[2] == source_file_id`);
  Tier 2 import-aware 223-233 (`_import_aware_candidates` narrowing, exact only at
  229-230, ambiguous at 231-232, fall-through when imports don't mention the name);
  Tier 3 same-repo 235-240 (`c[1] == source_repo`); Tier 4 global 242-244 (single
  candidate); final `return None, "ambiguous"` 245. Unresolved early-return at 196-198
  ("external/stdlib call").
- **Import-aware scoring**: `src/cairn/graph/resolver.py:_import_aware_candidates:248`
  — DIRECT (import tail is suffix of candidate qname, `_common_suffix_len:312`) and
  CONTAINING (import tail is contiguous subsequence of qname, scoring `len(tail) + i`
  at 294; last-segment fallback `tail[-1] == qsegs[0]` → prefix_len 1 at 299-300);
  max-score survivors kept, ties → ambiguous (306-309).
- **Type-aware support**: `_members_of:152` (BFS over ancestors, cycle-safe),
  `build_members_index:68` (keys `(enclosing_type_simple_name, member_name)` from
  `qualified_name` second-to-last segment, only when that segment is a real non-member
  non-module symbol name), `build_ancestor_index:119` (reads inheritance edges
  `kind IN ('extends','implements','embeds')` with `target_name IS NOT NULL` — does
  NOT depend on those edges being resolved).
- **Exact DB columns read** (verbatim SQL, resolver.py):
  - `build_symbol_index:17` → `symbols s` (`s.id`, `s.name`, `s.qualified_name`,
    `s.kind != 'module'`) JOIN `files f ON s.file_id = f.id` (`f.repo_id`, `f.id`).
  - `build_import_index:39` → `imports i` (`i.file_id`, `i.imported_path`) JOIN `files`
    scoped `f.repo_id = ?`.
  - `build_members_index:68` → `symbols` (`id`, `name`, `qualified_name`, `kind IN
    _MEMBER_KINDS` at line 65: `("method", "function", "property", "variable",
    "field", "constant")`).
  - `build_ancestor_index:119` → `edges e JOIN symbols src ON e.source_id = src.id`
    (`e.kind`, `e.target_name`).
  - Writes back: `UPDATE edges SET target_id = ?, target_name = ?, resolution = ? WHERE id = ?`
    (resolve_repo_edges, line 366).
- **Callers**: `src/cairn/graph/builder.py:370` (`resolve_repo_edges` inside
  `_resolve_all`); `src/cairn/graph/incremental.py:281` (reindex) and
  `src/cairn/graph/incremental.py:294` (`repair_incoming_edges:372` for deleted+recreated
  targets).
- **Parser-side signal coverage** (what `Edge.receiver_type` etc. is emitted today):
  - `src/cairn/parsers/base.py` `Edge` dataclass (41-57): `receiver_type` field at 57
    with comment "Bare type name of the call receiver, if the parser could infer one";
    `src/cairn/parsers/base.py:_infer_receiver_type:152` — the shared heuristic is
    exactly "receiver_text[0].isupper() → return receiver_text" (163-164), else None.
  - EMIT receiver_type: **5 of 14** — `src/cairn/parsers/go.py:349`
    (`receiver_type=self._infer_receiver_type(receiver_text)`, fed by
    `_split_selector` 353; method symbols get `parent_scope=receiver_type` 209/245),
    `src/cairn/parsers/java.py:290` (new/class chains 271-290),
    `src/cairn/parsers/kotlin.py:498` (`_infer_call_receiver_type:643` + in-file
    var→type tracker stacks at 45/72; operator-invoke rewrite 460-491),
    `src/cairn/parsers/php.py:364`, `src/cairn/parsers/ruby.py:242`.
  - DO NOT emit receiver_type (drop the signal): python (`src/cairn/parsers/python_parser.py:_parse_call:304`
    emits no receiver), swift, typescript+javascript (`src/cairn/parsers/typescript.py:_parse_call:516`),
    csharp, dart, objc (objc.py:336 mentions "receiver" only in an array-subscript
    comment), c, cpp (`c_family.py`) — **9 of 14**.
  - Imports: every parser emits `Import(imported_path=…)` — python stores the raw
    statement text (`src/cairn/parsers/python_parser.py:_parse_import:300`, line 302),
    typescript resolves relative specs (`src/cairn/parsers/typescript.py:_parse_import:454`,
    lines 465-466 `resolved if resolved else spec`), dart 375-376 and objc 321-322 same
    pattern. **No parser records the local alias** of `import X as Y` / `from m import n as k`
    — the resolver's import tier matches path tails only (`_import_aware_candidates`
    works on `imported_path` strings; alias bindings are invisible → such call edges
    fall through to same-repo/global).
  - Qualified names: `Symbol.qualified_name` (base.py:23) is built per-parser via
    `src/cairn/parsers/base.py:_qualified_name:112` (ts override at
    `src/cairn/parsers/typescript.py:_qualified_name:288`).
- **Where the resolver's edge tuples come from**: `resolve_repo_edges` docstring
  (331-338): 5-tuples `(edge_id, source_symbol_id, target_name, line, column)` or
  6-tuples `+ receiver_type` (read at 354) — builder/incremental collect them from
  `ParsedFile.edges`.

**Status**: DONE (machinery, inputs, columns, and the exact emit/drop split across the
fourteen languages mapped; the 9-language receiver gap + the universal alias gap are the
concrete FR-006/FR-007 work surfaces).

**Verify**:
```
CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_type_tier.py tests/test_resolver_type_scoped_fallback.py -q
rg -n 'receiver_type=' src/cairn/parsers/
```
Ran: `39 passed in 4.64s` (combined protect-test run, S-13); rg → exactly the 5 emitter
files listed above (go 1, java 2, kotlin 1, php 1, ruby 1 assignment sites).

---

## Change 3 — Baseline numbers (FR-005 / FR-010 ground truth)

### S-11 — Fixed-corpus resolution mix at dc9882b, and the instrument gap

**Evidence** (all numbers produced this session by the commands below):
- **cairn's own repo** (workspace `.`; scanner yields 13,488 files — includes the
  vendored `benchmarks/datasource` corpora since `benchmarks` is NOT in
  `DEFAULT_SKIP_DIRS` (scanner.py:102-119; `vendor` IS skipped); MAX_FILE_SIZE 1 MB
  prunes the minified dashboard assets):
  - build 198.7 s, `summary['resolution'] = {'exact': 223901, 'ambiguous': 777716, 'unresolved': 221947}`,
    `summary['scip'] = None` (no scip config — pure tree-sitter today, i.e. **this IS
    the pre-change baseline for FR-005**).
  - Final DB edges: `{'exact': 464048, 'ambiguous': 777716, 'unresolved': 221947}` —
    SQL exact > summary exact because `materialize_import_edges`
    (`src/cairn/graph/builder.py:materialize_import_edges:1262`) inserts `kind='imports'`
    edges with literal `resolution="exact"` post-resolve (insert at builder.py:1361).
    **The FR-005 instrument must pin which counter it uses** (see Gap).
  - exact share `exact/(exact+ambiguous)`: 0.3737 (SQL, all kinds) / 0.2237 (summary,
    resolve-phase only) — same DB, two definitions.
  - call-kind edges only: `{'exact': 192488, 'ambiguous': 668424, 'unresolved': 157928}`
    → call exact share 0.2236.
  - per-language (SQL; share = exact/(exact+ambiguous)): c 0.8432, cpp 0.8396, csharp
    0.8333 (2 amb/10 ex), dart 0.9286 (1/13), go 1.0000 (13 ex/1 unres), java 1.0000
    (14 ex), javascript 0.4573 (33198 amb/27976 ex), kotlin 0.6609 (39/76), objc 0.7826
    (5/18), php 0.8276 (5/24), python 0.3664 (743312 amb/429754 ex — dominates), ruby
    0.4762, swift 0.9000 (1/9), typescript 1.0000 (17 ex). (Spec.md:66's risk-mitigation
    ask — pre-change per-language mix — satisfied by this table; note the corpus's
    SCIP-eligible languages were NEVER SCIP-indexed here, so removal itself cannot
    regress this baseline.)
- **ds2 corpus** (`benchmarks/datasource/ds2/second-corpus/attrs-26.1.0`): the committed
  tree has NO `.git` marker (provenance.json:16), so a direct build yields
  `files=0`. Correct idiom (same as `verify_dataset.py` / `scripts/verify_ground_truth.py:build_fresh_graph:188`):
  copy the tree to a tmp workspace, `mkdir .git`, build. Ran: 50 files / 1722 symbols /
  6239 edges, 0.2 s; summary `{'exact': 1271, 'ambiguous': 679, 'unresolved': 2617}`
  (resolve-phase share 0.6517); SQL `{'exact': 3055, 'ambiguous': 679, 'unresolved':
  2617}` (share 0.8182, includes 112 imports-edges + contains/decorates). 50 files
  matches the sealed authoring facts (S-12).
- **Instrument gap**: `src/cairn/bench/scaling_suite.py:_resolve_rate:23` computes
  `(exact + ambiguous) / total` — the share of edges that are *not unresolved*, NOT
  FR-005's exact share `exact/(exact+ambiguous)` (spec.md:49). The scaling suite also
  runs on a synthetic corpus (`generate_corpus`), not the fixed one. FR-005's measurement
  needs either a small extension of `_resolve_rate` or a direct SQL/summary read as used
  above. `build_runs` rows persist `resolution_exact/ambiguous/unresolved` columns
  (schema.py:362), so historical baselines are queryable per build.

**Status**: DONE (both corpora measured at baseline with runnable commands; counter-
definition discrepancy pinned).

**Verify** (re-runnable recipe; scripts kept out of the repo — this session used
/tmp throwaways with a `__main__` guard, required because build workers spawn-reimport):
```
# self repo
CAIRN_HOME=<tmp> CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test python <script>  # build_graph(workspace=".", db_path=<tmp>) then:
#   SELECT resolution, COUNT(*) FROM edges GROUP BY resolution;          (+ per-language via JOIN files)
# ds2 corpus
cp -R benchmarks/datasource/ds2/second-corpus/attrs-26.1.0 <tmp>/attrs && mkdir <tmp>/attrs/.git
#   build_graph(workspace=<tmp>, ...) then same SQL
```
Ran: outputs quoted verbatim above (self: 198.7 s; ds2: 0.2 s).

---

## Change 4 — Measurement / eval infrastructure

### S-12 — Ground truth, seals, and wall-time gates

**Evidence**:
- **Retrieval ground truth (the AC5 gate)**: `scripts/verify_ground_truth.py:main:441`
  drives `verify_ground_truth:251` over `benchmarks/datasource/t2` via
  `scripts/verify_ground_truth.py:build_fresh_graph:188` (copy + `.git` marker + fresh
  build); matching lives in `cairn.eval` (`load_ground_truth`, `match_rank`,
  `parse_symbol_id`; eval.py:392 "Tier 1: strict identity (file suffix + exact symbol
  name)"). Exit contract 0/1/2 (lines 101-103).
- **ds2 seal (CI `ds2-seal` job, ci.yml:249-269)**: `python benchmarks/datasource/ds2/verify_dataset.py`
  — verifies 558 expectations over BOTH corpora (attrs + yarl) against fresh builds,
  cross-checks authoring facts + tree hashes. Ran this session:
  `OK : 558/558 expectations tier-1-exact (pass rate 1.0000, unresolved 0, aspirational 0)`,
  attrs build facts `{'repos': 1, 'files': 50, 'symbols': 1722, 'edges': 6239,
  'parse_errors': 0}`, exit 0.
- **Resolution-specific ground truth**: NONE exists. No dataset anywhere keys on
  `edges.resolution` (rg over `src/cairn/eval.py`, `benchmarks/**/ground_truth/**`,
  `scripts/verify_*.py` — the only resolution-aware surfaces are the resolver itself,
  explore's ambiguous-dispatch surfacing, and the summary counters). Spec.md:64's
  assumption holds: a resolution ground truth must be authored as part of the work
  (AC6's per-shape regression tests are the vehicle).
- **Wall-time gates (FR-010)**: CI `bench` job (ci.yml:324+) is advisory-only
  (`continue-on-error: true`): rolling-baseline comparison via `cairn bench --compare`,
  exit 2 on regressions past 15%, absorbed. Committed fallback baseline:
  `benchmarks/baselines/DS-v1/scaling.json`. The `build_runs` table (schema.py:350-362)
  records phase timings + resolution counts per build. "Materiality" for FR-010 is
  therefore whatever the 15% advisory threshold + the S-11 wall-time numbers define.

**Status**: DONE (all gates located and the missing-resolution-GT fact established by
exhaustive grep, not assumption).

**Verify**:
```
uv run python benchmarks/datasource/ds2/verify_dataset.py
rg -rn 'resolution' src/cairn/eval.py benchmarks/datasource/ds2/ground_truth/ scripts/verify_ground_truth.py
```
Ran: seal exit 0 (558/558); rg → no resolution-keyed expectations (eval.py hits are
retrieval-tier wording only).

---

## Change 5 — Regressions to protect

### S-13 — What pins resolution behavior today

**Evidence**:
- `tests/test_type_tier.py` — 5 tests: `test_receiver_type_disambiguates_same_named_method:54`,
  `test_same_file_collision_resolved_by_receiver_type:89`,
  `test_inheritance_resolves_to_base_class_method:120`,
  `test_receiver_type_none_is_abstain_safe:145` (the FR-009 abstain pin),
  `test_build_members_index_and_ancestor_index_shapes:178`.
- `tests/test_resolver_type_scoped_fallback.py` — 9 tests pinning
  `_import_aware_candidates` scoring (`test_package_qualified_resolves_via_contiguous_subsequence:21`
  … `test_direct_import_pattern_unaffected:162`).
- `tests/test_reindex_resolution_invariant.py` — 3 tests
  (`test_reindex_nulls_resolution_to_unresolved:51`,
  `test_no_dangling_exact_edges_after_reindex:69`,
  `test_unrelated_exact_edges_preserved:88`) — incremental-path resolution invariants.
- `tests/test_search_edge_expansion.py` — 12 tests: retrieval expansion reads stored
  (resolved) edges, not query-time overlap.
- `tests/test_invariants.py:test_invariant_exact_resolution_has_target_id:213` — the
  zero-false-exact invariant (exact ⇒ target_id NOT NULL), motivated by the SCIP
  importer bug (docstring 214-222); survives removal as the FR-009 backstop.
- `tests/test_graph_relationship_kinds.py:346` uses `build_ancestor_index:119` directly.
- Golden fixtures: `tests/fixtures/golden/regenerate.py` — `LANG_CONFIG` (lines 23-38)
  covers exactly the fourteen FR-007 languages (c, cpp, csharp, dart, go, java,
  javascript, kotlin, objc, php, python, ruby, swift, typescript);
  `regenerate_lang:91` / `normalise:43`; consumers `tests/test_golden_parsers.py`.
  Parser-signal enrichment (FR-007) changes parser OUTPUT → goldens + regenerate are
  the update vehicle.
- Correction to the brief's list: `tests/test_pointer_ambiguity.py` pins KNOWLEDGE-
  INGEST document-pointer resolution (classes `TestUniqueSuffixStillResolves:145`,
  `TestAmbiguousSuffixIndexesNothing:180`, … — workspace fixture is an OKF bundle, not
  a graph); it does NOT pin graph-edge resolution and is unaffected by resolver tiers.
- `explore`'s ambiguous-dispatch + exact-only call paths
  (`src/cairn/graph/explore.py:_ambiguous_dispatch:95`, `explore:133` at 147/152) and
  MCP `get_callers`/`impact` rendering (`src/cairn/mcp_server/tools_graph.py:515,522`)
  are the downstream consumers whose behavior the mix shift must not corrupt.

**Status**: DONE (protect-set enumerated, run green at baseline, and one brief
misattribution corrected).

**Verify**:
```
CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_type_tier.py tests/test_resolver_type_scoped_fallback.py tests/test_reindex_resolution_invariant.py tests/test_pointer_ambiguity.py tests/test_search_edge_expansion.py tests/test_invariants.py -q
```
Ran (this session): `39 passed in 4.64s`.

---

## Summary table

| id | item | status |
|---|---|---|
| S-01 | parser scip modules + stub + regen + extras | DONE |
| S-02 | builder hybrid/merge/revert paths | DONE |
| S-03 | config scip key → unknown-key semantics | DONE |
| S-04 | CLI import-scip + config echo + docstring | DONE |
| S-05 | peripheral touchpoints (schema/traversal/base/identity/skill-doc; incremental=none) | DONE |
| S-06 | scip tests to delete | DONE |
| S-07 | surviving tests needing comment refresh | DONE |
| S-08 | docs/diagrams/README/CHANGELOG | PARTIAL (CHANGELOG + PNG regen unpinned) |
| S-09 | stale BUGS.md/docs/scip.md references | DONE |
| S-10 | resolver tiers, inputs, columns, parser signal split | DONE |
| S-11 | baseline numbers both corpora + instrument gap | DONE |
| S-12 | GT/seal/wall-time gates; no resolution GT | DONE |
| S-13 | protect-set + brief correction | DONE |

**Unknowns**: none — every `unknown` candidate was resolved by grep/run this session.

**Biggest gaps found** (survey's main job — what the brief MISSED):
1. `import-scip` lives in `cli/hooks_viz.py:86`, not system.py; `graph/incremental.py`
   has ZERO scip code (brief listed both under wrong files).
2. FR-005's instrument does not exist: `scaling_suite._resolve_rate` measures
   (exact+ambiguous)/total on a synthetic corpus, and the summary counters vs the
   edges-table SQL disagree by construction (imports-edges inserted `'exact'`
   post-resolve, builder.py:1361) — the metric definition must be pinned before any
   before/after claim (self repo: 0.3737 SQL-share vs 0.2237 resolve-phase-share).
3. ds2 cannot be built in place (no `.git` marker → files=0); the copy+marker idiom is
   mandatory and must be part of the recorded recipe.
4. Shipped-user surface: `agent_integration/skill/references/tools.md:9` advertises SCIP
   bindings (installed into agent configs via install-agents) — beyond the brief's
   docs list. Plus README language-table rows, `architecture.md:84`, c4* diagram labels,
   and PNG renders needing regeneration.
5. Stale references to nonexistent `BUGS.md`/`docs/scip.md`/`docs/scip-hybrid-plan.md`
   in scip_indexers.py:103, test_invariants.py:19/217, test_big_tech_improvements.py:18-19.
6. Receiver-type signal is emitted by only 5 of 14 parsers (go/java/kotlin/php/ruby);
   no parser records import aliases — the concrete FR-006/007 enrichment surface.

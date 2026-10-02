# Full-codebase audit — 2026-10-02 (code only) — validated

**Verdict: fix-first.** The codebase is in strong mechanical health (gate fully green) and the security posture is good (localhost dashboard, MCP tool surface, hooks, and parser surfaces all came back clean), but the audit confirmed **141 discrete defects — 2 high (P0), 51 medium (P1), 88 low (P2) — plus 3 systemic clusters (P3)**. Every finding was independently confirmed by a second agent that re-derived it from the code, and the entire report was then independently re-validated against HEAD `98fc2d8`: **136/141 findings confirmed as written, 5 PARTIAL (S1, C14, Q32, Q41, Q51 — core defect real, one sub-claim each wrong; corrected in place in the tables below), 0 refuted**. All 26 systemic-cluster samples, all 9 test-gap claims, and the Stage-1 gate results reproduced. Evidence: "Validation detail" and the per-finding roll in the Appendix.

## Method

- **Stage 1 — mechanical gate** (repo's own checks, project mode):
  - `pytest` (.venv): **3818 passed, 2 skipped** (7m43s)
  - `bash -n` on all 8 tracked shell scripts: pass
  - `ruff check .` (F-only selection, mirrors CI): all checks passed
- **Stage 1.5 — scout map**: module inventory + risk areas from the cairn graph (no compass/wiki coverage exists for any module — see Residual risks).
- **Stage 2 — three lenses** (correctness / security / quality-&-tests) over 16 byte-balanced shards covering **313 tracked source files, ~2.9 MB**: `src/cairn/**` (Python, dashboard JS + templates), `extensions/vscode/**` (TS), `scripts/`, `.github/scripts/`, `benchmarks/datasource/ds2` verification scripts, shell scripts. ~250 raw findings.
- **Triage**: cross-lens dedupe, nits dropped, comment-policy violations consolidated into systemic clusters.
- **Confirmation**: every kept finding re-verified by an independent confirmer that saw only the code. **140/140 batched findings verified; the hooks-fallback high (C77) was independently derived by two reviewer shards and spot-checked against the tree; 0 unconfirmed.**
- **Stage 3 — independent validation** (same day, HEAD `98fc2d8`): 14 module-scoped validation passes re-derived every finding from the code (skeptical re-reads plus targeted read-only repros; no code modified). Result: **136 confirmed / 5 partial / 0 refuted / 0 unverifiable**; gate re-run reproduced Stage 1 exactly (3818 passed, 2 skipped in 7m28s). Corrected sub-claims are tagged *(validated: …)* in the tables; details in Validation detail.

**Scope exclusions (notCovered):** tests/ (509 files — executed by the gate, not line-audited; quality lens checked coverage gaps only), `benchmarks/datasource` corpora (vendored third-party: attrs, yarl), minified bundles (mermaid chunks, vis-network, alpine, htmx), CSS/SVG/images, docs/specs/CI YAML (asked for code only).

---

## Findings index — all 141 by priority

All rows below were re-validated on 2026-10-02; rows whose wording was corrected during validation are tagged *(validated: …)*.

### P0 — High (2)

| ID | Location | Issue |
|----|----------|-------|
| C77 | `hooks/claude_hooks.py:31` · `agent_install/_common.py:83` · `agent_integration/skill/scripts/impact_guard.py:23` | `python -m cairn.cli.main` fallback can never run the CLI (no `__main__` guard; entry point is `cairn.cli:main`) — all hooks silently no-op when `cairn` isn't on PATH; `impact_guard` then crashes on empty stdout |
| C1 | `graph/watcher.py:100` (+ `scanner.py:560` vs `:605-621`) | Query-time repair never converges when a file's parser is unavailable: drift scan lacks the parser gate, reindex can't fix or drop the file, strict repair raises — every graph read fails until the file is removed |

### P1 — Medium (51)

| ID | Location | Issue |
|----|----------|-------|
| C2 | `graph/builder.py:1178` | Scoped import-edge pass (run after every incremental update) deletes imports edges but re-derives only in-scope → cross-repo imports edges dropped until next full build |
| C3 | `graph/lsp.py:345` | Pyright upgrade pass probes column=0 (Python call edges never store column) → silent no-op when indented, wrong-symbol rewrite at module level |
| C4 | `dashboard/workspaces.py:124` | Any `sqlite3.OperationalError` → call_count 0: locked/corrupt store reported as legacy instead of unreadable |
| C5 | `graph/blast.py:183` | Pure-deletion hunks skipped before seeding → delete-only changes contribute zero blast-radius seeds |
| C6 | `graph/blast.py:43` | Git octal path escapes never unescaped (diff lacks `core.quotePath=false`) → non-ASCII paths never match `files.path` (live-reproduced); quoted rename branch fails earlier |
| C8 | `graph/traversal.py:475` | `trace_flow` re-walks visited nodes (guard only on dict insert) → duplicate branches, exponential re-exploration on diamonds; contradicts "each id visited once" |
| C9 | `graph/taint.py:224` | Symbol that both calls a source and calls a sink reports no path — entry symbol never tested against terminators |
| C11 | `graph/tests.py:46` | `'test_'` matched as unanchored substring (comment says "file starts with") → production paths classified as tests |
| C12 | `graph/search_pipeline.py:311` | PRF second-pass failure replaces good first-pass candidates with `[]` → zero results instead of degrading to fused/BM25 |
| C13 | `graph/watcher.py:53` | Banner says "N file(s) not refreshed" even when repair succeeded; MCP tools prepend it unconditionally |
| C14 | `graph/lexical.py:138` | Underscore-wrap guard runs after escaping → underscore patterns degrade to exact-match in the LIKE path; bites when LIKE runs alone (FTS unavailable) or for camelCase names — end-to-end `search_symbols("repo_map")` still returns `build_repo_map` via FTS *(validated: example corrected)* |
| C15 | `retrieval/vector_scan.py:123` | Pure-Python cosine fallback raises `struct.error` on a malformed blob (numpy path skips it; docstring promises skipping) |
| C17 | `viz/query.py:129` | `_parent` returns the entry's own symbol → every depth>0 impact edge renders as a self-loop |
| C24 | `memory/store.py:545` + `cli/memory.py:590` + `scripts/measure_memory_health.py:254` | Bare `fromisoformat` on the Z-suffixed timestamps cairn writes → ValueError swallowed to age=0 on Python 3.10: **`memory purge` silently never purges** |
| C25 | `memory/promotion.py:812` + `memory/scoring.py:164` | Aware-minus-naive `TypeError` uncaught (naive hand-authored timestamps preserved by design) → one such file crashes `cairn update` decay / `memory batch-critic` |
| C28 | `cli/uninstall.py:161` | Store targeting uses un-resolved path (registration resolves); miss falls back to whole `~/.cairn` and `-y` skips the prompt → `uninstall --graph-only -y` can delete **all** workspaces' stores |
| C29 | `llm/tasks.py:36` | Status vocabulary advertises `failed`, no code writes it (critic-exhausted = `done`) → `task list --status failed` never matches |
| C30 | `cli/task.py:107` | `claimer` never passed from the only shipped caller → task-ownership guard unreachable; any agent can complete others' claimed tasks |
| C36 | `wiki/pipeline.py:79` | `save_manifest`'s False return discarded → failed manifest write silently loses queued tasks; CLI then reports "Up to date, skipped" |
| C37 | `wiki/refine.py:14` | Refined-outline validation not repo-scoped (`_module_in_graph`/`file_exists` query all repos) → cross-repo/hallucinated modules planned for the target repo |
| C40 | `compass/generator.py:180` | `_rank_key_files` counts incoming edges to every same-named symbol store-wide, not to that file's symbols (contradicts its docstring) |
| C44 | `parsers/swift.py:304` | `_parse_property` expects direct `identifier`; grammar wraps in `pattern`→`simple_identifier` → **no Swift property is ever a symbol** (golden pins zero) |
| C45 | `parsers/swift.py:151` | `class` keyword in `SWIFT_MODIFIERS` recorded as a modifier on every Swift class (golden pins it) |
| C46 | `parsers/typescript.py:130` | Decorator on a class field stashed during the field's post-parse walk, consumed by the NEXT declaration (reproduced) |
| C47 | `parsers/kotlin.py:171` | `class_parameter` early-returns on a false "leaf" comment → call edges in primary-constructor defaults dropped (siblings all descend) |
| C48 | `parsers/scip_indexers.py:224` | External SCIP indexers launched without `cwd=repo_path` → scip-java/ts/go index the invoker's cwd |
| C53 | `dashboard/templates/chains_region.html:31` | Chain span computes `None - None` when all calls have NULL `invoked_at` (guard checks only call_count) → 500s /chains and its poll |
| C54 | `dashboard/templates/database.html:11` | Renders only `schema.skipped[-1].reason` after listing all names → wrong diagnostic for the others |
| C55 | `dashboard/templates/chains_region.html:48` | Expand anchor preserves only expand/window → drops an active `session` filter the poll URL keeps |
| C65 | `dashboard/static/knowledge-graph.js:438` | ~100 lines (htmx race machinery, cssVar, spiralPosition, physics, zoom/fit) duplicated from app.js/db-graph.js, already drifted |
| C68 | `knowledge/store.py:334` | Unguarded `md_file.stat()` aborts a whole directory import on one vanished file (the read below IS guarded) |
| C69 | `telemetry/cli_metrics.py:190` | Flush cycle has no cycle lock (sink.py added `_FLUSH_LOCK` for this exact failure) → daemon tick ∥ atexit drain double-inserts and drops never-written rows |
| C70 | `cli/wiki.py:49` | `--dry-run` ignored in the `--llm` branch: help promises "write nothing" but real tasks queue and the manifest persists |
| C73 | `mcp_server/tools_knowledge.py:106,152` | knowledge/-namespace guard on destructive tools duplicated verbatim across delete/status (comment concedes it) — drift weakens a destructive-tool guard |
| C74 | `memory/promotion.py:153` | `record_reference` production-dead (only a test calls it) and drifted from the batch form: batch retries lock contention, single-shot doesn't |
| C75 | `viz/query.py:274` | `get_symbol_overview` duplicates ~70 lines of `get_module_graph` (both carry a dead `kept` counter) |
| S1 | `agent_install/merge.py:444` | Uninstall strip helpers crash on non-dict values under mcpServers/mcp/hooks — install path guards exactly this class; `_already_installed` guards the top levels, its only hole is a non-dict `mcp.servers` *(validated: scope corrected)* |
| S3 | `agent_install/detect.py:193` | agy probe hardcodes `~/.gemini/...` while installer honors XDG/APPDATA → non-macOS installs report "not installed" |
| S4 | `agent_install/clients/droid.py:51` | Dry-run with droid on PATH takes neither branch → report omits the registration a real run performs |
| S7 | `telemetry/events.py:110` + `graph/embed_ladder.py:242` | `_coerce_attrs` never redacts plain-string attrs (docstring claims it does); `EMBED_SERVER_DEGRADED` netloc can carry `user:password@` → events table + OTLP |
| S8 | `scripts/regen_scip_pb2.sh:42` | `cp $WORK/scip.proto $WORK/` copies a file onto itself → non-zero under `set -e`; SCIP protobuf can never be regenerated |
| S9 | `scripts/install-hooks.sh:8` | Parallel reimplementation of `cairn hooks install` with hardcoded nonexistent repos (aborts under `set -e`); clobbers foreign post-commit hooks |
| S10 | `scripts/run_skill_evals.py:219` | `resolve_tool` accepts any `mcp__*__*` tail without registry check → typo'd expected tools pass the CI validator |
| S17 | `scripts/verify_ground_truth.py:342` | Loader admits L4 rows but summary has only L1/L5 keys → KeyError crash past the documented exit-code contract |
| S18 | `scripts/verify_no_code_change.py:113,123` | Deleted .py files: commit mode silently reports "comment/docstring changes only" (false clean); tree mode crashes with FileNotFoundError |
| Q1 | `bench/scaling_suite.py:115` | Env (CAIRN_DB/CAIRN_EMBED_BACKEND) never restored; leaked CAIRN_DB points at DBs the suite deletes (siblings all snapshot+restore) |
| Q2 | `bench/perf_suite.py:236` | Restore only on the success path (finally wraps only the return) → exception leaves process pinned to a deleted temp DB |
| Q27 | `graph/prf.py:22` | Byte-duplicate of `schema._unicode61_tokens` with a load-bearing "MUST tokenize identically" invariant nothing enforces |
| Q28 | `graph/incremental.py:879` | Dead `incremental_via_rebuild` (zero callers) with inconsistent return shape — int vs list under the same key |
| Q29 | `graph/incremental.py:500` | File-row lookup duplicated from `reindex_paths` with a "must agree" invariant only copy-paste enforces |
| Q40 | `knowledge/store.py:159` | Doc-type slugification (`slugify(x) or "general"`) hand-mirrored in 3 places; drift desyncs staged paths/ADR pointers from created ids |

### P2 — Low (88)

| ID | Location | Issue |
|----|----------|-------|
| C7 | `graph/blast.py:66` | No hunk-state tracking: content lines starting `-- `/`++ ` parsed as headers, overwrite parsed paths |
| C10 | `graph/taint.py:299` | Fuzzy hops join by bare name, no repo predicate — contradicts "same-repo" docstring |
| C16 | `refs.py:150` | Unescaped LIKE in `symbol_exists` (`_escape_like` exists unused here) → dead refs false-positive as live |
| C18 | `viz/renderers.py:20` | Node-id sanitize+truncation with no collision handling → distinct symbols merge in rendered graphs |
| C19 | `review/engine.py:221` | Wiki "no results" detected by substring of rendered output → page containing the marker drops the section |
| C20 | `graph/embeddings.py:645` + `reranker.py:128` | `_CACHED_NO_EXIST` sentinel treated as cache hit → partially cached model reported complete |
| C21 | `graph/embeddings.py:1005` | `_embed_openai` no envelope validation (unlike `_embed_server`); short lists truncate silently via zip |
| C22 | `graph/reranker.py:182` | Unlocked global model cache — embeddings documents and locks this exact race |
| C23 | `graph/lsp.py:69` | Single `read()` on a bufsize=0 pipe → short read desyncs JSON-RPC, times out pending requests |
| C26 | `memory/store.py:494` | memory_refs deleted by relative id but writers persist absolute `concept_id` → orphan rows |
| C27 | `memory/recurrence.py:44` | SELECT-then-INSERT on the sig PRIMARY KEY, no IntegrityError handling → concurrent recorders abort the loser |
| C31 | `llm/tasks.py:261` | Refusals (not-in-progress, ownership) return `dropped: True` → CLI prints "dropped after 3 failed attempts" |
| C32 | `llm/tasks.py:260` | complete/drop check-then-act race, last writer wins (no lock/re-check) |
| C33 | `mcp_server/metric_buffering.py:182` | Positional `popleft(len(batch))` after commit drops never-flushed rows when the deque overflowed mid-flush |
| C34 | `mcp_server/metric_buffering.py:306` | Size cap applies only to `str` results → structured-output tools return untruncated models past the 60k cap |
| C35 | `mcp_server/tools_compass.py:112` | ask_compass file-path loop unguarded (get_compass wraps the identical loop) → one corrupt file fails the tool |
| C38 | `wiki/catalog.py:117` | `_slug` non-injective, no page_id dedupe → two racing tasks can target one manifest key |
| C39 | `wiki/pipeline.py:111` | Enrich tasks drop the page's `diagrams` flag (retry producer preserves it) → no Mermaid instruction on enrich |
| C41 | `compass/generator.py:296` | Build-Failure-Patterns empty-section fallback unreachable (scans lines accumulated across the whole doc) |
| C42 | `compass/gaps.py:67` | Unescaped, unscoped substring LIKE inflates module counts past the gap gate |
| C43 | `compass/flow_gaps.py:72` | Coverage keyed on `name#suffix` resource but `compass flow` writes bare name → duplicate flow compasses generated |
| C49 | `parsers/kotlin.py:735` | Tail-identifier fallback sweeps call_suffix → argument identifier becomes the call target |
| C50 | `parsers/typescript.py:484` | Generic `implements I2<T>` produces no implements edge (extends works) |
| C51 | `parsers/swift.py:211` | Dead init branch — initializers parse as `init_declaration`, never reach it; golden pins zero init symbols |
| C52 | `parsers/routes.py:16` | Dead stem set + comment describing "route.ts handled below" that doesn't exist |
| C56 | `dashboard/templates/tokens_region.html:60` | Double period in empty state (message + macro both add one) |
| C57 | `dashboard/templates/knowledge_graph.html:28` | "X of X edges shown" — same variable both sides of "of" |
| C58 | `dashboard/templates/history_region.html:35` | Four-line filter-preservation block pasted ×3 in one template |
| C59 | `dashboard/templates/chains.html:2` (+3 templates) | Dead imports (4 of 5 named templates; knowledge_graph's links IS used) |
| C60 | `dashboard/templates/settings.html:22` | Inline banner recipe hand-copied from `_embed_banner.html`, already drifted (font-size) |
| C61 | `dashboard/routes/knowledge.py:144` | Intended docstrings placed after the first import → inert string expressions |
| C62 | `dashboard/routes/wiki.py:86` | Docstring claims "permanent redirect", handler returns 307 (temporary; tests pin 307) |
| C63 | `dashboard/shell.py:76` | Dead `selector_context`, divergent duplicate of shell_context's selector |
| C64 | `dashboard/routes/{knowledge,settings,wiki}.py` | 10 pure-forwarding wrappers over module-level `_handler`s; no test imports them |
| C66 | `hooks/claude_hooks.py:226` | Truncation to 4000 chars BEFORE `strip_private_data` → a split secret matches nothing, partial secret stored |
| C67 | `hooks/claude_hooks.py:105` | Non-object stdin JSON (null/list/str) crashes session_end/post_tool_failure despite "never raises" contract |
| C71 | `graph/ann_index.py:378` | Behaviorally dead if/else kept for 2-arg test monkeypatch doubles — a test-only seam |
| C72 | `graph/builder.py:1097` | Vacuously-true filter clause (`set(s) != {"."}` after split on ".") — dead code |
| C76 | `wiki/generator.py:18` | Dead passthrough wrapper exported as the package's only `__all__` symbol |
| S2 | `agent_install/merge.py:128` | `_backup_to_bak` unconditionally overwrites an existing .bak → destroys the only preserved copy |
| S5 | `cli/bench.py:162` | Unvalidated SWE-bench row fields (repo/base_commit) reach rmtree/checkout/os.replace paths — hardening gap |
| S6 | `graph/scanner.py:556` | Symlinked files (not dirs) indexed → content from outside the repo root enters the graph |
| S11 | `scripts/uninstall.sh:177` | `PYTHON="uv run python"` invoked quoted → can never exec; pip-detection branch silently never runs |
| S12 | `scripts/measure_memory_health.py:16` | Hardcoded personal `/Users/tan.le/.Trash/...` path in docstring + runtime hint |
| S13 | `scripts/verify_datasource.py:410` | `count_mismatch` labeled "tree-hash mismatch" in the human report |
| S14 | `benchmarks/datasource/ds2/verify_dataset.py:219` | Uncaught corpus KeyError instead of the documented exit-1 stale report |
| S15 | `benchmarks/datasource/ds2/verify_dataset.py:314` | Same unresolved count printed under two labels ("unresolved"/"aspirational") |
| S16 | `.github/scripts/bench_compare.py:69` | Committed-baseline fallback mislabeled "rolling CI" when only the .sha sidecar exists |
| Q3 | `cli/bench.py:612` | CLI sets `os.environ["CAIRN_DB"]` with no cleanup → suites' restore target is a deleted temp path |
| Q4 | `bench/perf_suite.py:223` | Dead try/except around query-op append (plain module-level import can't raise) |
| Q5 | `bench/swe_bench_suite.py:154` (+agent_suite) | Env snapshot/restore helper copy-pasted verbatim ×2 (+1 variant) |
| Q6 | `cli/agents.py:12,217` | Supported-client list duplicated as inline click.Choice literals; CLIENTS registry exists |
| Q7 | `cli/bench.py:548,632` | Payload stamp-and-emit block + 4-line comment duplicated verbatim |
| Q8 | `cli/ask_context.py:52` | Every route_query failure reported as store state "locked" |
| Q9 | `cli/ask_context.py:138` | Strips only .kt/.java → Python files get no wiki/memory context enrichment |
| Q10 | `bench/report.py:59` | `to_table` docstring claims TTY-conditional print; display prints unconditionally |
| Q12 | `cli/system/report.py:291` | Docstring says "the 10 doctor checks"; doctor runs 11 |
| Q13 | `cli/knowledge.py:49,76,702` | `_split` defined identically three times in one file |
| Q14 | `cli/memory.py:172,350,404` | Refs-verified rendering block copy-pasted ×3 |
| Q15 | `cli/system/doctor.py:64` | `_parse_ts` duplicates `metrics._fmt_ts` incl. rationale docstring |
| Q16 | `cli/hooks_viz.py:16` + `hooks/git_hooks.py:53` | `--cairn-dir` flag threads into a parameter documented as unused |
| Q17 | `cli/validate.py:9` (+knowledge/compass/serve/memory) | Dead noqa re-exports of `_helpers` names nothing imports |
| Q18 | `cli/uninstall.py:39` + `upgrade.py:58` | `_detect_install_method` duplicated; copies already differ (in-tree venv branch) |
| Q19 | `cli/uninstall.py:231` | `_stale_artifacts()` (filesystem walk) called twice, first result discarded |
| Q20 | `cli/wiki.py:191` + `dashboard/data.py:1308` | `_split_page_key` duplicated; must stay in sync |
| Q21 | `compass/generator.py:111,388` | concept_id/title/tags construction hand-rolled in both deterministic and LLM paths |
| Q22 | `cli/update.py:112` | `get_db` re-imported under a second alias in the same function |
| Q23 | `dashboard/app.py:23` | `TASK_STATUSES` omits `dropped` → ?status=dropped silently degrades to "all" |
| Q24 | `cli/task.py:98` + `cli/knowledge.py:38` | Unguarded `read_text` → raw FileNotFoundError tracebacks where neighbors exit cleanly |
| Q26 | `cli/system/doctor.py:519` | `(now - last_dt).days` truncation → 7-day staleness threshold fires at 8 days |
| Q31 | `graph/incremental.py:282` | try spans post-COMMIT embed block → healthy file recorded as parse failure |
| Q32 | `graph/incremental.py:492` | Fallback arm substitutes bare filename if prefix check disagrees with lexical check — dead in practice (all current callers pass normalized paths), reachable via non-normalized paths *(validated: "dead" qualified)* |
| Q33 | `graph/schema.py:147` | Dead `repo_deps` table created in every DB, never read/written |
| Q34 | `graph/cross_repo.py:134` | Namespace prefix match lacks `.` boundary → `xyz.be.common_extra` counted under `xyz.be.common` |
| Q35 | `graph/config.py:130` | Scalar-string branch unstripped vs list branch stripped → padded exclude patterns match nothing |
| Q36 | `eval.py:275,300,325` | First-match rank scan triplicated; l1 re-inlines `_result_field`/`_retrieve_l1` |
| Q37 | `graph/stats.py:111` | `get_tree` declares `prefix` param it ignores; no caller passes it |
| Q38 | `skillgen/assembly.py:128` | Re-implements the compass matching loop from `tools_compass.get_compass` |
| Q39 | `skillgen/selector.py:8` + dashboard routes | Underscore-private reach-ins (`_resolve_module`, `_projection`, `_EXPORT_ROW_LIMIT`, `_EPOCH/_parse_ts`, `embeddings._backend_name/_SERVER_FAMILY`) |
| Q41 | `knowledge/ingest/adapters.py:18` et al. | Dead/duplicated surface: mirrored 10MB cap, dead `CONVERTED_TAG`, unimplemented `SourceAdapter`, `invalidate_search_index` never called by 6 unlink sites *(validated: count corrected; the "half the Decision vocabulary unwritten" sub-claim could not be verified — no such vocabulary exists)* |
| Q42 | `okf/bundle.py:113` + `parsers/_registry.py:79` | Dead pre-3.9/3.10 version gates (is_relative_to, entry_points(group=)) — the bundle shim is itself incorrect (startswith accepts sibling roots) |
| Q44 | `parsers/go.py:173` | `receiver_type` param dead — only call site passes None, method branch unreachable |
| Q45 | `parsers/rust.py:61` | Only bare `node.text` use in src/parsers — bypasses `_node_text` byte-slice contract |
| Q46 | `cli/_helpers.py:33` | `_shorten` bare `startswith` mangles sibling-dir paths (`/a/bc/d.py` → `c/d.py` for ws `/a/b`) |
| Q48 | `llm/client.py:96` | Float-equality branch behaviorally identical to fall-through — dead code |
| Q49 | `mcp_server/server.py:218` | Timestamped stderr-print block repeated 8× inside run() |
| Q50 | `mcp_server/tools_graph.py:134,220` | get_callers_data/get_callees_data duplicate the same ~35-line scaffolding |
| Q51 | `agent_install/clients/kilo.py:21` et al. | kilo config body byte-identical to opencode; `_strip_mcp_kilo` duplicate; serve-command stdio entry ×3 (opencode/kilo `"type": "local"` entry ×2); `resolve_cg_str` exported with zero callers; uninstall returns cross=None only when no clients are targeted; claude settings probe checks keys cairn never writes *(validated: two sub-claims corrected)* |

### P3 — Systemic clusters (3)

| ID | Scope | Issue |
|----|-------|-------|
| SY1 | ~40 files, ~160 hits | AGENTS.md comment-rule violations: decision-log/rationale prose, incident stories, "previously/used to", task/spec/audit IDs (`T013`, `FR-005`, `spec §6.4`, `audit F1`, `US4-AC2`, `P0-2`), volatile values (pinned versions, benchmark numbers, file:line refs), multi-paragraph docstrings — across `src/cairn/`, `scripts/`, the extension. Samples confirmed 12/12 |
| SY2 | ~24 files | Stale/contradicting documentation: docstrings stating behavior the code doesn't have, dangling "see module docstring" pointers, references to nonexistent paths (`src/graph`, `src/knowledge`), rotted line-number references |
| SY3 | ~15 duplication sites | Mechanical copy-paste duplication covered by the reuse rule (two-plus call sites get one shared helper): `_split` ×3, refs-render ×3, timestamp block ×8, `_detect_install_method` ×2, `_split_page_key` ×2, payload-emit ×2, click.Choice ×2, kilo/opencode copies, stdio-entry ×3, env-restore ×3, template filter block ×3, `server.ts` defaultProbe vs getJson, critic-marker ×3, `_latest` ×2 |

---

## P0 detail — evidence

### C77 · `python -m cairn.cli.main` fallback can never run the CLI — hooks silently no-op
`src/cairn/hooks/claude_hooks.py:31`, `src/cairn/agent_install/_common.py:83`, `src/cairn/agent_integration/skill/scripts/impact_guard.py:23`

`cli/main.py` has no `if __name__ == "__main__"` block (verified: grep empty) and the entry point is `cairn = "cairn.cli:main"` (pyproject.toml:176). `python -m cairn.cli.main` therefore defines the click group and exits 0 without doing anything. Every hook (post_edit, session_end, session_start, post_tool_failure) silently no-ops in exactly the source-checkout / not-on-PATH environment the fallback exists for (`_run_cg` returns stdout without checking the subprocess did anything); in `impact_guard.py` the empty stdout then crashes `json.loads("")`. Fix: point the fallback at `cairn.cli` (the package whose `main` is the entry) or add the guard.
*Validated: live repro — `.venv/bin/python -m cairn.cli.main` exits 0 with empty stdout; all three fallback sites, the unchecked `_run_cg` return, and the `json.loads` crash verified.*

### C1 · Query-time graph repair never converges when a file's parser is unavailable
`src/cairn/graph/watcher.py:100` (+ `scanner.py:560` vs `:605-621`)

`_detect_changed` scans new files with `scanner.iter_source_files`, which lacks the `is_language_available` gate that `iter_files_and_skips` has. A workspace file whose grammar isn't installed (e.g. `.rs` without the rust extra) is flagged drifted on **every** graph read; `reindex_paths` can neither reindex nor delete it, and strict repair raises `RuntimeError("graph refresh incomplete")` instead of answering. Every MCP graph tool and `cairn def/callers/...` with default `--refresh` is dead until the file is removed — a state `cairn build` also never resolves (it skips the file).
*Validated: full chain confirmed; nuance — the parser-unavailable case raises via the `watcher.py:99` "graph refresh failed" branch (get_parser raises), not the quoted "graph refresh incomplete" message; the every-read-fails outcome is identical.*

## Systemic clusters — detail

- **SY1 — comment-rule violations.** Samples confirmed 12/12: `graph/semantic.py:36` (calibration essay with measured, rejected alternatives), `graph/embeddings.py:806` (incident story: "users read it as a hang"), `graph/embeddings.py:958` ("the old unescaped vec_% swept up…" — why-it-changed), `graph/reranker.py:390` (benchmark numbers as decision rationale), `paths.py:29` (corruption incident + "ruling"), `graph/ann_index.py:243` ("Spike findings… PERF-4 P4.1" + pinned versions), `mcp_server/metric_buffering.py:193` ("Previously… spawned its own daemon thread"), `llm/tasks.py:61` (rationale block), `scripts/install.sh:73` (dated incidents "2026-07-21/22"), `scripts/fetch_t3_corpus.py:16` (FR-006/AC7, TC-030..033, D-009), `scripts/measure_warm_time.py:26` (T013 + rotted file:line), `extensions/vscode/src/providers/codeLens.ts:2` (FR-001/FR-004). AGENTS.md routes these to `record_memory`, never code. Needs one dedicated cleanup pass (a prior `style: strip history tags` commit started this).
- **SY2 — stale/contradicting docs.** Confirmed samples: `cli/main.py:142` and `mcp_server/server.py:190` both cite `_server_core.py:75`, the FastMCP pin is at :68; `knowledge/search.py:13` cites `src/graph/tokenize.py` (doesn't exist — 5-6 files carry such paths); `knowledge/ingest/identity.py:19` cites `okf/utils.py:13-20`, slugify is :9-16; `knowledge/workflow.py:329` says "Write via add_document", code calls `bundle.write_concept` (and the comment at :320 states the opposite); `memory/privacy.py:23` describes JS-style `lastIndex` semantics the Python code doesn't have; `graph/scanner.py:347` says "collect lazily", code does eager `rglob`; `graph/traversal.py:356` claims "BFS", the walk is recursive DFS; `compass/router.py:312` docstring example contradicts its own stem table; `memory/scoring.py:31` says "6-signal", the dict has 8.
- **SY3 — mechanical duplication.** Verified pairs: `cli/knowledge.py` `_split` ×3 (:49, :76, :702); `cli/wiki.py:191`/`dashboard/data.py:1308` `_split_page_key` verbatim; `mcp_server/server.py` timestamp block ×8 (219, 230, 271, 288, 292, 319, 328, 363); `cli/uninstall.py:35`/`cli/upgrade.py:53` `_detect_install_method` (already drifted); `cli/memory.py` refs-render ×3.

## Test gaps (behaviors confirmed defective that no test pins)

`trace_flow` diamond graphs (tests are trees/cycles only) · `purge_archived` (no test; the Z-suffix class would also need a 3.10-floor run — CI's newer Python accepts 'Z' and masks it) · blast pure-deletion hunks (tests cover only content-modifying) · `verify_no_code_change.py` (no test file at all) · PRF second-pass failure · wiki enrich `diagrams` propagation · droid dry-run reporting · `_find_tracked_file_row`/`reindex_paths` agreement · rerank cache concurrency. Adding the missing pins alongside the fixes is the cheapest way to keep them fixed.

*All nine gaps confirmed by the validation pass — zero pinning tests found for any of them.*

## Residual risks / coverage notes

- Security lens came back clean on the highest-risk surfaces (dashboard XSS chain: escape-first markdown + autoescape + `|safe` seams verified end-to-end; MCP tool-arg → SQL/FTS5; hooks stdin; install/uninstall destructive ops — the two real scope bugs are C28/S1). Findings there are hardening/robustness, not exploitable paths.
- Python-floor divergence: several defects (C24, C25, Q42) manifest only on the declared 3.10 floor or with hand-authored data; CI evidently runs newer Python. *Validated: PR CI runs only 3.14 (ci.yml:156) — the full 3.10-3.14 matrix runs only on push.*
- The cairn knowledge layer itself has **no compass or wiki coverage for any module** (`get_compass` empty for graph/cli/mcp_server/dashboard/memory/parsers) — the audit's scout map had to be built from the graph and structure. Generating compasses for the six core modules would make the next audit and every onboarding cheaper. *Re-validated for graph and parsers: still empty.*
- `cairn update` post-task hygiene ran (clean tree, 0 files to reindex); no code was modified during this audit.

## Suggested fix order

1. C77 (hooks fallback) + C1 (watcher non-convergence) — user-visible dead features.
2. C28 (uninstall destructive scope) + S1 + C66/S7 (privacy) — destructive/privacy class.
3. C24/C25 (datetime floor), C2/C5/C6 (graph correctness), C44–C48 (parsers), C36/C37/C70 (wiki queue).
4. SY1/SY2 comment+docs cleanup pass; SY3 helper extraction.
5. P2 items opportunistically, grouped by module.

*Validation did not change this ordering.*

---

## Validation detail — 2026-10-02

Independent re-derivation of every point at HEAD `98fc2d8` (14 module-scoped passes; skeptical re-reads plus targeted read-only repros; no code modified). **141/141 findings checked: 136 CONFIRMED as written, 5 PARTIAL (corrected in the tables above), 0 REFUTED, 0 UNVERIFIABLE. All 26 systemic samples and all 9 test-gap claims confirmed.**

### Gate and method claims re-verified

| Audit claim | Re-check | Result |
|---|---|---|
| `pytest`: 3818 passed, 2 skipped | full re-run on `.venv` (Python 3.14.7) | ✅ 3818 passed, 2 skipped (7m28s) |
| `bash -n` on all 8 tracked shell scripts | re-run | ✅ all 8 pass |
| `ruff check .` (F-only, mirrors CI) | `ruff check --select F .` | ✅ all checks passed |
| entry point `cairn = "cairn.cli:main"` (pyproject.toml:176) | grep | ✅ exact |
| requires-python floor 3.10 (basis of C24/C25/Q42) | pyproject.toml:11 | ✅ `>=3.10`; PR CI runs only 3.14 (ci.yml:156), masking the floor bugs |
| no compass/wiki coverage for any module (residual risk) | `get_compass` for graph, parsers | ✅ empty |

### The 5 PARTIAL findings — corrections (already folded into the tables)

1. **S1** — strip-helper half confirmed (all six uninstall strip helpers crash on non-dict values where install guards via `_merge_keys_non_object`). **Correction:** `_already_installed` is not broadly in the crash class — it isinstance-guards top-level `mcpServers`/`mcp`/`hooks`; its only hole is the nested `mcp.get("servers", {}).get("cairn")` chain (non-dict `mcp.servers`).
2. **C14** — escaping-order bug real (repro'd at the LIKE layer: `_search_like("repo_map")` returns only exact matches). **Correction:** the headline example is overstated — end-to-end `search_symbols("repo_map")` still returns `build_repo_map` via the FTS phrase-prefix path; the degradation bites only when LIKE runs alone (FTS unavailable/error) or for names FTS can't reach (camelCase).
3. **Q32** — the fallback arm is dead for every path current callers produce. **Correction:** not dead *by construction* — non-normalized raw strings (e.g. `/a//b/c`) can reach it, so "dead" should read "dead in practice".
4. **Q41** — 4 of 5 sub-claims confirmed (mirrored 10MB cap, dead `CONVERTED_TAG`, unimplemented `SourceAdapter`, never-called `invalidate_search_index`). **Corrections:** unlink sites are **6**, not 5; the "half the Decision vocabulary unwritten" sub-claim is **unverifiable** (no such vocabulary construct exists in the code).
5. **Q51** — core duplication confirmed (kilo ≡ opencode body; `_strip_mcp_kilo` dupe; `resolve_cg_str` zero callers; claude probe checks keys cairn never writes). **Corrections:** "stdio entry ×3" holds only for the `{"command": …, "args": ["serve"]}` family (`_common.py:124`, `agy.py:51`, `zcode.py:46`) — the opencode/kilo `"type": "local"` entry is ×2; and kilo uninstall returns `cross=None` only when *no* clients are targeted, not "while deleting" unconditionally.

### Confirmed findings with precision notes (finding stands, detail corrected)

- **C1** — the parser-unavailable case raises via the `watcher.py:99` "graph refresh failed" branch (get_parser raises), not the quoted "graph refresh incomplete" message; the every-read-fails outcome is identical.
- **C3** — Python call edges store column=0 (not NULL) — same wrong probe either way.
- **C21** — the silent-truncation `zip` lives at the write sites (embeddings.py:1380 etc.), not inside `_embed_openai`; effect as described.
- **C24** — `scripts/measure_memory_health.py:254`'s fallback is `continue` (row skipped → freshness "unavailable"), not age=0; the two purge sites (`memory/store.py:545`, `cli/memory.py:590`) do swallow to age=0, so "purge never purges" stands.
- **C25** — `cairn update` itself survives via the blanket except at `cli/update.py:97` (the decay pass aborts with a warning); `memory batch-critic` crashes outright.
- **C59** — conservative if anything: the dead-import pattern exists in 6 templates (chains, history, tokens, tasks, knowledge, wiki), not 4; knowledge_graph's `links` IS used.
- **C71** — the else arm does execute in production (`source="embeddings_mv"` from search_pipeline.py:107); the point is the two arms are behaviorally indistinguishable — the split exists only for 2-arg test doubles (6 monkeypatch sites verified).
- **C73** — `knowledge_status` is annotated `destructiveHint=False` (write-tier, not destructive); the duplicated-guard drift risk stands.
- **Q36** — module is `src/cairn/eval.py`, not `graph/eval.py`.
- **Q49** — 7 of the 8 duplicated blocks print to stderr; the 8th (SSE-listening, server.py:364) prints to stdout.
- **S11** — `resolve_store` is rescued by its own hard-coded *unquoted* `uv run python` fallback, so store resolution still works; every `"$PYTHON"` invocation (119, 367, 418) is broken as described.
- **S14** — for the crashing input, exit-2 ("malformed dataset") is arguably as apt as exit-1; either way an uncaught KeyError matches no documented path.
- **S17** — latent today: committed t2/ds2 datasets contain zero L4 rows; the KeyError fires only when an L4 row is added.

---

## Appendix — per-finding validation roll

Verdicts recorded during validation against the original audit wording (the index tables above already carry the corrected sub-claims for the 5 PARTIALs). Evidence anchors are file:line reads unless marked "repro'd" (live read-only reproduction).

### P0 (2/2 confirmed)

| ID | Verdict | Note |
|----|---------|------|
| C77 | CONFIRMED | live repro: `.venv/bin/python -m cairn.cli.main` exits 0 silently; all 3 fallback sites + `json.loads("")` crash verified |
| C1 | CONFIRMED | full chain verified; raises via "graph refresh failed" branch (see precision note) |

### P1 (49 confirmed, 2 partial)

| ID | Verdict | Note |
|----|---------|------|
| C2 | CONFIRMED | scoped materialize_import_edges deletes repo-wide, re-derives in-scope only |
| C3 | CONFIRMED | column stored as 0 (not NULL) — same wrong probe either way |
| C4 | CONFIRMED | inner except OperationalError → 0 reclassifies locked as legacy |
| C5 | CONFIRMED | `count == 0` skip precedes seeding; whole-file deletions skipped too |
| C6 | CONFIRMED | repro'd: octal escapes + quoted rename lines both fail |
| C8 | CONFIRMED | repro'd diamond: M appears twice in branches; guard is insert-only |
| C9 | CONFIRMED | repro'd: entry seeded visited without terminator test → no path |
| C11 | CONFIRMED | repro'd: `app/contest_data/loader.py` classified is_test=True |
| C12 | CONFIRMED | except path assigns [] over good first-pass candidates |
| C13 | CONFIRMED | banner renders from drifted_paths only; MCP prepends unconditionally |
| C14 | PARTIAL | LIKE-layer bug real + repro'd; end-to-end example overstated (FTS still finds it) |
| C15 | CONFIRMED | repro'd struct.error with numpy blocked; numpy path filters |
| C17 | CONFIRMED | `_parent` returns entry's own symbol → source==target edges |
| C24 | CONFIRMED | Z-suffix writes + bare fromisoformat → age=0; PR CI only runs 3.14 |
| C25 | CONFIRMED | ValueError-only try; naive strings pass through by design |
| C28 | CONFIRMED | repro'd store_key divergence (symlinked ws) → whole-home branch, `-y` skips prompt |
| C29 | CONFIRMED | only in-progress/done/dropped ever written; critic-exhausted → done |
| C30 | CONFIRMED | sole caller omits claimer; guard at tasks.py:274 unreachable |
| C36 | CONFIRMED | False return discarded at pipeline.py:79 (cli/wiki.py:287 checks it) |
| C37 | CONFIRMED | neither helper filters repo_id; deterministic planner does |
| C40 | CONFIRMED | subquery by bare name, no file/repo constraint |
| C44 | CONFIRMED | live tree-sitter-swift parse: pattern→simple_identifier, no identifier child |
| C45 | CONFIRMED | live parse: mods=['class']; golden pins modifiers:["class"] |
| C46 | CONFIRMED | repro'd: field decorator lands on next declaration's symbol |
| C47 | CONFIRMED | repro'd: default-value call edges dropped; type refs still emit |
| C48 | CONFIRMED | no cwd= anywhere; specs pass no repo path |
| C53 | CONFIRMED | Jinja `None - None` TypeError verified in venv → 500 |
| C54 | CONFIRMED | lists all names, prints skipped[-1].reason |
| C55 | CONFIRMED | anchor keeps expand/window; poll keeps session too |
| C65 | CONFIRMED | cssVar/spiral/htmx-race/zoom spot-checked ≈100 lines, drifted |
| C68 | CONFIRMED | stat() outside try; read below guarded |
| C69 | CONFIRMED | flush cycle split across two lock windows; _FLUSH_LOCK guards only _flush_events |
| C70 | CONFIRMED | --llm branch never reads dry_run; returns before dry-run summary |
| C73 | CONFIRMED | verbatim dupe; comment concedes; shared _refuse_out_of_namespace exists unused |
| C74 | CONFIRMED | only test caller; batch retries 3×, single-shot bare |
| C75 | CONFIRMED | ~70-line dupe; dead `kept` counter in both |
| S1 | PARTIAL | strip helpers confirmed; _already_installed guards top-level (see corrections) |
| S3 | CONFIRMED | hardcoded ~/.gemini vs installer's APPDATA/XDG paths |
| S4 | CONFIRMED | dry-run + droid-on-PATH misses both branches |
| S7 | CONFIRMED | both halves repro'd: plain-string creds pass _coerce_attrs; netloc keeps user:pass@ |
| S8 | CONFIRMED | cp self-copy exits 1 (verified); set -euo pipefail aborts |
| S9 | CONFIRMED | all 6 REPOS dirs absent from repo root; unconditional `cat >` clobber |
| S10 | CONFIRMED | unregistered mcp__*__* accepted; not scoped to wrong_calls |
| S17 | CONFIRMED | VALID_LEVELS admits L4; summary_out lacks the key (latent: 0 L4 rows today) |
| S18 | CONFIRMED | commit mode false-clean via continue; tree mode uncaught FileNotFoundError |
| Q1 | CONFIRMED | no restore anywhere in scaling_suite; all 3 siblings restore |
| Q2 | CONFIRMED | finally wraps only `return report` |
| Q27 | CONFIRMED | line-for-line dupe; no test ties them |
| Q28 | CONFIRMED | zero callers; int vs list under repos_rebuilt |
| Q29 | CONFIRMED | duplicated lookup; invariant comment only |
| Q40 | CONFIRMED | 3 hand-mirrored slugify sites (store, staging, adr) |

### P2 (86 confirmed, 2 partial)

| ID | Verdict | Note |
|----|---------|------|
| C7 | CONFIRMED | synthetic-diff repro: `-- `/`++ ` content lines overwrite paths |
| C10 | CONFIRMED | fuzzy join has no repo predicate |
| C16 | CONFIRMED | in-memory sqlite repro: foo_bar matches pkg.fooXbar; % reaches via wiki/sources.py:63, workflow.py:236 |
| C18 | CONFIRMED | alnum→_ + [:30], no collision handling |
| C19 | CONFIRMED | marker `" results matching '"` substring on rendered output |
| C20 | CONFIRMED | _CACHED_NO_EXIST ≠ None → partial cache reads complete; repo's own model_warmup comment concedes |
| C21 | CONFIRMED | zip at write sites (see precision note) |
| C22 | CONFIRMED | unlocked check-then-act; embeddings documents+locks the same race |
| C23 | CONFIRMED | raw FileIO single read(n), no accumulation loop |
| C26 | CONFIRMED | DELETE on _rel_id vs writers' absolute concept_id |
| C27 | CONFIRMED | sig TEXT PRIMARY KEY; no IntegrityError handling here or in caller |
| C31 | CONFIRMED | both refusal paths return dropped=True; CLI prints "3 failed attempts" |
| C32 | PARTIAL | dead-in-practice, reachable only via non-normalized paths |
| C33 | CONFIRMED | literal form is a popleft() loop, same effect as claimed |
| C34 | CONFIRMED | isinstance(result, str) gate skips structured models |
| C35 | CONFIRMED | scalar branch returns unstripped; list branch strips |
| C38 | CONFIRMED | repro'd _slug collisions; no page_id dedupe; manifest last-write-wins |
| C39 | CONFIRMED | enrich calls plan_facts without diagrams; retry preserves it |
| C41 | CONFIRMED | fallback unreachable — every branch appends a bullet first |
| C42 | CONFIRMED | unescaped/unscoped LIKE; _escape_like exists unused |
| C43 | CONFIRMED | name#suffix keys vs bare-name resource |
| C49 | CONFIRMED | repro'd: getHandler()(argOne) emits bogus go→argOne edge |
| C50 | CONFIRMED | repro'd: generic_type target skipped; extends works |
| C51 | CONFIRMED | grammar emits init_declaration; init branch unreachable; golden pins zero |
| C52 | CONFIRMED | all 7 stems disjoint from {page,index}; comment false |
| C56 | CONFIRMED | message period + macro-appended period |
| C57 | CONFIRMED | edge_count both sides of "of" |
| C58 | CONFIRMED | same 4-line block ×3 (export, Newer, Older) |
| C59 | CONFIRMED | 6 templates with dead imports (claim said 4); knowledge_graph links used |
| C60 | CONFIRMED | drift = missing font-size: 0.85rem |
| C61 | CONFIRMED | strings after imports in _knowledge_graph + _knowledge_graph_inspect |
| C62 | CONFIRMED | 307 returned; tests/test_dashboard_app.py:4200 pins 307 |
| C63 | CONFIRMED | zero references; diverges on LAUNCH_LABEL |
| C64 | CONFIRMED | exactly 10 pure wrappers; no test imports routes modules |
| C66 | CONFIRMED | truncate-then-redact; patterns match whole tokens |
| C67 | CONFIRMED | _read_stdin catches only JSON errors; .get on non-dict raises |
| C71 | CONFIRMED | arms behaviorally indistinguishable; 6 monkeypatch sites verified |
| C72 | CONFIRMED | verified empirically: split parts can never equal "." |
| C76 | CONFIRMED | zero callers; sole __all__ symbol |
| S2 | CONFIRMED | write_bytes truncates the .bak |
| S5 | CONFIRMED | presence-only validation; sha→rmtree/checkout, repo+sha→os.replace |
| S6 | CONFIRMED | dirs skipped, symlinked files yielded; no containment check |
| S11 | CONFIRMED | quoted "$PYTHON" at 119/367/418; resolve_store rescued by own fallback |
| S12 | CONFIRMED | both docstring :16 and runtime hint :282 |
| S13 | CONFIRMED | count_mismatch printed under "tree-hash mismatch" headline |
| S14 | CONFIRMED | corpus KeyError uncaught (exit-2 arguably apt — see precision note) |
| S15 | CONFIRMED | ex['unresolved'] printed under both labels |
| S16 | CONFIRMED | sha-sidecar-only → committed baseline labeled "rolling CI" |
| Q3 | CONFIRMED | env set, never restored; suites snapshot the temp value |
| Q4 | CONFIRMED | append of tuple+lambda cannot raise; import happened earlier |
| Q5 | CONFIRMED | verbatim ×2 + per-var variant |
| Q6 | CONFIRMED | identical inline literals; CLIENTS registry at _common.py:19 |
| Q7 | CONFIRMED | verbatim block + comment ×2 |
| Q8 | CONFIRMED | except Exception → state="locked" |
| Q9 | CONFIRMED | only .kt/.java; OKFBundle.search is literal substring |
| Q10 | CONFIRMED | print_table prints unconditionally; is_tty unused |
| Q12 | CONFIRMED | HEALTH_CHECKS has 11 entries |
| Q13 | CONFIRMED | byte-identical ×3 at exactly :49/:76/:702 |
| Q14 | CONFIRMED | ×3 at 172-176 / 347-356 / 401-409 |
| Q15 | CONFIRMED | same dual-shape parse + near-verbatim rationale docstring |
| Q16 | CONFIRMED | docstring says unused; body never reads it |
| Q17 | CONFIRMED | 5 files, names never used, no external importers |
| Q18 | CONFIRMED | uninstall adds in-tree-venv branch upgrade lacks |
| Q19 | CONFIRMED | walk runs twice, first result shadowed |
| Q20 | CONFIRMED | verbatim except return annotation |
| Q21 | CONFIRMED | identical construction in both paths |
| Q22 | CONFIRMED | get_db + _get_db in one function |
| Q23 | CONFIRMED | dropped written at tasks.py:703; non-member → "all" |
| Q24 | CONFIRMED | both sites unguarded; neighbors exit cleanly |
| Q26 | CONFIRMED | repro'd: 7d23h → .days=7 → not stale |
| Q31 | CONFIRMED | try spans past COMMIT into embed block |
| Q33 | CONFIRMED | single reference repo-wide is the CREATE |
| Q34 | CONFIRMED | plain startswith; dependents LIKE leg same |
| Q35 | CONFIRMED | padded scalar survives into scanner matching |
| Q36 | CONFIRMED | (module is src/cairn/eval.py) |
| Q37 | CONFIRMED | body ignores prefix; sole caller omits it |
| Q38 | CONFIRMED | identical predicate; docstring concedes |
| Q39 | CONFIRMED | every named reach-in found |
| Q41 | PARTIAL | 4/5 sub-claims confirmed; 6 unlink sites not 5; Decision-vocabulary claim unverifiable |
| Q42 | CONFIRMED | gates dead on >=3.10; shim repro'd: sibling root passes |
| Q44 | CONFIRMED | sole call site passes None |
| Q45 | CONFIRMED | only bare .text; worse: strict decode vs errors="replace" |
| Q46 | CONFIRMED | repro'd /a/bc/d.py → c/d.py |
| Q48 | CONFIRMED | default == sentinel constant |
| Q49 | CONFIRMED | 8 blocks (7 stderr + 1 stdout) |
| Q50 | CONFIRMED | ~35-line shared scaffolding |
| Q51 | PARTIAL | core dupes confirmed; stdio×3 and cross=None sub-claims corrected |

### P3 — systemic clusters (3/3 stand, 26/26 samples confirmed)

- **SY1** — 12/12 comment-rule samples confirmed (calibration essay, incident stories, "previously", task/spec IDs, pinned versions/benchmark numbers, rotted file:line refs).
- **SY2** — 9/9 stale-doc samples confirmed (wrong line pins, nonexistent `src/...` paths ×5 more, add_document vs write_concept contradiction, JS-regex semantics, lazy-vs-eager, BFS-vs-DFS, router example vs table, 6-vs-8 signals).
- **SY3** — 5/5 duplication samples confirmed (`_split` ×3, `_split_page_key` ×2, timestamp block ×8, `_detect_install_method` ×2 drifted, refs-render ×3; nit: the 8th timestamp block prints to stdout, the other seven to stderr).

### Test-gap claims — all 9 confirmed

trace_flow diamonds · purge_archived (zero test references) · blast pure-deletion hunks · verify_no_code_change.py (no test file, not wired into CI) · PRF second-pass failure · wiki enrich diagrams propagation · droid dry-run reporting · `_find_tracked_file_row`/`reindex_paths` agreement · rerank cache concurrency.

## Bottom line for the fix pass

The audit is trustworthy as a fix backlog. The only deltas a fixer needs beyond this document: none — the five PARTIAL corrections and the precision notes are folded in above. The suggested fix order stands.

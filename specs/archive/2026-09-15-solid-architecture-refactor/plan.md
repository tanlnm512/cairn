# Plan: solid-architecture-refactor

**Source**: `specs/solid-architecture-refactor/spec.md` (18 FRs, 7 user stories) +
`specs/solid-architecture-refactor/survey.md` (statuses cited below are copied
verbatim from survey.md — this plan does not re-derive status).

**Team context**: solo dev, single umbrella branch, one final implementation
PR (spec.md `Assumptions & risks`). All milestones below land as commits on
that one branch; "parallel" means concurrent *work threads* (subagents/
sessions) during the build, not parallel PRs.

---

## Milestone map

| # | Milestone | User story | FRs | Survey status going in |
|---|---|---|---|---|
| M0 | Baseline lock (gate, no FRs owned) | — | — | n/a |
| M1 | Parser factory | US1 (P1) | FR-001, FR-002, FR-003 | TODO, TODO, TODO |
| M2 | Embedding backend contract & registry | US2 (P1) | FR-004, FR-005, FR-006 | TODO, TODO, DONE (preservation) |
| M3 | Graph persistence repository seams | US7 (P3) | FR-015, FR-016 | TODO, DONE (preservation) |
| M4 | Dashboard modularity | US3 (P2) | FR-007, FR-008 | TODO, DONE (preservation) |
| M5 | Semantic search pipeline | US4 (P2) | FR-009, FR-010 | PARTIAL, DONE (preservation) |
| M6 | LLM backend contract normalization | US5 (P2) | FR-011, FR-012 | DONE (contract exists), TODO (real bug) |
| M7 | Operational CLI modularity | US6 (P2) | FR-013, FR-014 | TODO, DONE (preservation) |
| M8 | Closing guardrails + umbrella PR | — | FR-017, FR-018 | TODO (forward constraint), TODO (forward constraint) |

Every FR appears exactly once above (18/18). "DONE (preservation)" FRs are not
new work — they are the regression contract each milestone must not break;
each milestone's checkpoint re-runs the survey.md verify command that proved
that baseline.

**Note on M3's position**: US7 is spec.md's lowest priority (P3), but M3 is
sequenced third, immediately after M1, not last. This is a dependency-forced
deviation from priority order (see Dependency graph below) — M1 and M3 both
edit `src/cairn/graph/builder.py` and `src/cairn/graph/incremental.py`, so
whichever goes second must start from the other's finished state. Sequencing
M3 right after M1 also pulls the spec's flagged transaction-timing risk
("moving SQLite writes can change transaction timing") forward, consistent
with the method's "risky things pulled early enough to de-risk."

---

## Milestone detail

### M0 — Baseline lock (gate)
Purpose: freeze the regression floor before any refactor edit, so every later
milestone diffs against a known-green tree (spec.md risk mitigation:
"characterization tests, compatibility wrappers, staged milestones, and a
full closing regression gate").

**Checkpoint**:
- `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest -q` (full suite) — record pass count.
- `uv run ruff check src` — preserve the existing pyflakes-only CI gate baseline.
- `uv run ruff check --select C901,PLR0912,PLR0913,PLR0915 --statistics src` — record the FR-018 baseline as complexity (`C901`) 88, branches (`PLR0912`) 52, statements (`PLR0915`) 37, and args (`PLR0913`) 67.
- `uv run mypy --ignore-missing-imports src` — must be clean (HARD gate per survey FR-018 citation of `ci.yml:67-68`).
- `sed -n '25,65p' pyproject.toml` — record the current runtime-dependency list as the FR-017 baseline (survey already captured this at survey.md FR-017; re-snapshot here so M8 diffs against the same anchor).

### M1 — Parser factory (FR-001, FR-002, FR-003)
Demoable: a `ParserFactory`-equivalent abstraction under `src/cairn/parsers/`
owns `BaseParser` construction and caching; `graph/builder.py` and
`graph/incremental.py` call the factory instead of the inline `if/elif`
chain currently at `builder.py:68-123`.

**Touches**: `src/cairn/parsers/*.py` (new factory module), `src/cairn/graph/builder.py`
(the `get_parser`/`_parser_instances` region, `builder.py:45-124`, and the call
site at `builder.py:798`), `src/cairn/graph/incremental.py` (the `get_parser`
import/call at `incremental.py:208-209`).

**Checkpoint**:
- `rg -n "class \w*Parser" src/cairn/parsers/*.py` — expect a factory type present (survey baseline: 17 hits, no factory class).
- `rg -n "get_parser\(" src/cairn/ -g '*.py'` — call sites in builder.py/incremental.py should delegate to the new factory module, not construct parsers inline.
- New/updated test asserting `factory.get_parser("python") is factory.get_parser("python")` (survey FR-002 gap: "no test pins the 'same cached instance' contract explicitly").
- `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_parsers_language_adapter.py -q` → expect 9 passed (survey FR-001 baseline).
- `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_build_graph_connection_cleanup.py tests/test_build_graph_decomposition.py tests/test_build_graph_periodic_commit.py tests/test_build_inmemory.py tests/test_build_runs.py tests/test_incremental_derived.py tests/test_reindex_resolution_invariant.py -q` (indexing-path regression; file list from this session's `ls tests | grep -iE "build|index|incremental"`).

### M2 — Embedding backend contract & registry (FR-004, FR-005, FR-006)
Demoable: `embeddings.py`'s embedding entry points dispatch through the
`EmbeddingBackend` Protocol and registry in `src/cairn/graph/embed_backends.py`,
replacing the duplicated `if backend == "..."` branches (survey FR-005: 9 hits
across `current_model`, `embeddings_available`, an unnamed function near
`:496`, and `_embed`).

**Touches**: `src/cairn/graph/embed_backends.py` and `src/cairn/graph/embeddings.py` only.

**Checkpoint**:
- `rg -n "class .*Backend|EMBEDDING_BACKENDS" src/cairn/graph/embed_backends.py` — expect the contract type and registry.
- `rg -n "if backend ==|elif backend ==" src/cairn/graph/embeddings.py` — branch count should collapse outside the registry itself (survey baseline: 9 hits).
- `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_embedding_model.py tests/test_embed_ladder.py tests/test_embeddings_freshness.py tests/test_embed_cli_adopt.py tests/test_embed_cli_download_model.py tests/test_embed_cli_server_down.py tests/test_embed_commit_tracking.py tests/test_embed_flush_stalled.py tests/test_embedding_backend_quality.py tests/test_embeddings_mv.py tests/test_ensure_semantic_deps.py tests/test_memory_embeddings.py tests/test_semantic_unavailable.py tests/test_update_path_embedding.py -q` (full embed-related file list from survey.md FR-006's `ls tests | grep -i embed` citation) — must preserve resolution order/fallback/cache-invalidation/vector-format/dimensions/model-identity (FR-006).

### M3 — Graph persistence repository seams (FR-015, FR-016)
Demoable: `INSERT INTO files/symbols/imports/edges` (currently inline at
`builder.py:836,985,995,1001,1181` per survey) move behind a repository
seam; `graph/builder.py` and `graph/incremental.py` call the repository
instead of executing SQL directly.

**Touches**: `src/cairn/graph/builder.py` (the `insert_parsed_file`/
`materialize_import_edges` region, `builder.py:810-1207`, disjoint from M1's
`builder.py:45-124`/`798` region but same file), `src/cairn/graph/incremental.py`
(`incremental.py:208-218` import/call block and `incremental.py:330-333`,
same lines M1 touches at `:208-209`), new `src/cairn/graph/repositories.py`
(or equivalent).

**Depends on**: M1 (see Dependency graph — same two files, adjacent lines).

**Checkpoint**:
- `rg -n "class.*Repository|Repository\(" src/cairn/graph/*.py` — expect new repository class(es) (survey baseline: 0 hits).
- `rg -n "INSERT INTO files|INSERT INTO symbols|INSERT INTO imports|INSERT INTO edges" src/cairn/graph/builder.py` — direct SQL in builder.py should drop toward 0 as it moves behind the seam (survey baseline: 5 hits).
- `rg -n "_new_id|build_lock|note_contention" src/cairn/graph/builder.py` — ID generation and transaction/lock machinery (survey FR-016 citations, `builder.py:39,131-132`) must still be reachable from the new seam with the same semantics.
- `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_build_graph_connection_cleanup.py tests/test_build_graph_decomposition.py tests/test_build_graph_periodic_commit.py tests/test_build_inmemory.py tests/test_build_runs.py tests/test_incremental_derived.py tests/test_reindex_resolution_invariant.py tests/test_ann_incremental.py tests/test_ann_index.py -q` (same indexing-path file list as M1, plus ANN-index files, since persistence changes are the higher-risk half of that path).

### M4 — Dashboard modularity (FR-007, FR-008)
Demoable: the 30+ route handlers currently nested as closures inside
`create_app` (`app.py:403-1497` per survey, e.g. `landing` at `:403`,
`workspaces_overview` at `:429`, ... `database` at `:1409`) move into
separate controller units; `create_app` (`app.py:223`) assembles a route
table instead of implementing handlers inline.

**Touches**: `src/cairn/dashboard/app.py` + new `src/cairn/dashboard/routes/*.py`
(or equivalent) only.

**Checkpoint**:
- `wc -l src/cairn/dashboard/app.py` — expect a reduction from the survey baseline of 1520 lines.
- `rg -n "def create_app" src/cairn/dashboard/app.py` — factory should no longer contain nested handler defs.
- `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_dashboard_app.py -q` → expect 157 passed (survey FR-008 baseline, 1 pre-existing unrelated warning).
- Full dashboard surface (survey FR-008 gap listed these as not run this session — run now as this milestone's regression gate): `pytest tests/test_dashboard_accessibility.py tests/test_dashboard_assets.py tests/test_dashboard_data.py tests/test_dashboard_export.py tests/test_dashboard_graph_retheme.py tests/test_dashboard_htmx_lists.py tests/test_dashboard_knowledge.py tests/test_dashboard_knowledge_graph.py tests/test_dashboard_live_soak.py tests/test_dashboard_packaging.py tests/test_dashboard_readonly.py tests/test_dashboard_restyle.py tests/test_dashboard_scale.py tests/test_dashboard_shell.py tests/test_dashboard_theme.py tests/test_dashboard_workspaces.py -q`.

### M5 — Semantic search pipeline (FR-009, FR-010)
Demoable: `semantic_search` (currently one 1238-line-file function starting
at `semantic.py:495`, per survey) is composed from explicit query, retrieval,
fusion, rerank, enrichment, telemetry, and result-assembly stages, using the
existing stage modules (`reranker.py`, `query_enrich.py`, `fusion.py`,
`ann_index.py`) as building blocks rather than inline branches.

**Touches**: `src/cairn/graph/semantic.py`, `src/cairn/graph/reranker.py`,
`src/cairn/graph/query_enrich.py`, `src/cairn/graph/fusion.py`,
`src/cairn/graph/ann_index.py`.

**Interface note (not a file conflict)**: `semantic.py:594,630,745,775,785`
call `emb.is_hash_fallback()`, `emb.current_model()`, `emb.embed_query()` —
public functions of M2's `embeddings.py`. No file overlap with M2, but M5's
checkpoint should be re-run after M2 lands to confirm those names/signatures
are unchanged (already required by M2's own FR-006 checkpoint).

**Checkpoint**:
- `rg -n "def semantic_search|def retrieve|def fuse|def rerank|def enrich" src/cairn/graph/semantic.py src/cairn/graph/fusion.py src/cairn/graph/reranker.py src/cairn/graph/query_enrich.py` — expect an explicit stage/pipeline structure, not one branching function.
- `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_semantic_events.py tests/test_semantic_unavailable.py tests/test_semantic_enrichment.py -q` (survey FR-010 citation, not run at survey time — this is this milestone's regression gate).
- Provenance/degradation strings at `semantic.py:585-592` (`"semantic"|"bm25"|"fused(bm25+semantic)"`, `"degraded": "embedding-backend"`) and telemetry events (`SEMANTIC_BACKEND`, `EMPTY_RESULT`, `RERANK_SKIPPED`) must still be emitted identically.

### M6 — LLM backend contract normalization (FR-011, FR-012)
Demoable: `FileQueueBackend.extract` and `SubprocessBackend.extract` skip
malformed JSON identically. FR-011's `LLMClient` Protocol already exists
(survey: DONE, `client.py:27-33`) — this milestone's real work is FR-012,
the actual behavioral inconsistency survey found: `FileQueueBackend.extract`
(`client.py:84-94`) skips malformed lines with `try/except json.JSONDecodeError:
continue`; `SubprocessBackend.extract` (`client.py:155-159`) has no such
guard and raises uncaught on malformed `{`-prefixed lines.

**Touches**: `src/cairn/llm/client.py` only.

**Checkpoint**:
- `rg -n "def extract" -A 10 src/cairn/llm/client.py` — both backends must show the same skip-malformed-JSON guard.
- New test covering malformed-JSON extraction for both backends (survey FR-012 gap: "no test coverage for extract-malformed-JSON on either backend").
- `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_redaction_chokepoints.py -q` (only existing test survey found exercising these backends: `grep -rl "SubprocessBackend\|FileQueueBackend\|get_client" tests`).

### M7 — Operational CLI modularity (FR-013, FR-014)
Demoable: the former `src/cairn/cli/system.py` module (2145 survey baseline)
splits into the `src/cairn/cli/system/` package by command family;
`_run_doctor` (formerly `system.py:1732`) resolves checks through a registry
instead of calling check functions directly.

**Touches**: `src/cairn/cli/system/*.py` only.

**Interface note (not a file conflict, but re-verify after dependents land)**:
`system.py` imports directly from modules other milestones edit —
`_backend_name`, `is_hash_fallback`, `current_model`, `embed_count` from
`..graph.embeddings` (M2, `system.py:772,814`); `reindex_paths` from
`..graph.incremental` (M1/M3 chain, `system.py:544`); `record_build_run`
from `..graph.builder` (M3, `system.py:616`); `_ms_bucket` from
`..graph.semantic` (M5, `system.py:931` — a private/underscore symbol, so
this is the tightest coupling in the map: if M5 renames or removes
`_ms_bucket`, M7 breaks even though the files never conflict). M7's own code
work is not blocked by M1/M2/M3/M5, but its checkpoint must be re-run once
those land, and any milestone that renames a symbol `system.py` imports
should update that import as part of its own task list.

**Checkpoint**:
- `wc -l src/cairn/cli/system/*.py` — target the split package and expect a reduction from the survey baseline of 2145 former-module lines.
- `rg -n "class.*Check|_CHECKS" src/cairn/cli/system/*.py` — expect a check registry (survey baseline: 0 hits).
- `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_doctor.py -q` → expect 46 passed (survey FR-014 baseline).

### M8 — Closing guardrails + umbrella PR (FR-017, FR-018)
Demoable: the full refactor closes with no new runtime dependency, no new
import cycle, no new static-complexity finding vs. M0's baseline, and a
green full regression suite — then the single umbrella PR is opened per
spec.md's assumption.

**Depends on**: M1–M7 all landed (this gate re-checks the whole merged tree,
not any one subsystem).

**Checkpoint**:
- `sed -n '25,65p' pyproject.toml` diffed against M0's snapshot — no new runtime dependency (FR-017).
- `rg -n "radon|xenon|import-linter|importlinter" pyproject.toml .pre-commit-config.yaml` — survey found no dedicated cycle tool; perform a manual import-cycle check (e.g. `python -c "import cairn"` and importing each touched package root).
- `uv run ruff check --select C901,PLR0912,PLR0913,PLR0915 --statistics src` — FR-018 gate: complexity (`C901`) ≤ 88, branches (`PLR0912`) ≤ 52, statements (`PLR0915`) ≤ 37, and args (`PLR0913`) ≤ 67 versus M0's baseline.
- `uv run mypy --ignore-missing-imports src` clean (HARD gate, unchanged from M0).
- `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest -q` full suite green, pass count ≥ M0's baseline.
- Open the single umbrella PR (spec.md assumption: "the complete implementation lands on one umbrella branch and one final implementation PR").

---

## Dependency graph

```
M0 (baseline lock)
 ├─▶ M1 (parser factory) ─▶ M3 (persistence repository seams)  [same files: builder.py, incremental.py]
 ├─▶ M2 (embedding contract)                                    ─┐
 ├─▶ M4 (dashboard)                                               │
 ├─▶ M5 (semantic pipeline)  [soft: verify against M2's public API]
 ├─▶ M6 (LLM contract)                                            ├─▶ M8 (closing guardrails)
 └─▶ M7 (CLI modularity)  [soft: verify against M1/M2/M3/M5 symbols]
                    M3 ─────────────────────────────────────────┘
```

Only one edge is a **strict file-ownership dependency**: M1 → M3, because
both edit `src/cairn/graph/builder.py` (M1: lines 45-124 + call site 798;
M3: lines 810-1207 — disjoint ranges, same file) and both edit
`src/cairn/graph/incremental.py` at the *same* import/call block
(`incremental.py:208-218` imports both `get_parser` — M1's target — and
`insert_parsed_file` — M3's target — three lines apart; `materialize_import_edges`
— also M3's target — is called again at `incremental.py:330-333`). Two
agents editing that block concurrently would conflict; M3 starts only after
M1's edits to these two files are in.

Every other pair of milestones (M1×M2, M1×M4, M1×M5, M1×M6, M1×M7, M2×M4,
M2×M5, M2×M6, M2×M7, M3×M4, M3×M5, M3×M6, M3×M7, M4×M5, M4×M6, M4×M7,
M5×M6, M5×M7, M6×M7) touches a disjoint file set per the file lists in each
milestone's "Touches" line above — confirmed this session via `rg` for
cross-imports (see interface notes on M5 and M7 above for the two places
where a *symbol*, not a file, is shared).

M8 is the only other forced-serial node: it re-checks the fully merged tree
(dependency diff, import cycles, complexity, full regression) and cannot run
until M1–M7 are all in.

---

## Parallelization map

**Parallel group (all start immediately after M0, no ordering constraint
among them beyond M1→M3):**

| Milestone | Files it owns | Why independent |
|---|---|---|
| M1 | `src/cairn/parsers/*.py` (new), `src/cairn/graph/builder.py:45-124,798`, `src/cairn/graph/incremental.py:208-209` | Only refactor touching parser construction |
| M2 | `src/cairn/graph/embed_backends.py`, `src/cairn/graph/embeddings.py` | No other milestone edits either file (CLI/dashboard/semantic only *call* public functions, don't edit them) |
| M4 | `src/cairn/dashboard/app.py`, new `src/cairn/dashboard/routes/*.py` | Dashboard-only; its one cross-import (`app.py:298` → `embeddings`) is a call, not an edit |
| M5 | `src/cairn/graph/semantic.py`, `reranker.py`, `query_enrich.py`, `fusion.py`, `ann_index.py` | Stage modules are already file-separate from parser/embedding/dashboard/CLI/LLM/persistence work |
| M6 | `src/cairn/llm/client.py` | Fully isolated — imports only `..okf.bundle` and `. tasks`, no cross-refactor coupling at all |
| M7 | `src/cairn/cli/system/*.py` | CLI-only file edits; its imports *from* M1/M2/M3/M5 are read-only call sites, not shared files |

**Strictly ordered pair**: M1 → M3, justified above (shared `builder.py` +
`incremental.py`, same import block).

**Final serial node**: M8, after all of M1–M7.

**Soft interface risks to watch (file-disjoint, symbol-coupled — flag in
task-breaker, don't serialize on them)**:
- M7 imports `_ms_bucket` (private) from M5's `semantic.py:931` (call site) — if M5 renames it, M7's task list needs a one-line import fix.
- M7 imports `_backend_name`, `is_hash_fallback`, `current_model`, `embed_count` from M2's `embeddings.py` — same risk, lower severity since `is_hash_fallback`/`current_model` are already public-contract names FR-006 requires M2 to preserve.
- M7 imports `reindex_paths` (`..graph.incremental`) and `record_build_run` (`..graph.builder`) — both public function names from the M1→M3 chain; low risk since FR-016 requires M3 to preserve behavior, but the module path itself must stay stable.
- M5 imports `emb.is_hash_fallback`, `emb.current_model`, `emb.embed_query` from M2 — covered by FR-006's own preservation mandate, no extra action needed beyond M2's checkpoint.

---

## Self-check

Every FR (FR-001…FR-018) appears in exactly one milestone table row above.
Every milestone (M1–M8) has a checkpoint with at least one command reused
from survey.md's verify commands (cited verbatim) plus, where survey.md had
no command, a command composed this session and marked as such rather than
claimed as already run. M3's position is called out as priority-deviating
and justified by file evidence, not asserted from memory.

# Tasks: embedding-server-backend

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
Status reflects code state per [survey.md](survey.md), not intent: the survey found the
server backend, ladder, parity gate, config substrate, dashboard settings, and
notifications all absent (S01-S13), so every task starts todo; the one DONE-area nuance
(existing local/hash/openai machinery works and is regression-guarded) appears as the
explicit regression-guard task T007, still `- [ ]`.
Phases = plan.md milestones; `[P]`/`(after)` markers carry plan.md's parallelization map
([G1]/[G2]/[G3] + serial spine).

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
| Phase | Total | Done |
|-------|-------|------|
| 1     | 7     | 0    |
| 2     | 2     | 0    |
| 3     | 8     | 0    |
| 4     | 6     | 0    |
| **Σ** | 23    | 0    |

## Phase 1: Server backend core (env-configured) (FR-001, FR-002, FR-003, FR-004, FR-006, FR-009)
<!-- Checkpoint (plan.md): with a live local server whose /v1/models lists the configured
     id, `cairn embed` exits 0 and `SELECT DISTINCT model FROM embeddings` shows
     `server/<netloc>/<model>`; with the server stopped, `cairn embed` exits 1 with a
     remediation hint. Safety: uv run --extra dev pytest tests/test_embedding_backend_quality.py tests/test_model_warmup.py -q -->
<!-- plan.md serial spine: T001-T004 all edit src/cairn/graph/embeddings.py — one owner at
     a time, hence the chain; everything outside embeddings.py (T005, T006) hangs off the
     spine outputs it consumes. -->

- [ ] T001 Introduce the server backend family in the dispatch hub `src/cairn/graph/embeddings.py`: `_backend_name()` accepts `server`/`omlx`/`ollama`; `_effective_backend()` resolves the family to `server` without touching the ImportError coalesce branch (a server name must never fall into `hash` — `is_hash_fallback()` stays False, its conjunction guard per survey S01); preset map `omlx`→`http://127.0.0.1:8000/v1`, `ollama`→`http://127.0.0.1:11434/v1`, bare `server` requires `CAIRN_EMBED_BASE_URL`; tests pinning resolution and `is_hash_fallback()` False for server configs; `_embed_hash`/`_embed_local`/`_embed_openai` bodies and their env names byte-for-byte untouched (FR-001, FR-009)
      verify before implementing: `uv run --extra dev pytest tests/test_embedding_backend_quality.py -q` (13 baseline, survey S12) and `rg -n 'CAIRN_EMBED_BASE_URL' src/` (expected zero hits today — survey S01 gap)
- [ ] T002 (after T001) Add the `_embed_server` stdlib-urllib client beside `_embed_openai` (its request/response shape at `src/cairn/graph/embeddings.py:732-753` is the verbatim template, survey S02) and branch `server` in `_embed()` preserving the `Tuple[List[bytes], int]` contract: `CAIRN_EMBED_SERVER_BATCH` chunking (default 32), retry x3 with exponential backoff 0.5/1/2 s jittered on connection errors/timeouts/5xx/429, other 4xx fails immediately with the server's error body verbatim (oMLX OpenAI-shaped `not_found_error` doubles as remediation text), `CAIRN_EMBED_TIMEOUT` (default 30 s), bearer header only when `CAIRN_EMBED_API_KEY` is set, response `data.sort(key=lambda d: d["index"])` decoded via `_floats_to_blob`, mixed-dimension batches rejected; unit tests against a stubbed HTTP surface (FR-001, FR-003)
- [ ] T003 (after T002) Arm `embeddings_available()` (`src/cairn/graph/embeddings.py:65-83`) with the availability probe: cached per-process `GET {base}/models` with 2 s timeout and optional bearer, True only if HTTP 200 AND the configured `CAIRN_EMBED_SERVER_MODEL` id is listed; extend `reset_backend_cache()` (`embeddings.py:314-320`) to invalidate the probe cache together with `_EFFECTIVE_BACKEND_CACHE`; tests for reachable-and-listed / down / 200-but-model-missing (FR-002)
- [ ] T004 (after T003) Derive server model stamps in `current_model()` (`src/cairn/graph/embeddings.py:39-57`): `server/{netloc}/{model}` from the resolved base URL + model id, with `CAIRN_EMBED_MODEL_STAMP` consulted first as a pure override; tests assert the stamp flows unmodified through `_table_name()` sanitization (`src/cairn/graph/ann_index.py:149-165`, e.g. `vec_server_127_0_0_1_8000_bge-m3`) so vec0 tables, staleness, and purge machinery work untouched — survey S03 (FR-004)
- [ ] T005 (after T003) Shape the `cairn embed` server-down path in `src/cairn/cli/embed.py`: the existing unavailable exits (`sys.exit(1)` at lines 53/66/78 — survey S13) print a server-specific remediation hint (preset base URL, `/v1/models` check, `cairn doctor`) instead of the torch-install text, implementing D-003 loud failure; exit codes unchanged; CliRunner tests per the house convention (survey supporting evidence) (FR-002)
- [ ] T006 (after T003) Extend `_warm_embedder` (`src/cairn/graph/model_warmup.py:154-173`, currently skips anything != `local` — survey S05) with a server arm: on a healthy cached probe fire ONE tiny `/v1/embeddings` POST to trigger the server-side lazy load, sharing the T003 probe cache rather than double-probing; keep `_inside_pytest()` (lines 141-151), offline guard, daemon-thread blanket try/except warning, and never-raise-into-boot behavior byte-identical; extend `tests/test_model_warmup.py` (23 baseline incl. `test_openai_backend_skips_embed_model` — survey S05/S12) (FR-006)
      verify: `uv run --extra dev pytest tests/test_model_warmup.py -q`
- [ ] T007 (after T006) Regression-guard the frozen backends after all Phase-1 edits: run `uv run --extra dev pytest tests/test_embedding_backend_quality.py tests/test_model_warmup.py -q` green and confirm the working-tree diff contains zero hunks inside `_embed_hash`/`_embed_local`/`_embed_openai` bodies, their env names, or `OPENAI_API_KEY` semantics (SC-4 / FR-009); expected outcome is a no-op fix commit or a clean proof run

## Phase 2: Migration alias + parity gate (FR-005)
<!-- Checkpoint (plan.md): corpus embedded under BAAI/bge-m3 locally, switched to oMLX with
     CAIRN_EMBED_MODEL_STAMP=BAAI/bge-m3: `cairn embed` exits 0 with zero vectors
     recomputed and search still correct; pointed at a different model it hard-aborts
     nonzero quoting a measured mean cosine below 0.98. -->
<!-- plan.md [G2]: warmup (T006, Phase 1) and this phase's parity work are file-disjoint;
     this phase needs only the Phase-1 skeleton's pinned `server` value (delivered). -->

- [ ] T008 Create `src/cairn/graph/embed_ladder.py` hosting the parity sampler reused later by doctor and the dashboard button (tech-spec Areas 2/3): sample at most 16 stored chunks under a stamp, dimension match required, mean cosine gate 0.98 as a named module constant (D-007), failures always report the measured value; samples embed through the T002 server client and compare against stored blobs by cosine; unit tests include the >512-token truncation-divergence case (measured 0.991 stays ABOVE the gate by design, D-004) (FR-005)
      do-not-revive note: `purge_stale_models` is dead code (defined, zero call sites — survey S03); old-stamp cleanup rides stamp staleness, not this module
- [ ] T009 (after T008) Hoist the alias preflight into the embed writers in `src/cairn/graph/embeddings.py` before the first INSERT (the `ON CONFLICT(symbol_id, model)` upsert at `embeddings.py:1074-1082` — survey S03): when `CAIRN_EMBED_MODEL_STAMP` aliases a stored stamp, run the T008 parity check BEFORE any row is written; pass keeps rows under the alias stamp with zero re-embeds; fail hard-aborts nonzero quoting the measured mean cosine and dim mismatch; integration tests cover migration-with-alias keeping search correctness and the different-model abort (FR-005)

## Phase 3: Fallback ladder + loud degradation (FR-007, FR-012, FR-013)
<!-- Checkpoint (plan.md): server killed mid-life: `semantic_search` raises nothing, returns
     usable results (bm25-provenanced where the dense leg died) carrying the `degraded` tag
     and hint; exactly one warn line per reason; `cairn doctor` gains the new checks and the
     degradation entry; MCP results carry the footnote; dashboard shows the banner; the adopt
     flow makes the choice permanent.
     Targeted verifies: uv run --extra dev pytest tests/test_doctor.py tests/test_semantic_events.py tests/test_semantic_unavailable.py -q -->
<!-- plan.md [G1]: T010/T013/T014/T015 are mutually file-disjoint arms needing only the
     Phase-3-kickoff agreement on symbol names (event name, reason enum literals,
     `embedding-backend` tag, accessor signatures) — pinned by T011, so each carries
     `[P]` plus the one shared predecessor. T016/T017 extend the same arms to the
     remaining FR-013 surfaces (equally disjoint files).
     plan assumptions carried: rung-1 capability detection = model-list parsing only;
     degradation state process-scoped, persistence undecided — unknown, verify. -->

- [ ] T010 [P] Add `EMBED_SERVER_DEGRADED` to the telemetry catalog `src/cairn/telemetry/events.py` (constant next to `HASH_FALLBACK = "hash_fallback"`, `events.py:42` — survey S07) and the `src/cairn/telemetry/__init__.py` re-export list, choosing the convention deliberately rather than copying the `RERANK_SKIPPED` inconsistency (Area-5 pitfall: not in `__all__`, imported directly — survey S07); add the reason frozenset following the `_SEMANTIC_REASONS` frozenset precedent (`events.py:220`) with exactly the six FR-013 reasons `server_down`, `model_missing`, `parity_fail`, `fallback_session_alias`, `fallback_local`, `hybrid_only`; extend `tests/test_semantic_events.py` (10 baseline — survey S12) (FR-007)
- [ ] T011 (after T010) Implement the availability ladder core in `src/cairn/graph/embed_ladder.py`: evaluated at most once per process per backend-state, cache dropped via a `reset_backend_cache()` hook added in `src/cairn/graph/embeddings.py` (called by ensure_semantic_deps, 7+ test files, `conftest.py:132-134` — survey S01/S12); rung 1 scans `/v1/models` for candidates and adopts a parity-passing one session-scoped through the Phase-2 alias mechanics (zero re-embed), notifying the permanence command; rung 2 falls back to a local model gated on `model_is_cached()` (`embeddings.py:385`) AND parity >= 0.98; rung 3 is the terminal BM25/FTS5-hybrid-only state; hash is never a rung (hard rule, D-003; structurally safe via `is_hash_fallback()`'s conjunction — tech-spec session grep); embed/down failures trigger evaluation exactly once (FR-012)
- [ ] T012 (after T011) Implement `notify_degradation(reason)` in `src/cairn/graph/embed_ladder.py` wiring the shared notification fan-out so all five FR-013 surfaces fire or suppress together (D-010): user-facing warn-once logger line via a PRIVATE once-set (must NOT be silenced by `is_telemetry_off()` — `warn_once` at `events.py:179-194` refuses when telemetry is off, which would violate US3 AC3, so the telemetry-gated variant is reserved for event emission); one `emit(EMBED_SERVER_DEGRADED, reason=<enum>)` per process per reason with host+model-only payload, never request bodies (spec A2.6); expose the accessors the doctor entry, MCP footnote, and dashboard banner consume (FR-013, FR-007)
- [ ] T013 [P] (after T012) Wrap the previously-uncaught dense leg in `src/cairn/graph/semantic.py`: try/except around `emb.embed_query` at `semantic.py:769` and the `_run_pass` call at `semantic.py:1012` (both verified uncaught — survey S04), catching embed errors for ALL backends and mapping any hard failure to the evaluated rung (generic catch per D-011 — the Phase-1 gap fix is intentional, spec A5); rung-3 results ride the existing `provenance="bm25"` path (`semantic.py:995`) and gain the new `degraded` result key with exact tag value `embedding-backend` plus a remediation hint, both absent today (survey S04: only `_fusion_degraded`/`_rerank_degraded` internal flags exist); narrow scope so fusion/provenance behavior is untouched, guarded by the existing suites (FR-012)
      verify: `uv run --extra dev pytest tests/test_semantic_unavailable.py tests/test_semantic_events.py -q` (12 + 10 baseline — survey S12) — pits: do not cross-wire the new key onto compass results (`src/cairn/mcp_server/tools_compass.py:166` reads its own `result.get("degraded")`)
- [ ] T014 [P] (after T011) Add `--adopt-server-model` to the `embed` command options block (`src/cairn/cli/embed.py:10-42`; no such flag exists — survey S13): validates a parity-verified server candidate from the T011 adoption state and executes the D-009 permanence act — persist the ALIAS BINDING (spec FR-012/A2.7: the stored corpus KEEPS its stamp while embeds and queries run through the adopted model, FR-005 parity re-verified once per process; NOT a corpus restamp) so the ladder stops session-falling (durable alias storage lands with the FR-010 substrate in Phase 4; until then the flag's effect equals pinning `CAIRN_EMBED_MODEL_STAMP` to the stored stamp for the produced corpus); CliRunner tests (FR-012)
- [ ] T015 [P] (after T012) Slot `_check_embed_server` into `_run_doctor`'s return list (`src/cairn/cli/system.py:1231-1266`) using the `_result` shape (`system.py:740-742`) and PASS/WARN/FAIL constants (`system.py:727-729`): probe, model-listing, parity-sample (reusing the T008 sampler), latency; self-short-circuits to a single informational PASS line when the configured family is not `server` so default-configured doctor output stays byte-stable and the 27-test suite passes unchanged (D-012); an active degradation surfaces as a doctor entry via the T012 accessor; probe failures FAIL with remediation hint, parity failures WARN (advice); exit semantics untouched (FR-007, FR-013)
      verify: `uv run --extra dev pytest tests/test_doctor.py tests/test_semantic_events.py -q`
- [ ] T016 [P] (after T012) Append the degradation footnote to MCP tool results, modeled on the `unembedded_memory_hint` append pattern (`src/cairn/graph/embeddings.py:1478` produced, consumed at `src/cairn/mcp_server/tools_memory.py:52,164` — session grep in tech-spec): footnote names the active rung, reason, and the remediation command; renders nothing when no degradation is active (FR-013)
- [ ] T017 [P] (after T012) Render the degradation banner on the dashboard: thread the T012 banner accessor into handler context in `src/cairn/dashboard/app.py` and add the banner markup to the affected templates under `src/cairn/dashboard/templates/` — strictly read-only changes (the app's first POSTs deliberately wait for Phase 4); pages for non-server, non-degraded states render byte-identically; coverage follows the `tests/test_dashboard_app.py` tradition (69 baseline — survey S09/S12) (FR-013)

## Phase 4: Config substrate + dashboard + docs (FR-008, FR-010, FR-011)
<!-- Checkpoint (plan.md): from a clean shell, dashboard-saved values survive restart in
     ~/.cairn/config.json; re-exporting an env var overrides the file; a base-URL save
     without the confirm step is refused; the API key is never rendered back; the Status
     view shows backend, stamp, counts, probe health, rung.
     Targeted verify: uv run --extra dev pytest tests/test_dashboard_app.py -q -->
<!-- plan.md order: FR-010 (T018) precedes FR-011 (T019/T020) — Settings has nowhere to
     persist without the substrate — and FR-008 docs (T022/T023) land last by design.
     [G3] after the substrate: dashboard work (T019→T020, one arm because they share
     app.py + templates/), docs prose (T022), install_hint (T023) are disjoint arms. -->

- [ ] T018 Build the persistent config substrate: `CONFIG_FILE = CAIRN_HOME / "config.json"` beside `REGISTRY_FILE` plus loader in `src/cairn/paths.py` (structural precedent `_load_registry` with try/except JSONDecodeError/OSError returning empty at `paths.py:120-128`; corrupt JSON degrades to defaults-with-warning, never raises into embed calls); one env > file > default resolver choke point (D-008) consulted by `_backend_name`, `current_model`, and preset lookups at the Phase-1 read sites in `src/cairn/graph/embeddings.py` (wiring point per survey S01); mtime-triggered re-read so running processes pick up changes without restart; caches cleared by `reset_backend_cache()`; tests monkeypatch the module-level path variable, not the env var after import (CAIRN_HOME import-time binding pitfall, `paths.py:29-31` — survey S10) (FR-010)
      verify before implementing: `rg -rn 'config.json' src/cairn/paths.py` (expected zero today — survey S10)
- [ ] T019 (after T018) Add the dashboard Settings section — the app's first-ever POST routes: append `Route("/settings", ..., methods=["POST"])` to the 17-entry route list (`src/cairn/dashboard/app.py:583-606`; zero POSTs today — survey S09) and add NEW `src/cairn/dashboard/templates/settings.html` (12 templates exist, zero 'setting' hits — survey S09); fields: backend choice, server model id, API key write-only (store, never render back, masked placeholder), timeout/batch, migration alias, confirm-required base-URL change (refuse the write without the explicit confirm step), and a Run-parity-check action invoking the T008 sampler; loopback enforced via the existing `DEFAULT_HOST = "127.0.0.1"` + `_require_loopback` (`src/cairn/cli/dashboard.py:20-31`); keep new handlers off existing GET routes so the 69-test suite's read-only assumptions hold; extend `tests/test_dashboard_app.py` (FR-011)
- [ ] T020 (after T019) Add the Embeddings status view: `Route("/embeddings")` plus NEW `src/cairn/dashboard/templates/embeddings.html` rendering effective backend, resolved stamp, per-corpus row counts, last-embedded time, probe health, and the active fallback rung as a banner — fed by the SAME degradation accessor as the Phase-3 banner (one source, no double build, plan.md deviation note); show each setting's effective value AND whether an env var overrides the file (precedence transparency per D-008); extend `tests/test_dashboard_app.py` (FR-011)
- [ ] T021 (after T018) Teach `_check_config` (`src/cairn/cli/system.py:1191-1206`, echoes only env vars today — survey S08) the file layer: echo effective embedding-config values marking which are env-pinned, file-set, or defaulted, so doctor output cannot diverge from dashboard truth; default-configured output stays stable (FR-010, FR-011)
- [ ] T022 [P] (after T018) Document the server backend in `docs/configuration.md` and `docs/retrieval.md` (both mention only local/hash/openai — survey S11): all seven A2.1 env vars, the three server values + preset URLs, probe semantics, the fallback ladder and its notifications, the oMLX safetensors conversion walkthrough (spec appendix C), the privacy note (embedding input is code text sent to the configured URL; localhost default, remote is an explicit user choice — spec A2.6), and the 'run doctor after re-pulling models' rule stated in retrieval.md (Area-8 pitfall: docs drive agent behavior too) (FR-008)
      verify before implementing: `grep -n 'server\|omlx\|ollama' docs/configuration.md docs/retrieval.md` (expected zero hits — survey S11)
- [ ] T023 [P] (after T018) Update `install_hint()` text (`src/cairn/graph/embeddings.py:86-92`, currently only the `[semantic]` extra + hash smoke test — survey S11) to name the no-torch server path: set `CAIRN_EMBED_BACKEND` to `omlx`/`ollama`/`server` with a reachable `/v1` machine, no sentence-transformers install required; doc-string-only diff, the lone Phase-4 touch to `embeddings.py`, scheduled clear of the serial spine (FR-008)

## Conventions
- `- [ ]` todo · `(in-progress)` claimed (noted in the task line) · `- [x]` done + proof note:
      `done DATE — command/output that proves it`
- Dropped: `- [ ] ~~T0NN~~ dropped DATE (D-NNN)` — never delete the line;
  dropped tasks stay visible with the decision that killed them (none so far)
- `[P]` = member of a plan.md parallel group (no shared files with sibling arms);
  a group member awaiting one shared predecessor carries both markers, e.g.
  `[P] (after T011)` — it runs concurrently with its siblings once that
  predecessor lands; `(after T###)` otherwise orders tasks that share files
  or consume another's output; serial-spine stages (plan.md) are single-owner
- Every task cites its FR-###; tasks with no FR are scope creep — fix the spec first
- Tests ship in the same commit as their code; full-suite gate at each checkpoint:
  `uv run --extra dev pytest -q`

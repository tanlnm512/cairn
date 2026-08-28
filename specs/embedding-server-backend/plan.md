# Plan: embedding-server-backend

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-27

Basis: code state comes only from [survey.md](survey.md) (baseline `51a56d8`). Where this
plan goes beyond what survey evidence pins, the assumption is marked **[assumption]**.

## Rollout vs annex A5 (validation)

The spec's A5 two-phase split is directionally confirmed: backend+ladder before
config+dashboard, default-off throughout, no schema migration (stamps are strings in
existing columns — survey S03). Two refinements, made against survey evidence:

1. **A5 Phase 1 (9 FRs) is split into three shippable milestones (Phases 1–3 below)** — the
   parity gate (FR-005) is the risky piece and lands isolated right after the backend core;
   a solo developer on a PR-per-feature cadence gets three reviewable landings instead of
   one 9-FR mega-PR. Each is demoable at its own checkpoint.
2. **FR-013 stays whole in Phase 3**, including the dashboard banner. A5 deferred the banner
   to its Phase 2; splitting one FR across phases breaks the one-FR-one-milestone rule and
   leaves US3 AC3 incomplete until the end. Survey S09 shows the dashboard is a safe home for
   a read-only degradation banner today (17 routes, all GET, loopback-guarded). Phase 4's
   Status view (FR-011) then presents the *same* degradation-state accessor in its richer
   form — one source, no double build. **[deviation from A5]**

## Milestones
<!-- Each milestone = a phase in task.md. -->
| Phase | Milestone | Delivers (demoable) | FRs | Depends on |
|-------|-----------|---------------------|-----|------------|
| 1     | Server backend core (env-configured) | With `CAIRN_EMBED_BACKEND=omlx\|ollama\|server` (+base URL for bare `server`) a reachable `/v1` machine embeds all three corpora; rows carry derived `server/{netloc}/{model}` stamps that flow unmodified through existing vec0 table naming (`re.sub` per survey S03, e.g. `vec_server_127_0_0_1_8000_bge_m3`); transient errors retried 3× with backoff, other 4xx fail with the server message verbatim; the probe (200 AND model listed, never resolving to hash) gates availability; boot warmup fires one tiny probe for server backends; `cairn embed` against a down server exits 1 with remediation hint; `local`/`hash`/`openai` byte-for-byte unchanged (`_embed_openai` untouched per S02). | FR-001, FR-002, FR-003, FR-004, FR-006, FR-009 | — |
| 2     | Migration alias + parity gate | Corpus built under the local stamp migrates to a server serving the same weights via `CAIRN_EMBED_MODEL_STAMP=<old stamp>`: parity check (≤16 sampled stored chunks, mean cosine ≥ 0.98, dim match) runs BEFORE any write; pass ⇒ zero rows re-embedded and search results correct; fail ⇒ hard abort quoting the measured value. | FR-005 | Phase 1 |
| 3     | Fallback ladder + loud degradation | A down server or missing model never crashes `semantic_search` (today `embed_query` at semantic.py:769 propagates uncaught — survey S04): rung 1 adopts a parity-verified same-server candidate session-scoped with the permanence command named; rung 2 falls back to a parity-verified local model; rung 3 serves BM25/FTS5 hybrid with `provenance="bm25"`, a `degraded` tag on results (field does not exist yet per S04), and a remediation hint. Every degradation emits all five notifications: warn-once line, one `EMBED_SERVER_DEGRADED` per reason enum, MCP footnote, doctor entry, dashboard banner. Includes `cairn embed --adopt-server-model` (does not exist per S13) and doctor's probe/model-listing/parity-sample/latency checks (none exist per S08). | FR-007, FR-012, FR-013 | Phase 1, Phase 2 |
| 4     | Config substrate + dashboard + docs | `~/.cairn/config.json` persists settings (env > file > default, mtime re-read, invalidated by `reset_backend_cache()`; nothing like it exists per S10); dashboard Settings section (first POST routes in the app — zero today per S09; loopback-only, write-only API key, confirm-required base-URL change, live parity action) and Embeddings status view (effective backend, resolved stamp, per-corpus counts, probe health, active rung); docs/configuration.md, docs/retrieval.md, and `install_hint()` describe the server path incl. oMLX safetensors conversion and privacy notes (only local/hash/openai documented today per S11). | FR-008, FR-010, FR-011 | Phase 1–3 |

Every FR-001..FR-013 appears in exactly one milestone. Shared-infrastructure facts, not
work items: the MCP embed flusher needs no changes (its infinite retry already tolerates
server outages — survey S06), and `purge_stale_models` stays dead code — model-swap
invalidation rides stamp staleness as today (survey S03; see Risks).

## Dependencies

```
Phase 1 (core) ──> Phase 2 (parity) ──┐
       │                              ├──> Phase 3 (ladder + notifications)
       └──────────────────────────────┘
Phase 1-3 ──> Phase 4: FR-010 (config) -> FR-011 (Settings UI) -> FR-008 (docs, last)
```

- **Phase 2 after 1**: the parity check embeds samples through the Phase-1 server client and
  compares against stored stamps created by Phase-1 stamping (FR-005 consumes FR-001/003/004).
- **Phase 3 after 1+2**: every ladder rung needs the probe (FR-002) and reuses the FR-005
  parity gate for adoption; session-scoped adoption is the alias mechanism Phase 2 built;
  `--adopt-server-model` permanence writes the alias config (brief: FR-004 stamps before
  FR-005 alias gate; FR-002 probe before FR-012 ladder — confirmed correct orderings).
- **Phase 4 after 1–3**: Settings saves must have resolution code to influence (wiring point =
  the Phase-1 env read sites in embeddings.py, survey S01); the Status view renders probe
  health and active rung produced by Phase 3; docs (FR-008) intentionally land last so they
  describe the complete surface once, not twice. Inside Phase 4, FR-010 strictly precedes
  FR-011: the Settings POST route has nowhere to persist without the substrate (also the
  brief's stated ordering). The banner relocation/expansion into the Status view rides FR-011.
- **Deliberate non-dependency**: memory-concept embeds during server outages already survive
  via the flusher retry loop (S06); scheduling any work "because the flusher needs adapting"
  would be wrong.

## Parallelization map
<!-- Which work areas are independent (different files/subsystems, no shared
     state) and can be developed concurrently, and which are strictly
     sequential. The task-breaker turns this into [P] markers per task. -->

Contention reality: nearly every FR threads through `src/cairn/graph/embeddings.py` (dispatch,
probe, stamps, alias — survey S01-S02, supporting-evidence consumer inventory), so everything
outside that file is parallelizable and inside it is the serial spine.

- Independent: **[G1, within Phase 3]** telemetry catalog (`src/cairn/telemetry/events.py` +
  `__init__.py`: add `EMBED_SERVER_DEGRADED` + reason frozenset, HASH_FALLBACK pattern per
  S07) ∥ **doctor checks** (`src/cairn/cli/system.py`: extend `_run_doctor`'s 8-check list,
  `_check_embeddings` shape per S08) ∥ **dense-leg guard + `degraded` result field**
  (`src/cairn/graph/semantic.py`: wrap embed_query call sites ~769/~1012, S04) ∥ **CLI surface**
  (`src/cairn/cli/embed.py`: `--adopt-server-model` + server-down exit path per S13) — four
  disjoint files; the only shared artifact is the Phase-3-kickoff agreement on symbol names
  (state accessor signature, reason enum literals, tag value `"embedding-backend"`).
- Independent: **[G2, during Phase 2]** warmup step (`src/cairn/graph/model_warmup.py`: extend
  `_warm_embedder`'s `!= "local"` gate + tiny-probe step, guards reusable per S05) ∥ parity-gate
  work occupying embeddings.py — disjoint files; requires only that the Phase-1 skeleton pinned
  the `"server"` backend value.
- Independent: **[G3, within Phase 4 after substrate]** dashboard routes/templates
  (`src/cairn/dashboard/app.py` + `src/cairn/dashboard/templates/*`: first POSTs, settings +
  status views, S09) ∥ docs prose (`docs/configuration.md`, `docs/retrieval.md`) ∥ `install_hint()`
  text edit (embeddings.py:86-92 — the one embeddings.py touch in G3; scheduled alone so the
  serial spine is idle).
- Pinning rule enabling the groups: anything outside embeddings.py codes against the name
  strings the Phase-1 skeleton fixes (backend values, stamp format, event name, degraded tag),
  mirroring how the survey shows tests already pin behavior (S12).
- Strictly ordered (serial spine, one owner at a time): **embeddings.py dispatch+skeleton**
  (backend names, probe, stamp derivation, batched/retrying `_embed_server` client) →
  **parity check** (consumes client + stored stamps) → **degradation-state accessor**
  (single source of "current rung + reason", cached per process, resettable via
  `reset_backend_cache()`) → **config-substrate wiring** (file layer consulted under env at the
  Phase-1 read sites) → **Settings UI** (writes what the substrate reads). Justification: each
  stage produces the symbols/state the next consumes; parallelizing any adjacent pair means
  concurrent edits to embeddings.py or divergent re-derivations of degradation state in doctor,
  banner, and MCP footnote.
- Exception worth naming: the Phase-3 banner is delivered *before* the Phase-4 settings/view
  work on the same dashboard, so dashboard-area work itself is sequential across those phases —
  acceptable because they are different milestones; within either milestone, dashboard files
  appear in at most one parallel arm.

## Checkpoints
<!-- Exit condition per phase; verify before starting the next. -->
Full-suite command everywhere: `uv run --extra dev pytest -q`.

- **After Phase 1**: Given a live local server whose `/v1/models` lists the configured id
  (oMLX/Ollama setup per spec appendix C), `cairn embed` exits 0 and the workspace db shows
  `SELECT DISTINCT model FROM embeddings` containing `server/<netloc>/<model>`; with the server
  stopped, `cairn embed` exits 1 printing a remediation hint. Existing-backend safety:
  `uv run --extra dev pytest tests/test_embedding_backend_quality.py tests/test_model_warmup.py -q`.
- **After Phase 2**: Corpus embedded under `BAAI/bge-m3` locally, then switched to oMLX with
  `CAIRN_EMBED_MODEL_STAMP=BAAI/bge-m3`: `cairn embed` exits 0 with zero vectors re-computed
  (all rows satisfied under the kept stamp) and `semantic_search` still returns expected hits;
  aimed at a server serving a *different* model it hard-aborts nonzero quoting a measured mean
  cosine below 0.98.
- **After Phase 3**: Server killed mid-life: `semantic_search` raises nothing and returns usable
  results — BM25-provenanced where the dense leg died, carrying the `degraded` tag and hint;
  exactly one warn line per reason in logs, `cairn doctor` output includes the new
  embedding-server checks (and an entry for the active degradation), MCP results carry the
  footnote, the dashboard shows the degradation banner; restoring the server and running the
  adopt flow makes the chosen model permanent. Targeted verifies:
  `uv run --extra dev pytest tests/test_doctor.py tests/test_semantic_events.py tests/test_semantic_unavailable.py -q`.
- **After Phase 4**: From a clean shell (no exports), values saved via dashboard Settings
  survive a process restart (`~/.cairn/config.json` holds them); re-exporting an env var
  overrides the file; a base-URL save without the confirm step is refused; the API key is never
  rendered back; the Status view shows backend, stamp, counts, probe health, rung. Targeted
  verify: `uv run --extra dev pytest tests/test_dashboard_app.py -q`.

## Risks & mitigations
- Risk: server-side model swap under a stable id mixes vector spaces silently → parity gate on
  every adoption (rungs 1–2, alias first-use), doctor parity sample, documented re-pull rule;
  residual accepted (spec D-009).
- Risk: first-ever POST routes on a 17-route GET-only dashboard (S09) → loopback guard reused
  from `src/cairn/cli/dashboard.py`, confirm-required base-URL change, write-only API key;
  dashboard test suite extended in the `test_dashboard_app.py` tradition (69 baseline tests).
- Risk: wrapping the previously-uncaught hot path in `semantic.py` (embed_query at :769, S04)
  regresses fusion/provenance behavior → narrow catch at the dense leg only; existing suites
  guard it: `test_semantic_unavailable.py` (12), `test_fusion.py`, provenance tests (S04/S12).
- Risk: someone builds on `purge_stale_models`, which survey proves is dead code (defined,
  zero call sites, S03) → plan stance: rely on existing stamp-staleness re-embed flow; any task
  that finds it needs reviving reports the gap instead of silently changing model-swap semantics.
- Risk: ladder evaluation cost / surprise session fallbacks (spec A4) → once-per-process cached
  evaluation behind the probe cache (`reset_backend_cache()` forces re-eval), and every rung
  notifies via FR-013's five surfaces.
- Risk: solo-dev overload from over-broad parallelism → only genuinely file-disjoint arms are
  marked parallel ([G1]-[G3]); the five-step serial spine stays single-owner.

### Plan assumptions (no survey evidence — verify at task/tech stages)
- Rung-1 candidate detection: A2.7 says scan `/v1/models` for "embedding-capable" candidates;
  how capability is detected is unspecified — the plan assumes model-list parsing only. **[assumption]**
- Phase-3 banner: a read-only degradation indicator servable by the existing GET-only dashboard
  (placement/template owned by tech-spec). **[assumption]**
- Degradation state is process-scoped like the probe cache (lost on restart, re-derived on next
  failure); whether any persistence is wanted is undecided. **[assumption — unknown — verify]**
- New-test volume/locations are task-breaker decisions; survey test counts (S12) are baselines,
  not targets. **[assumption]**

## Delivery
One PR per milestone (branch per milestone, e.g. `feat/embed-server-core`), rebased/landed via
the repo's PR-only gate on main: conventional commit + conventional PR title, pre-commit
`run --all-files` before every push, CI green (pytest, mypy, bandit, pip-audit, PR-title) before
merge; within a PR, one commit per task with code + its tests together (docs edits ship in the
milestone that owns them: FR-008 material in Phase 4). Default-off (`CAIRN_EMBED_BACKEND=local`
unchanged) keeps every merge zero-behavior-change for existing users (SC-4), so no milestone
waits for its successor to ship safely. Post-merge per repo procedure: `cairn update` +
`record_memory`.

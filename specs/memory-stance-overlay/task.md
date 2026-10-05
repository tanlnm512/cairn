# Tasks: memory-stance-overlay

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
**Lifecycle**: v2
Status reflects code state per [survey.md](survey.md), not intent.
**Delivered**: commit @ fb89eae

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
| Phase | Total | Done |
|-------|-------|------|
| 1 | 2 | 2 |
| 2 | 2 | 2 |
| 3 | 3 | 3 |
| **Σ** | 7 | 7 |

## Phase 1: Stance substrate (FR-001)
<!-- Checkpoint: recorded stance + baseline persist in file frontmatter (plan.md After Phase 1). -->
- [x] T001 (implemented) Thread a validated record-time stance prior and seed the refs baseline through the capture chokepoint — failing test first (C-02), then an optional stance kwarg on `create_memory` in src/cairn/memory/store.py and `capture_memory` in src/cairn/memory/promotion.py, a stance parameter on the MCP record_memory tool in src/cairn/mcp_server/tools_memory.py, and the matching documented default in src/cairn/agent_integration/skill/references/tools.md (exact-traffic pin: test_agent_surface.py test_tools_md_default_args_match_live_signatures). Writes frontmatter keys memory_stance, memory_stance_peer, memory_refs_baseline (tech-spec D-003); zero-ref bodies get no baseline (D-004). Verify before implementing: `grep -n "stance" src/cairn/memory/ src/cairn/mcp_server/tools_memory.py` (survey S2: no match today). (FR-001)
  - done 2026-10-05 — done 2026-10-05 — 10 prior/baseline tests green; agent_surface signature parity incl. stance=None; 225 memory-suite passed
  - Touches:
    - `src/cairn/memory/store.py`
    - `src/cairn/memory/promotion.py`
    - `src/cairn/mcp_server/tools_memory.py`
    - `src/cairn/agent_integration/skill/references/tools.md`
    - `tests/test_memory_stance.py`
- [x] T002 (implemented) Add the --stance flag (preferred|tentative|contested) to `cairn memory record` in src/cairn/cli/memory.py, passing the prior through to capture_memory (survey S3) — (after T001; consumes capture_memory's optional keyword `stance=None` (str choice, None = unset) that T001 lands) (FR-001)
  - done 2026-10-05 — done 2026-10-05 — --stance Choice flag; live record+search renders stance=tentative
  - Touches:
    - `src/cairn/cli/memory.py`

## Phase 2: Reflect engine (FR-002, FR-003, FR-005, FR-006, NFR-003, NFR-004)
<!-- Checkpoint: reflect twice on the fixture store is a byte-identical no-op (plan.md After Phase 2). -->
- [x] T003 (implemented) [P] Implement the deterministic stance verdict module under src/cairn/memory/ beside promotion.py — contested iff a memory_superseded_by edge exists AND the pair shares a verified symbol ref (D-002; fixture: test_memory_lifecycle.py pins the edge as frontmatter), tentative when the live `_graph_verification` fraction (survey S4) drops below memory_refs_baseline (FR-005), preferred at fraction 1.0 with refs present, precedence contested > tentative > preferred > prior (D-004), baseline refreshed as max(baseline, current) (D-006), memories processed in sorted concept-id order, files written only when a stance key changes, never memory_tier/memory_score/body (survey S9, D-003) — with fixture tests covering US1 AC1, US1 AC2 (idempotent double run), US3 AC1. Consumes T001's extension keys memory_stance / memory_stance_peer / memory_refs_baseline. (FR-002)
  - done 2026-10-05 — done 2026-10-05 — 9 verdict tests green (contested w/ peer, idempotent, baseline-drop tentative, precedence, never-tier/score/body)
  - Touches:
    - `src/cairn/memory/stance.py`
    - `tests/test_memory_stance.py`
- [x] T004 (implemented) Wire the `cairn memory reflect` CLI verb in src/cairn/cli/memory.py — load bundle + graph conn, one pass over list_memories in sorted order via the T003 verdict entry point `reflect_store(bundle, conn) -> dict` (changed/unchanged/contested counts), write changed files via bundle.write_concept under bundle.lock() (per-file atomic, NFR-004), print the summary; verify the 30s wall budget on the primary store (NFR-003) — (after T003; consumes reflect_store's signature and summary dict named here) (FR-006)
  - done 2026-10-05 — done 2026-10-05 — reflect verb live on real store: 0.57s/128 memories, idempotent 2nd run
  - Touches:
    - `src/cairn/cli/memory.py`
    - `tests/test_memory_stance.py`

## Phase 3: Surfacing + docs (FR-004)
<!-- Checkpoint: contested memory renders stance + peer inline in recall, explore, and CLI lines (plan.md After Phase 3). -->
- [x] T005 (implemented) [P] Render stance inline in the recall_memory loop in src/cairn/mcp_server/tools_memory.py (append after the refs segment; contested gets a peer hint line mirroring the existing STALE hint shape) and in explore's tribal section in src/cairn/mcp_server/tools_graph.py (append to the existing title line only — the exact-count pins on the `=== Tribal memory` headers and body lines at tests/test_explore_memory.py :102/:118/:142/:173/:209 must hold); extend the refs-verified render pins in tests/test_memory_stale_flag.py rather than duplicating them (FR-004)
  - done 2026-10-05 — done 2026-10-05 — recall/explore stance rendering; explore exact-count pins hold; 37 passed render suites
  - Touches:
    - `src/cairn/mcp_server/tools_memory.py`
    - `src/cairn/mcp_server/tools_graph.py`
    - `tests/test_memory_stale_flag.py`
    - `tests/test_explore_memory.py`
- [x] T006 (implemented) Append the stance segment (and contested peer) to `_memory_line` in src/cairn/cli/memory.py so search/list/digest all render it through the one helper (survey S8; three call sites: memory_search :191, memory_list :362, memory_digest :407), keeping the pinned `refs-verified=` substrings intact — (after T004; shares src/cairn/cli/memory.py with the reflect verb T004 lands) (FR-004)
  - done 2026-10-05 — done 2026-10-05 — _memory_line stance+peer segment; refs-verified= pins intact; 29 passed CLI suites
  - Touches:
    - `src/cairn/cli/memory.py`
- [x] T007 (implemented) [P] Document `cairn memory reflect` and the record `--stance` flag in the Memory section of docs/cli-reference.md (FR-004)
  - done 2026-10-05 — done 2026-10-05 — cli-reference Memory stance section + CHANGELOG; doc-links 0 broken
  - Touches:
    - `docs/cli-reference.md`

## Conventions
- `- [ ]` todo · `(in-progress)` claimed · `(implemented)` landed —
      implementation evidence durable before the one all-at-once tick ·
      `- [x]` done + proof note: `done <date> — <test/command that proves it>`
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
- Every task cites FR-### or an applicable NFR-###; a task with neither is
  scope creep — fix the spec first
- Every code task carries a `Touches:` block (repo-relative files,
  directories, or globs); `[P]` tasks whose touches overlap are chained, not
  spawned together

# Plan: tribal-memory-loop-closure

**Spec**: [spec.md](spec.md) | **Created**: 2026-09-04
**Baseline**: 76639899dff50062a483a13238b5e3ab2d91dddf (survey.md's HEAD)

## Milestones
<!-- Each milestone = a phase in task.md. -->
| Phase | Milestone | Delivers (demoable) | FRs (US) | Depends on |
|-------|-----------|---------------------|----------|------------|
| 1 | Explore memory integration | `explore(query)` returns a `=== Tribal memory ===` section (≤3 entries, title + "How to apply:" line, `(none)` when empty) and each surfaced memory records a `memory_refs` row via a real `session_id` | FR-001, FR-002, FR-003 (US1, P1) | — |
| 2 | Scoring formula rebalance | `WEIGHTS` in `scoring.py` drops `critic_score`/`authority`, remaining 5 terms renormalize to sum 1.0; synthetic-signal unit test shows wider score spread than today's two-cluster distribution | FR-005 (US3, P1) | — |
| 3 | Hook lifecycle: session-start + auto-capture correctness | `post_tool_failure` captures only on the 2nd occurrence of a `(tool_name, normalized_error)` signature and is wired into `_HOOK_ENTRYPOINTS`/client configs; `capture_memory` force-routes session-bookkeeping-shaped titles/bodies to `raw` tier; `session_end` reads `transcript_path`, parses the JSONL transcript, and queues a `memory-extract` task on non-empty transcripts; a new `session_start` hook emits top score-ranked tribal memories once per session (or nothing when the store is empty) | FR-004 (US2, P2), FR-006, FR-007, FR-008, FR-009 (US4, P1) | — (grouped by shared-file locality in `claude_hooks.py`/`_common.py`, not by data dependency) |
| 4 | Observability: L4 eval + doctor check | `eval.py` accepts `"L4"` in `VALID_LEVELS` with `evaluate_l4_query`/`_retrieve_l4` reporting recall@k/MRR for `search_memory` against a ground-truth situation→memory dataset; `cairn doctor` gains a check that WARNs when a workspace has ≥1 tribal memory older than 30 days with zero `memory_refs` in that window | FR-010, FR-011 (US6, P2) | — |
| 5 | MCP surface trim + docs | `tools_memory.py` no longer registers `memory_promote`/`memory_demote`/`memory_evolve`/`memory_decay`/`memory_delete`/`memory_digest` as `@mcp.tool` (CLI subcommands — note `memory_delete` → `cairn memory forget`, not `delete` — keep working, unchanged); README.md/docs/mcp-tools.md counts and tables updated from 28→22 tools; README's "recalled alongside graph results" claim re-verified against Phase 1's `explore` change (US5) | FR-012, FR-013 (US7, P2; US5, P3 — validation only, no FR of its own) | Phase 1 (content-only: FR-013's doc text needs Phase 1 landed to state the claim correctly; FR-012's code removal itself has no dependency) |

Out of scope for all phases (per spec.md Scope): no backfill/re-scoring task
for the 34 existing production memories — this plan only sequences
going-forward code paths.

## Dependencies

```
Phase 1 (explore+refs)  ─┐
Phase 2 (scoring)        ├─ mutually independent, no shared files → run concurrently
Phase 3 (hooks)          │   (internally chained — see below)
Phase 4 (eval+doctor)   ─┘
                            Phase 5 FR-012 (registration removal) ── independent, joins the concurrent set
                            Phase 5 FR-013 (doc text)  ── soft dependency on Phase 1 landing
                                                          (needs the real explore-recall claim to write correctly);
                                                          also cleanest to write last so tool-count table is final.
```

No phase's code depends on another phase's *output* (no shared data
contracts, no phase produces a value another consumes) — the only coupling
in this feature is **file-level**, confined inside Phase 3, and the
**content-level** one at Phase 5/FR-013 noted above. There is no hard
build-order requirement between Phases 1, 2, 3, 4 — they may be worked in
any order or concurrently.

Internal chain inside Phase 3 (all touch `src/cairn/hooks/claude_hooks.py`
and/or `src/cairn/agent_install/_common.py` — see parallelization map):
`FR-006 (recurrence gate logic in post_tool_failure)` → `FR-007 (register
post_tool_failure in _HOOK_ENTRYPOINTS)` — trivial once the gate exists, same
phase, sequenced second. `FR-004 (new session_start function + its own
_HOOK_ENTRYPOINTS/client-wiring entry)` and `FR-009 (fix session_end's
transcript_path read)` touch the same two files in different functions —
no data dependency on FR-006/007, but same-file edits are serialized by the
**ownership rule** (one task owns a file at a time), not by logic. Order
within the phase (any order works logically; recommended for lowest churn):
FR-006 → FR-007 → FR-009 → FR-004 (riskiest/most-coupled first, per
spec's own risk note on FR-006's lookup cost).

## Parallelization map
<!-- Which work areas are independent (different files/subsystems, no shared
     state) and can be developed concurrently, and which are strictly
     sequential. The task-breaker turns this into [P] markers per task. -->

**File touch list per FR** (from survey.md citations):
| FR | File(s) | Function(s) |
|----|---------|-------------|
| FR-001/002/003 | `src/cairn/mcp_server/tools_graph.py` (primary); `src/cairn/graph/explore.py` if seed-symbol names aren't already returned by `queries.explore()` — **unknown, tech-spec to confirm** | `explore()` MCP wrapper, section-render block |
| FR-004 | `src/cairn/hooks/claude_hooks.py` (new fn); `src/cairn/agent_install/_common.py` (`_HOOK_ENTRYPOINTS`); `src/cairn/agent_install/clients/claude.py` (+ `cursor.py` if in scope) | new `session_start()`; entrypoint dict; hook-wiring block |
| FR-005 | `src/cairn/memory/scoring.py`; `tests/test_memory_lifecycle.py` | `WEIGHTS`, `score_memory` |
| FR-006 | `src/cairn/hooks/claude_hooks.py`; new `src/cairn/memory/recurrence.py` + a `memory_failure_signatures` table in `src/cairn/graph/schema.py` (resolved in tech-spec.md D-005: dedicated SQLite table, evaluated inside the detached `cairn memory record` subprocess, not in the hook) | `post_tool_failure()` |
| FR-007 | `src/cairn/agent_install/_common.py` | `_HOOK_ENTRYPOINTS` |
| FR-008 | `src/cairn/memory/promotion.py` | `capture_memory()` (survey: lines 20-96) |
| FR-009 | `src/cairn/hooks/claude_hooks.py` | `session_end()` (survey: lines 85-104) |
| FR-010 | `src/cairn/eval.py` | `VALID_LEVELS`, new `evaluate_l4_query`/`_retrieve_l4` |
| FR-011 | `src/cairn/cli/system.py` | new `_check_<name>` (pattern at lines 743-1295) |
| FR-012 | `src/cairn/mcp_server/tools_memory.py` | 6 `@mcp.tool` decorator removals |
| FR-013 | `README.md`, `docs/mcp-tools.md` | prose/table only |

**Independent (assumed concurrent — disjoint files, no shared state):**
- Phase 1 (`tools_graph.py`, maybe `explore.py`) ∥ Phase 2 (`scoring.py`) ∥
  Phase 4 (`eval.py`, `cli/system.py`) ∥ FR-012 (`tools_memory.py`) — four
  completely disjoint file sets, zero shared symbols. Verified: no file name
  in this group repeats in any other row above.
- Within Phase 3, FR-008 (`promotion.py::capture_memory`, lines 20-96) ∥
  FR-005 (`promotion.py` lines 436, 685, inside `batch_critic` /
  `_rescore_with_critic`, Phase 2) — **same file, disjoint line ranges,
  confirmed by survey.md**: FR-005's writes are two isolated
  `signals["critic_score"] = ...` lines far outside `capture_memory`'s
  20-96 span. Safe to run as parallel tasks; flag for a quick rebase check
  at merge time since both land in `promotion.py` (same file, not same
  hunk).

**Strictly ordered (same file, must chain — ownership rule, not logic):**
- `src/cairn/hooks/claude_hooks.py` and `src/cairn/agent_install/_common.py`
  are each touched by **three** FRs (FR-004, FR-006/007, FR-009). Even
  though the touched functions are disjoint (`session_start` is new,
  `post_tool_failure` and `session_end` are pre-existing, separate
  functions — confirmed by survey.md line ranges 85-104 vs 106-171), one
  task must own each file at a time per the shared-protocol ownership rule.
  Chain: FR-006 → FR-007 (FR-007 is a one-line registration that is trivial
  *once* FR-006's gate exists, so it is cheap to sequence immediately after)
  → FR-009 → FR-004, all inside Phase 3, each a separate commit against the
  same two files.
- FR-013 (doc prose) is content-ordered after Phase 1 lands (the "recalled
  alongside graph results" claim in README can only be written accurately
  once `explore` actually does it) and is cleanest written after FR-012's
  registration removal too (so the 22-tool table is counted once, correctly,
  rather than drafted and re-edited). This is not a file lock (no other FR
  touches README.md/docs/mcp-tools.md) — it is a content-correctness
  ordering only, safe to defer FR-013 to the very end of the whole plan
  regardless of how the other phases are sequenced.

## Checkpoints
<!-- Exit condition per phase; verify before starting the next. -->
Each checkpoint's tasks must follow **C-02 (test-first)**: a failing test
task precedes its implementation task in task.md for every FR below — the
task-breaker should never sequence an implementation task before its own
failing-test task.

- **After Phase 1**: A failing test asserting `explore()`'s MCP response
  contains `=== Tribal memory ===` (populated or `(none)`) and that a
  matching memory produces a `memory_refs` row, written first and red;
  then the implementation turns it green.
  Verify: `grep -n "Tribal memory" src/cairn/mcp_server/tools_graph.py`
  (baseline: 0 matches per survey.md; expect ≥1 after) plus the new pytest
  passing.
- **After Phase 2**: A failing test pinning the new `WEIGHTS` (no
  `critic_score`/`authority` keys, sum == 1.0) and a synthetic-signals test
  asserting a wider score spread than the current two-cluster distribution,
  written first and red; then `scoring.py` changes turn them green. Existing
  pinned-value tests (`tests/test_memory_lifecycle.py:177,180-181,185-186,
  270,273` per survey.md) updated in the same task, not deferred.
  Verify: `python3 -m pytest tests/test_memory_lifecycle.py -k "weight or formula" -q`
- **After Phase 3**: Failing tests first, per FR: (a) recurrence-gate test —
  same `(tool_name, normalized_error)` signature fired twice only captures
  on the 2nd; (b) `_HOOK_ENTRYPOINTS` contains `post_tool_failure` and it's
  written to a fixture client config; (c) `capture_memory` given a
  bookkeeping-shaped title (branch ref / `T\d{3}` / dated progress count,
  per the 4 real examples in survey.md) lands in `raw` tier regardless of
  requested tier; (d) `session_end` given a `transcript_path`-shaped payload
  (JSONL fixture) queues a `memory-extract` task, per C-04 patching
  `cairn.hooks.claude_hooks.subprocess.Popen` at the call site and using an
  explicit `tmp_path` db/knowledge override, never the real `~/.cairn`; (e)
  `session_start` hook emits top-ranked memories for a populated store and
  nothing for an empty one. All five written and red before any of the
  corresponding implementation edits land.
  Verify: `grep -n "post_tool_failure" src/cairn/agent_install/_common.py`
  (baseline: 0 matches per survey.md; expect 1 after);
  `grep -rn "transcript_path" src/cairn/` (baseline: 0 matches; expect ≥1
  after).
- **After Phase 4**: Failing tests first — an L4-level eval test asserting
  `"L4"` is accepted and recall@k/MRR are computed against a ground-truth
  fixture, and a doctor test asserting WARN status for a workspace fixture
  with a stale, zero-referenced tribal memory — before the `eval.py`/
  `cli/system.py` implementation.
  Verify: `grep -n "VALID_LEVELS" src/cairn/eval.py` shows `"L4"` included;
  `grep -n "^def _check_" src/cairn/cli/system.py` shows the new check.
- **After Phase 5**: A failing test enumerating registered `@mcp.tool` names
  and asserting the 6 names are absent, before removing the six functions
  outright (tech-spec D-010: deleted, not merely un-decorated — a module
  imported only for registration side effects should not carry unreachable
  functions with live agent-facing docstrings; CLI equivalents are
  unaffected and keep passing their existing tests unchanged). D-012 adds
  one small in-scope addition to this phase: `cairn memory demote` gains a
  `--db` option so it threads a conn into `demote_memory` the same way the
  removed MCP tool did — without it, removing `memory_demote` would orphan
  `memory_embeddings` rows on every demote.
  Verify: `grep -n "memory_promote\|memory_demote\|memory_evolve\|memory_decay\|memory_delete\|memory_digest" src/cairn/mcp_server/tools_memory.py`
  shows 0 matches (functions deleted, not just undecorated);
  `grep -n "28 tools\|22 tools" README.md docs/mcp-tools.md` shows 22, not 28.
  <!-- Re-briefed 2026-09-04: tech-spec.md D-010 supersedes this checkpoint's
       original "still defined but undecorated" wording; orchestrator applied
       the correction directly (single-line, zero ambiguity) rather than a
       full planner re-spawn. -->

## Risks & mitigations
- Risk: `explore`'s `conn` closes (`tools_graph.py`) before section-rendering
  code runs today, and `record_reference`'s batched write needs a live
  `conn` (survey.md, confirmed). → Mitigation: Phase 1's tech-spec must
  sequence the `search_memory` call (and its reference write) before
  `conn.close()`, not after; this is why Phase 1 is pulled to the front —
  it's the FR with the most concretely-confirmed structural risk.
- Risk: FR-006's recurrence lookup has no existing indexed path (survey.md:
  "no SQL table/index keyed on tool_name+error was found... genuine unknown
  — verify") and must stay cheap inside a fast, detached hook subprocess.
  → Mitigation: tech-spec for Phase 3 must specify a bounded/indexed lookup
  before implementation; do not let it default to `search_memory`'s full
  lexical/semantic scan (documented as reading every memory concept from
  disk on thin hits).
- Risk: FR-008's heuristic false-positives (durable knowledge wrongly
  demoted to raw) are explicitly called out in spec.md as unacceptable,
  while false negatives are fine. → Mitigation: Phase 3's tests must use
  the durable-knowledge examples from survey.md (`never evict numpy...`,
  `kotlin grammar is the vendored...`, etc.) as negative fixtures, not just
  the bookkeeping examples as positive fixtures.
- Risk: `memory_delete`'s CLI verb is `forget`, not `delete` — a doc-writing
  mistake in Phase 5 could invent a nonexistent `cairn memory delete`
  command. → Mitigation: FR-013's task cites `forget` explicitly (already
  flagged in spec.md's Assumptions & risks).
- Risk: Parallel edits to `promotion.py` from Phase 2 and Phase 3's FR-008,
  though line-disjoint today, could still collide if either FR's tech-spec
  changes `capture_memory`'s or `batch_critic`'s line ranges before landing.
  → Mitigation: re-check disjointness with a fresh grep immediately before
  merging either task, not only at plan time.

## Delivery
One commit per task, code + its failing test together (test lands red in
the same commit sequence as the implementation that turns it green, per
C-02 — not squashed into a single commit that hides the red state). Default
branch per spec.md: `fix/tribal-memory-loop-closure`. No mid-plan migration
task for existing production memories (explicitly out of scope, per spec.md
Scope section) — do not add one even if it looks cheap during
implementation, per spec.md's own caveat that it would need to "surface"
that as a task-time discovery, not a plan-time assumption.

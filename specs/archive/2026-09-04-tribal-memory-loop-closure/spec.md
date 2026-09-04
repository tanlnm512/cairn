# Spec: tribal-memory-loop-closure

**Status**: done (2026-09-04 — 19/19 tasks, closing audit passed: scope diff adjudicated, cleanliness clean, full regression 2697 passed)
**Created**: 2026-09-04
**Branch**: `fix/tribal-memory-loop-closure`

## What
Close the tribal-memory feedback loop so agent-recorded knowledge (decisions,
patterns, mistakes, workarounds) actually reaches agents during normal work,
gets scored on signals that vary, and stops accumulating low-value entries —
instead of sitting in a store nothing reads.

## Why
Live-store inspection (34 real tribal/draft memories, 2026-08 to 2026-09)
shows the loop is open at every stage:

- `memory_refs` has **0 rows** — the only recall paths (`recall_memory`,
  `ask_compass`) are never invoked by agents in normal flow; `explore`, the
  documented "recommended first call," has no memory integration at all.
- The 7-term memory score (`src/cairn/memory/scoring.py`) is dominated by
  constants: `critic_score` (weight 0.20) is 0.5 for all 34 memories because
  the `memory-critic` task kind has never been queued; `authority` (0.10) is
  0.5 for every agent-authored memory by construction; `cross_session_refs`
  (0.20) and `reinforcement` (0.05) are 0 for all 34 because nothing records
  a reference. 60% of the score's weight carries zero real signal; 71% of
  memories land on one of two scores (0.593 / 0.585).
- `post_tool_failure` (the auto-capture hook most likely to produce
  high-value "mistake" memories) is defined but was never added to
  `_HOOK_ENTRYPOINTS` or any installed client config — it has never fired.
- `session_end` → `memory-extract` has queued **zero** tasks in the live
  store despite being the only other automatic write path.
- Roughly a third of the tribal tier is session/task bookkeeping ("T007
  pins get_repo_head display seam for T008", "landed on
  feature/arch-review-improvements") that will never be useful again but is
  indistinguishable, by score, from durable knowledge like "never evict
  numpy from sys.modules mid-process."
- The README claims memory is "recalled alongside graph results" — true
  only for `ask_compass` (titles only, top 3), false for every graph tool
  including `explore`.

Without this work, tribal memory stays a write-only journal: agents keep
writing to it (by hand or via broken hooks) but nothing ever reads it back,
so the lifecycle machinery (promote/demote/evolve/decay — 8 of cairn's 28
MCP tools) operates on a score that cannot discriminate.

## Business value
Cairn's differentiated pitch is "code-grounded memory that's actually used,
not ungrounded LLM recall." Closing the loop is what makes that literally
true instead of aspirational. Success is measured by:
- `memory_refs` accumulating rows in normal use (currently 0).
- The memory score's live spread widening beyond two clustered values.
- The tribal tier's session-bookkeeping share shrinking over time (currently
  ~1/3), not growing.
- The MCP tool count and README claims matching what agents actually call.

## User stories

### US1 — Memory surfaces without being asked (P1)
As an agent working in a repo, I want relevant tribal memory to show up when
I call `explore` on a symbol, so that I benefit from past mistakes/decisions
without having to know `recall_memory` exists or guess the right query.

**Acceptance criteria**:
- AC1: Given tribal memories exist whose title/body overlaps `explore`'s
  resolved seed symbols, When an agent calls `explore(query)`, Then the
  response includes a `=== Tribal memory ===` section listing up to 3
  matching memories (title + "How to apply" line only).
- AC2: Given no tribal memory matches, When `explore` is called, Then the
  section reads `(none)` rather than being omitted (consistent with every
  other explore section).
- AC3: Given a memory match was surfaced, When the call completes, Then a
  `memory_refs` row is recorded for that memory (the recall actually counts
  toward `cross_session_refs`).

### US2 — Memory arrives at session start (P2)
As an agent starting a new session in a repo, I want the highest-value
tribal memories surfaced automatically, so that I have prior tribal
knowledge even in sessions where I never call a cairn tool.

**Acceptance criteria**:
- AC1: Given a repo has tribal memories, When a supported client's
  session-start hook fires, Then the top memories (score-ranked, capped for
  token budget) are emitted as additional context.
- AC2: Given a repo has no tribal memories, When the hook fires, Then it
  emits nothing (no empty section, no error).

### US3 — The score actually discriminates (P1)
As a maintainer relying on memory promotion/decay to keep the tribal tier
honest, I want the score formula to depend only on signals that vary in
practice, so that promotion/demotion decisions are meaningful rather than
noise.

**Acceptance criteria**:
- AC1: Given the current formula has two terms (`critic_score`, `authority`)
  that are provably constant across the live store, When the formula is
  revised, Then those terms are removed and the remaining weights
  renormalize to sum to 1.0.
- AC2: Given `cross_session_refs` becomes live once US1/US2 ship, When the
  formula is recomputed for existing memories, Then scores show a wider
  spread than the current two-cluster distribution (verified by a unit test
  on synthetic signal inputs, not by waiting for production data).

### US4 — Auto-capture produces durable knowledge, not noise (P1)
As a maintainer, I want automatic memory capture to (a) actually run and
(b) avoid recording things that will never be useful again, so that the
tribal tier's signal-to-noise ratio doesn't degrade as usage grows.

**Acceptance criteria**:
- AC1: Given a tool fails with the same `(tool_name, normalized_error)`
  signature a second time in a repo, When `post_tool_failure` fires, Then a
  mistake memory is captured (first occurrence is not captured).
- AC2: Given `post_tool_failure` now behaves correctly, When
  `cairn install-agents` wires hooks, Then it is included in
  `_HOOK_ENTRYPOINTS` and written to client configs like `post_edit` /
  `session_end`.
- AC3: Given `capture_memory` receives a title/body matching session-state
  patterns (branch names, `T\d{3}` task IDs, dated progress counts), When
  the memory is captured, Then it is routed to the `raw` tier (7-day
  expiry) regardless of caller-supplied tier.
- AC4: Given `session_end` fires with a non-empty transcript, When the hook
  runs, Then a `memory-extract` task is queued or an agent-driven capture
  occurs (closing the currently-silent path) — verified by a task/queue
  assertion, not by re-reading this spec's own claim.

### US5 — The claim matches the code (P3)
As a reader of the README, I want documented behavior to match what the
code does, so that trust in cairn's "verifiable" claims isn't undermined by
its own docs.

**Acceptance criteria**:
- AC1: Given US1 ships, When the README describes memory recall, Then "recalled
  alongside graph results" is true for `explore` too (already true for
  `ask_compass`) — no doc change needed beyond confirming the claim now
  holds; if any tool is named that still doesn't do this, the claim is
  narrowed to name only the tools that do.

### US6 — Measurable, not just plausible (P2)
As a maintainer, I want memory recall quality to be measured the same way
code (L1) and knowledge (L5) recall already are, so that formula changes
are falsifiable instead of argued from anecdote.

**Acceptance criteria**:
- AC1: Given `eval.py` has L1 and L5 evaluation levels, When this ships,
  Then an L4 level exists pointing at `search_memory`, with a ground-truth
  dataset of situation→expected-memory pairs, reporting recall@k and MRR
  the same way L1/L5 do.
- AC2: Given a memory store where recall has never fired, When
  `cairn doctor` runs, Then a new check reports WARN for "memory tier has
  entries but zero references in the last 30 days" (write-only memory).

### US7 — The MCP surface matches what agents use (P2)
As an agent selecting from cairn's MCP tools, I want the memory tool list to
contain only tools agents actually call, so that tool-selection budget isn't
spent on maintenance verbs nobody invokes autonomously.

**Acceptance criteria**:
- AC1: Given `recall_memory` and `record_memory` are the only memory tools
  with agent call sites in current usage, When the MCP surface is revised,
  Then `memory_promote`, `memory_demote`, `memory_evolve`, `memory_decay`,
  `memory_delete`, `memory_digest` are removed from the MCP tool
  registration and remain available only via `cairn memory <verb>` CLI
  commands.
- AC2: Given the tool count drops from 28 to 22, When docs are updated,
  Then README/docs/mcp-tools.md reflect the new count and tool table.

## Requirements
- **FR-001**: The system shall include a tribal-memory section in
  `explore`'s response, populated via the existing `search_memory` fused
  lexical+semantic search scoped to the `tribal` tier and ranked against
  `explore`'s resolved seed symbol names.
- **FR-002**: WHEN `explore` surfaces a tribal memory, the system shall
  record a `memory_refs` row for it via the existing `record_reference`
  path (pass a real `session_id` into `search_memory`, not `None`).
- **FR-003**: The system shall cap the tribal-memory section at 3 entries,
  each rendered as title + the memory's "How to apply:" line only.
- **FR-004**: WHERE a client supports a session-start hook, the system shall emit the top score-ranked tribal memories for the current workspace as additional context once per session.
- **FR-005**: The system shall remove `critic_score` and `authority` from
  the memory scoring formula's weighted terms and renormalize the
  remaining weights (`graph_verification`, `cross_session_refs`,
  `agent_confidence`, `freshness`, `reinforcement`) to sum to 1.0.
- **FR-006**: The system shall gate `post_tool_failure` auto-capture on
  recurrence: a mistake memory is captured only when the same
  `(tool_name, normalized_error)` signature has already been observed at
  least once before in the workspace's raw/tribal memory store.
- **FR-007**: The system shall register `post_tool_failure` as an
  installable hook entrypoint (`_HOOK_ENTRYPOINTS`) wired the same way as
  `post_edit`/`session_end` in `cairn install-agents`.
- **FR-008**: WHEN `capture_memory` is called with a title/body matching session-bookkeeping patterns (branch-name references, `T\d{3}`-style task IDs, dated progress-count phrasing), the system shall force the memory's tier to `raw` regardless of the tier the caller requested.
- **FR-009**: WHEN a Claude Code session ends, the system shall read the
  transcript from the `transcript_path` field of the `SessionEnd` hook
  payload (parsing the JSONL file at that path) instead of the non-existent
  inline `messages` field it reads today, so that a non-empty transcript
  actually reaches `cairn memory capture` and a `memory-extract` task is
  queued (root cause confirmed in survey.md: `data.get("messages", [])` is
  always `[]` against Claude Code's real payload shape, so the capture path
  has never been reachable in production).
- **FR-010**: The system shall add an L4 evaluation level to `eval.py`
  mirroring the existing L1/L5 shape (`evaluate_l4_query`, `_retrieve_l4`,
  a ground-truth dataset), reporting recall@k and MRR for `search_memory`.
- **FR-011**: The system shall add a `cairn doctor` check that reports WARN
  when a workspace has ≥1 tribal memory older than 30 days with zero
  `memory_refs` rows recorded in that window.
- **FR-012**: The system shall remove `memory_promote`, `memory_demote`,
  `memory_evolve`, `memory_decay`, `memory_delete`, `memory_digest` from
  MCP tool registration, keeping their behavior reachable only via existing
  `cairn memory <verb>` CLI subcommands (no behavior change to the
  underlying functions — registration-surface change only).
- **FR-013**: The system shall update README.md and docs/mcp-tools.md tool
  counts/tables to reflect the reduced 22-tool MCP surface (28 − 6).

## Scope
**In**:
- `explore` memory integration + reference recording (FR-001..003)
- session-start hook for Claude Code / Cursor (FR-004)
- scoring formula rebalance (FR-005)
- `post_tool_failure` recurrence gate + hook wiring (FR-006, FR-007)
- capture-time triage for session-bookkeeping content (FR-008)
- fixing the silent `session_end` capture path (FR-009)
- L4 eval level + doctor check (FR-010, FR-011)
- MCP memory tool surface trim + doc updates (FR-012, FR-013)

**Out (deferred)**:
- Actually running `memory-critic` LLM tasks to make `critic_score` live
  again (would require wiring an LLM agent to process the task queue
  continuously — a bigger, separate change). Dropped from the formula
  instead (FR-005); can be re-added later as a D-### if the task queue
  gets a standing processor.
- Retroactively re-tiering or re-scoring the 34 existing memories in any
  live user's store — this spec changes the code path going forward; a
  backfill/migration script is out of scope unless a task surfaces it's
  trivially cheap during implementation.
- Any change to `wiki`/`compass`/`knowledge` layers beyond the memory tier.

## Assumptions & risks
- Assumption: "session-bookkeeping pattern" detection (FR-008) is a
  best-effort regex/heuristic, not a classifier — false negatives (missed
  bookkeeping noise) are acceptable; false positives (durable knowledge
  wrongly demoted to raw) are not, so the heuristic should err toward
  narrow/precise patterns. Survey/tech stage to confirm exact patterns
  against the 34 real memories as ground truth.
- Assumption: MCP tool removal (FR-012) is acceptable as a breaking change
  pre-1.0 (repo is "Beta — pre-1.0" per README) — user confirmed at spec
  time (see AskUserQuestion answer, "Trim to 2 MCP tools").
- Risk: `record_reference`'s batched write happens inside `search_memory`
  under a live `conn` — `explore`'s connection is opened/closed per call
  (`tools_graph.py`); need to confirm the write lands before `conn.close()`
  and doesn't reintroduce the lock-contention issue `record_references_batch`
  was written to avoid. Mitigation: surveyor to trace `explore`'s conn
  lifecycle before tech-spec commits to an approach.
- Risk: gating `post_tool_failure` on recurrence (FR-006) requires reading
  existing memory state from within a hook meant to be a fast, detached,
  non-blocking subprocess call — a lookup query must stay cheap. Mitigation:
  tech-spec to specify an indexed/bounded lookup, not a full corpus scan.
- Note: `memory_delete`'s existing CLI equivalent is named `cairn memory
  forget`, not `delete` (survey.md, FR-012/013). FR-012 still holds (the
  operation remains reachable via CLI) but FR-013's doc updates must not
  invent a `memory delete` CLI command that doesn't exist — cite `forget`
  by its real name.

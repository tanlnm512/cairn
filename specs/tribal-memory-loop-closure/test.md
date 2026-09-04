# Test Cases: tribal-memory-loop-closure

**Spec**: [spec.md](spec.md) | **Created**: 2026-09-04
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details.

## TC-001 — Explore surfaces a matching tribal memory
- **Story**: US1 · **Traces to**: FR-001, AC1
- **Given** a tribal memory exists whose title/body overlaps the symbols an
  `explore` query will resolve
- **When** an agent calls `explore(query)` for that symbol
- **Then** the response includes a `=== Tribal memory ===` section listing
  the matching memory, showing its title and its "How to apply" line
- **Pass condition**: the returned `explore` output contains a
  `=== Tribal memory ===` section, and the matching memory's title text
  appears within it alongside a "How to apply" line for that memory.

## TC-002 — Explore reports "(none)" when no tribal memory matches
- **Story**: US1 · **Traces to**: FR-001, AC2
- **Given** no tribal memory's title/body overlaps the symbols a query will
  resolve (a query on a fresh/unrelated symbol with no recorded memories)
- **When** an agent calls `explore(query)`
- **Then** the tribal-memory section is still present in the response but
  reads `(none)`, consistent with how explore renders its other empty
  sections — it is not silently omitted
- **Pass condition**: the response contains a `=== Tribal memory ===`
  section whose body is exactly `(none)`; the section header is present
  even though there is no match.

## TC-003 — A surfaced memory's usage gets counted
- **Story**: US1 · **Traces to**: FR-002, AC3
- **Given** a tribal memory that has never been recalled before (its
  recorded usage/reference count is at a known baseline, e.g. zero)
- **When** an agent calls `explore(query)` and that memory is surfaced in
  the `=== Tribal memory ===` section
- **Then** the memory's usage is counted toward its recall history — a
  later, independent inspection of that same memory (e.g. recalling it
  again, or any equivalent query that reports how many times a memory has
  been referenced) shows its reference count has increased from the
  baseline
- **Pass condition**: comparing the memory's reported reference/usage count
  before and after the `explore` call shows an increase of at least one;
  the increase persists across a new session/query (not just in-memory for
  the single call).

## TC-004 — Explore's tribal-memory section is capped at 3, title + "How to apply" only
- **Story**: US1 · **Traces to**: FR-003
- **Given** more than 3 tribal memories match the symbols an `explore`
  query will resolve
- **When** an agent calls `explore(query)`
- **Then** the `=== Tribal memory ===` section lists at most 3 memories,
  and each listed memory shows only its title and its "How to apply" line
  — no other memory fields (body, confidence, tags, etc.) are rendered
- **Pass condition**: counting the listed memory entries in the section
  yields ≤ 3; for each entry, only a title line and a "How to apply" line
  are present, with no additional memory metadata printed.

## TC-005 — Concurrent explore calls on the same memory don't corrupt or error the reference count
- **Story**: US1 (boundary) · **Traces to**: FR-002, AC3
- **Given** a tribal memory that matches a query, and two agents/sessions
  positioned to call `explore(query)` for it at the same time
- **When** both calls execute concurrently against the same repo
- **Then** neither call errors or hangs, and the memory's usage count
  reflects both references being recorded (no lost update, no duplicate
  crash)
- **Pass condition**: both `explore` calls return successfully (no
  exception/timeout surfaced to the caller), and the memory's final
  reference count increased by 2 relative to its pre-test baseline (not 0,
  not 1 from a dropped write, not an error state).

## TC-006 — Session start surfaces top tribal memories as context
- **Story**: US2 · **Traces to**: FR-004, AC1
- **Given** a repo has tribal memories with varying scores
- **When** a supported client's session-start hook fires for a new session
  in that repo
- **Then** the top score-ranked tribal memories for that workspace are
  emitted as additional context to the agent, once for that session
- **Pass condition**: the session-start output/context includes tribal
  memory content, and the memories present are the highest-scored ones for
  that workspace (not an arbitrary or unscored subset); a second
  session-start event in the same session does not re-emit them.

## TC-007 — Session-start memory context stays within a token budget
- **Story**: US2 (boundary) · **Traces to**: FR-004, AC1
- **Given** a repo has a large number of tribal memories, more than would
  fit under a reasonable context budget if all were emitted
- **When** the session-start hook fires
- **Then** the emitted memory context is capped rather than dumping every
  tribal memory in the store
- **Pass condition**: the size (character/line count, or item count) of the
  emitted memory context stays under the documented cap regardless of how
  many tribal memories exist in the store.

## TC-008 — Session start emits nothing when there are no tribal memories
- **Story**: US2 · **Traces to**: FR-004, AC2
- **Given** a repo has zero tribal memories
- **When** the session-start hook fires
- **Then** no memory section, empty placeholder, or error is emitted —
  the hook is silent on this point
- **Pass condition**: the session-start output contains no
  memory-related section at all (not even an empty one), and the hook
  completes without raising an error or writing an error message.

## TC-009 — The published scoring weights drop critic_score/authority and still sum to 1.0
- **Story**: US3 · **Traces to**: FR-005, AC1
- **Given** the scoring formula's weight table
- **When** the formula is inspected after this change ships
- **Then** `critic_score` and `authority` are no longer weighted terms, and
  the remaining terms (`graph_verification`, `cross_session_refs`,
  `agent_confidence`, `freshness`, `reinforcement`) sum to 1.0
- **Pass condition**: the weight table used by the scoring formula has no
  entry for `critic_score` or `authority`; summing the remaining weight
  values yields 1.0 (within floating-point tolerance).

## TC-010 — A memory with strong graph-verification and repeated recall outscores one with only self-reported confidence
- **Story**: US3 · **Traces to**: FR-005, AC2
- **Given** two otherwise-comparable memories: Memory A has strong
  graph-verification evidence and has been recalled/referenced multiple
  times across sessions; Memory B has only a caller-supplied confidence
  value and has never been recalled or referenced
- **When** both memories are scored under the revised formula
- **Then** Memory A scores meaningfully higher than Memory B, to the point
  that A is eligible for tribal-tier promotion while B is not
- **Pass condition**: Memory A's computed score is materially higher than
  Memory B's (not within the same narrow cluster the old formula produced),
  and only Memory A crosses the tribal-tier promotion threshold.

## TC-011 — Scores show wider spread than the old two-cluster distribution
- **Story**: US3 (boundary) · **Traces to**: FR-005, AC2
- **Given** a synthetic set of memories whose only varying signal is
  `cross_session_refs` (some referenced many times, some never)
- **When** each is scored under the revised formula
- **Then** the resulting scores are spread across a meaningfully wider
  range than two values, rather than clustering onto one or two shared
  scores as the pre-change formula produced
- **Pass condition**: computing scores for the synthetic set yields more
  than 2 distinct score values, with the range (max − min) wider than the
  pre-change formula's observed range on the same inputs.

## TC-012 — A tool failure is not captured as a mistake memory the first time it occurs
- **Story**: US4 · **Traces to**: FR-006, AC1
- **Given** a tool has never failed with a given `(tool_name, error)`
  combination in this workspace before
- **When** that tool fails with that exact error for the first time and the
  failure hook fires
- **Then** no mistake memory is captured for this occurrence
- **Pass condition**: after the first failure, no new mistake memory
  exists in the store for that `(tool_name, error)` signature.

## TC-013 — The same tool failure recurring a second time is captured as a mistake memory
- **Story**: US4 · **Traces to**: FR-006, AC1
- **Given** a tool has already failed once with a given `(tool_name,
  error)` combination in this workspace (per TC-012)
- **When** the same tool fails with the same error signature again and the
  failure hook fires
- **Then** a mistake memory is captured for that failure
- **Pass condition**: after the second occurrence, a new mistake memory
  exists in the store referencing that tool/error, that was absent after
  the first occurrence alone.

## TC-014 — A different failure signature is treated as a first occurrence, not captured
- **Story**: US4 (boundary) · **Traces to**: FR-006, AC1
- **Given** a tool has already failed once with error signature X (per
  TC-012, not yet captured)
- **When** the same tool fails with a different error signature Y for the
  first time
- **Then** signature Y is treated as its own first occurrence and is not
  captured (the recurrence gate is keyed on the exact `(tool_name, error)`
  pair, not just the tool)
- **Pass condition**: no mistake memory exists for the `(tool_name, Y)`
  signature after this single occurrence, even though `(tool_name, X)` has
  already occurred once.

## TC-015 — The failure-capture hook is wired into agent installation like other hooks
- **Story**: US4 · **Traces to**: FR-007, AC2
- **Given** a supported client's agent-installation flow already wires the
  edit and session-end hooks
- **When** the agent-installation flow (`cairn install-agents`) runs
- **Then** the failure-capture hook is wired into the client's config
  alongside the existing edit/session-end hooks, not left absent
- **Pass condition**: after running the installation flow, the client's
  hook configuration contains an entry for the tool-failure hook, in
  addition to the pre-existing edit/session-end entries.

## TC-016 — Session-bookkeeping content is routed to the short-lived raw tier regardless of requested tier
- **Story**: US4 · **Traces to**: FR-008, AC3
- **Given** a memory is captured with a title/body that matches
  session-bookkeeping patterns (e.g. references a branch name, a `T###`
  style task ID, or a dated progress count like "240 done, 157 remaining"),
  and the caller requests it be stored in the durable tribal tier
- **When** the capture is processed
- **Then** the memory is placed in the raw tier with its short expiry,
  overriding the caller's requested tier
- **Pass condition**: after capture, the memory's stored tier is `raw`
  (with the raw tier's expiry applied), not the tribal tier the caller
  requested.

## TC-017 — Durable knowledge is not mistakenly routed to the raw tier
- **Story**: US4 (boundary) · **Traces to**: FR-008, AC3
- **Given** a memory is captured with declarative/technical content
  containing no branch name, no task ID, and no dated progress count (e.g.
  a statement of a technical constraint or a testing convention)
- **When** the capture is processed with a caller-requested tribal tier
- **Then** the memory keeps the caller-requested tier — the bookkeeping
  heuristic does not fire on durable content
- **Pass condition**: after capture, the memory's stored tier matches what
  the caller requested (not force-downgraded to raw).

## TC-018 — A non-empty session transcript results in a queued memory-extraction
- **Story**: US4 · **Traces to**: FR-009, AC4
- **Given** a session that produced a non-empty transcript ends
- **When** the session-end hook processes that transcript
- **Then** a memory-extraction task is queued (or an agent-driven capture
  occurs) as a result — the transcript content actually reaches the
  capture path instead of being silently dropped
- **Pass condition**: after the session ends, a new memory-extraction
  task/queue entry (or a newly captured memory) exists that references
  that session, where none existed before the session ended.

## TC-019 — An empty session transcript produces no spurious task and no error
- **Story**: US4 (boundary) · **Traces to**: FR-009, AC4
- **Given** a session ends having produced no transcript content at all
- **When** the session-end hook fires
- **Then** no memory-extraction task is queued, and the hook does not error
- **Pass condition**: no new memory-extraction task/queue entry appears
  after this session ends, and the hook exits without an error being
  surfaced.

## TC-020 — README's memory-recall claim matches what explore actually does (MANUAL)
- **Story**: US5 · **Traces to**: FR-001, US5-AC1 (downstream doc-consistency
  check on FR-001's delivery — US5 itself names no dedicated FR in spec.md)
- **Given** FR-001 has shipped (explore now surfaces tribal memory, per
  TC-001/TC-002)
- **When** a reviewer reads the README's description of memory recall
  behavior
- **Then** the claim "recalled alongside graph results" is true for every
  tool named in that claim; if a named tool still does not do this, the
  claim is narrowed to name only the tools that actually do
- **Pass condition** (MANUAL — subjective doc/code comparison, not
  automatable): a reviewer checks each tool named in the README's recall
  claim against its actual behavior and confirms the sentence names no
  tool it doesn't hold true for.

## TC-021 — L4 evaluation reports recall@k and MRR for a known memory-recall query
- **Story**: US6 · **Traces to**: FR-010, AC1
- **Given** a ground-truth dataset of situation→expected-memory pairs
  exists for the memory-recall evaluation level
- **When** the evaluation tool is run at the memory-recall (L4) level
  against that dataset
- **Then** it reports recall@k and MRR numbers for the memory search,
  in the same reporting shape as the existing code (L1) and knowledge (L5)
  levels
- **Pass condition**: running the evaluation at the memory-recall level
  produces a numeric recall@k and a numeric MRR in its output/report,
  computed against the ground-truth situation→memory pairs.

## TC-022 — `cairn doctor` flags a write-only tribal memory
- **Story**: US6 · **Traces to**: FR-011, AC2
- **Given** a workspace has at least one tribal memory older than 30 days
  with zero recorded references in that 30-day window
- **When** `cairn doctor` runs
- **Then** it reports a WARN-level check for "memory tier has entries but
  zero references in the last 30 days"
- **Pass condition**: the doctor report includes a check with WARN status
  whose description matches the write-only-memory condition, and the
  overall doctor exit code reflects WARN (not FAIL, not silently passing).

## TC-023 — `cairn doctor` does not warn when a tribal memory has recent references
- **Story**: US6 (boundary) · **Traces to**: FR-011, AC2
- **Given** a workspace's tribal memories all have at least one recorded
  reference within the last 30 days
- **When** `cairn doctor` runs
- **Then** the write-only-memory check does not fire a WARN
- **Pass condition**: the doctor report shows the write-only-memory check
  as PASS (or absent/not triggered), with no WARN raised for any
  currently-referenced memory.

## TC-024 — The six memory lifecycle tools are no longer offered via MCP but remain reachable via CLI
- **Story**: US7 · **Traces to**: FR-012, AC1
- **Given** an agent connected to cairn's MCP server
- **When** the agent lists available MCP tools
- **Then** `memory_promote`, `memory_demote`, `memory_evolve`,
  `memory_decay`, `memory_delete`, and `memory_digest` are not present in
  the tool list, while the equivalent operations remain available through
  `cairn memory <verb>` CLI commands (noting the delete operation's CLI
  verb is `forget`, not `delete`)
- **Pass condition**: none of the six named tools appear in the MCP tool
  listing; running each corresponding `cairn memory <verb>` CLI command
  (`promote`, `demote`, `evolve`, `decay`, `forget`, `digest`) still
  performs its operation successfully.

## TC-025 — `recall_memory` and `record_memory` continue to work via MCP after the trim
- **Story**: US7 (regression guard) · **Traces to**: FR-012
- **Given** the six lifecycle tools have been removed from the MCP
  registration
- **When** an agent calls `recall_memory` and `record_memory` via MCP
- **Then** both continue to function exactly as before — recall returns
  matching memories, record captures a new one
- **Pass condition**: an MCP call to `recall_memory` returns memory
  results for a known query, and an MCP call to `record_memory` results in
  a new memory existing in the store afterward.

## TC-026 — Docs reflect the reduced 22-tool MCP surface with correct verb names
- **Story**: US7 · **Traces to**: FR-013, AC2
- **Given** the MCP tool count has dropped from 28 to 22 (per FR-012)
- **When** README.md and docs/mcp-tools.md are reviewed
- **Then** both documents state the tool count as 22 (not 28), their tool
  tables no longer list the six removed tools as MCP tools, and no
  document invents a `cairn memory delete` CLI command — the delete
  operation is correctly named `cairn memory forget`
- **Pass condition**: grepping README.md and docs/mcp-tools.md for the
  tool count finds "22" (not "28") in every place a total is stated; the
  six removed tools' MCP-tool table rows are gone or clearly marked
  CLI-only; no occurrence of a "memory delete" CLI command name exists in
  either doc.

## Coverage matrix
<!-- Every FR appears; `check.py` fails an FR with no TC. -->
| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001      | TC-001, TC-002, TC-020 | auto (TC-020 manual) |
| FR-002      | TC-003, TC-005 | auto |
| FR-003      | TC-004     | auto |
| FR-004      | TC-006, TC-007, TC-008 | auto |
| FR-005      | TC-009, TC-010, TC-011 | auto |
| FR-006      | TC-012, TC-013, TC-014 | auto |
| FR-007      | TC-015     | auto |
| FR-008      | TC-016, TC-017 | auto |
| FR-009      | TC-018, TC-019 | auto |
| FR-010      | TC-021     | auto |
| FR-011      | TC-022, TC-023 | auto |
| FR-012      | TC-024, TC-025 | auto |
| FR-013      | TC-026     | auto |
| US5-AC1 (no FR — doc/code consistency) | TC-020 | manual |

**Untestable**: none — every FR has an observable, automatable pass
condition. Only TC-020 (US5's doc-vs-code consistency check) is MANUAL,
because comparing prose claims to behavior across arbitrary tool names is
inherently a subjective review, not a scriptable assertion.

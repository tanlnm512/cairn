# Test Cases: context-pack

**Spec**: [spec.md](spec.md) | **Created**: 2026-09-17
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details.

Observable for this suite = the agent-CLI contract: exit code, stdout
payload, and reported counts. All TCs are forward-looking acceptance tests —
the pack command does not exist yet, so none guards existing pack behavior.

## TC-001 — Happy path: one block, within budget, count reported
- **Story**: US1 · **Traces to**: FR-001, AC1
- **Given** a fully indexed codebase, a task description, and a generous budget
- **When** the pack command runs once with that task and budget
- **Then** a single markdown context block is emitted containing source snippets, a blast-radius summary, a module guidance excerpt, and any relevant memories; the block reports the tokens it used and the number is within the requested budget
- **Pass condition**: `cairn pack --task "how does the retry backoff policy handle transient failures" --budget 4000`
exits 0; stdout is one markdown block with no leading or trailing chatter;
the block states its token total, greater than 0 and at most 4000.

## TC-002 — Works without embeddings (word-based fallback)
- **Story**: US1 · **Traces to**: FR-001
- **Given** an indexed codebase where vector embeddings are absent
- **When** the pack command runs with a task phrased around a known concept
- **Then** the pack still succeeds and its snippets match the task's subject matter (found by word match, not vectors)
- **Pass condition**: `cairn pack --task "token budget fitting for context packs" --budget 3000`
exits 0 in an index built without the embeddings extra; the block contains
at least one source snippet about token budgeting.

## TC-003 — Expansion reaches what the task did not name
- **Story**: US1 · **Traces to**: FR-001
- **Given** an indexed codebase where a named entry point relies on helpers it calls and is relied on by its callers
- **When** the task names only the entry point
- **Then** the block also includes material for related definitions the task text never mentions, reached through call relationships
- **Pass condition**: `cairn pack --task "trace what happens when a wiki page promotion is rejected by the critic" --budget 5000`
human confirms the block contains definitions beyond those named in the
task, linked to the named flow by caller/callee relationships.

## TC-004 — Content completeness for a well-connected symbol
- **Story**: US1 · **Traces to**: FR-003, AC1
- **Given** a matched symbol that has callers, belongs to a module with a navigation guide, and has recorded memories
- **When** the pack runs with a budget large enough to fit everything
- **Then** the symbol's section shows verbatim source, a summary of what depends on it (immediate and second-hop), its module's guidance excerpt, and relevant memories
- **Pass condition**: `cairn pack --task "retry backoff policy with callers and past mistakes" --budget 6000`
exits 0; the block contains all four content kinds: verbatim code, a
blast-radius summary, a module guidance excerpt, and at least one memory.

## TC-005 — Boundary: no memories exist
- **Story**: US1 · **Traces to**: FR-003
- **Given** an indexed codebase with zero recorded memories
- **When** the pack runs for any task
- **Then** it succeeds without a memories section; the other three content kinds are unaffected
- **Pass condition**: `cairn pack --task "compress the repo map cluster output" --budget 2500`
exits 0; the block has no memory section and still shows source,
blast-radius, and guidance content.

## TC-006 — Boundary: oversized definition is trimmed
- **Story**: US1 · **Traces to**: FR-003
- **Given** a matched definition whose full text alone exceeds the budget
- **When** the pack runs under that budget
- **Then** the definition appears trimmed — its signature plus its key body, never a full dump; the reported token total stays within budget
- **Pass condition**: `cairn pack --task "the graph closure builder routine" --budget 2000`
exits 0; the snippet for the oversized definition is abridged and the
reported token total is at most 2000.

## TC-007 — Ranking: the widely-relied-upon item outlasts the peripheral one
- **Story**: US1 · **Traces to**: FR-002, FR-004, AC2
- **Given** two definitions match the task; one is depended on by many parts of the system, the other by almost none
- **When** the budget fits only one of them
- **Then** the widely-depended-on one is kept; the peripheral one is dropped and counted as dropped — selection follows structural importance, not match order
- **Pass condition**: `cairn pack --task "memory promotion scoring" --budget 900`
on a workspace containing such a pair, human confirms the hub's material is
present, the leaf's is absent, and the drop report names the leaf.

## TC-008 — Impossible budget degrades gracefully and reports drops
- **Story**: US1 · **Traces to**: FR-004, AC2
- **Given** a task whose full selection cannot come close to the requested budget
- **When** the pack runs with that tiny budget
- **Then** it exits successfully, drops lowest-importance items first, reports how many items were dropped, and still emits a well-formed block with whatever fit
- **Pass condition**: `cairn pack --task "retry backoff policy with callers and past mistakes" --budget 50`
exits 0; the output reports a dropped-items count greater than zero; the
remaining block is complete and consistent, with no cut-off fragments.

## TC-009 — Boundary: empty task description
- **Story**: US1 · **Traces to**: FR-001
- **Given** the pack command invoked with no task text, empty or whitespace only
- **When** it runs
- **Then** it is rejected with a clear usage message and a non-zero exit code — no crash, no stack trace, no empty-pack output
- **Pass condition**: `cairn pack --task "" --budget 1000`
exits non-zero; the message names the missing task argument; the same holds
for a whitespace-only task.

## TC-010 — Boundary: non-positive budget
- **Story**: US1 · **Traces to**: FR-004, AC2
- **Given** a valid task with a budget of zero or negative
- **When** the pack runs
- **Then** it is rejected with a clear message and non-zero exit — an invalid budget is an input error, not a degenerate empty pack
- **Pass condition**: `cairn pack --task "retry backoff policy" --budget 0`
exits non-zero with a message naming the invalid budget; a negative budget
behaves identically.

## TC-011 — Standing guard: deterministic, offline, no LLM
- **Story**: US1 · **Traces to**: FR-005
- Standing regression: fails if a network call, an LLM call, or any
  nondeterminism ever enters the default pack path.
- **Given** a stable index and a fixed task
- **When** the pack runs twice with identical inputs, the second run with all network traffic forced through a dead local proxy
- **Then** both runs exit 0 and their outputs are byte-identical — an LLM or network dependency would fail or diverge
- **Pass condition**: `cairn pack --task "retry backoff policy" --budget 1500 > /tmp/qa-pack-run1.md && HTTPS_PROXY=http://127.0.0.1:9 HTTP_PROXY=http://127.0.0.1:9 cairn pack --task "retry backoff policy" --budget 1500 > /tmp/qa-pack-run2.md && diff /tmp/qa-pack-run1.md /tmp/qa-pack-run2.md`
exits 0 with no diff output.

## TC-012 — Standing verify: at-scale timeliness
- **Story**: US1 · **Traces to**: FR-001
- **Given** an indexed repository orders of magnitude larger than this project's
- **When** the pack runs for a representative task
- **Then** it completes promptly and respects the budget — the one-shot promise holds at scale, not only on small trees
- **Pass condition**: `time cairn pack --task "request routing and validation" --budget 3000`
human-observed on a large indexed repository: finishes well under two
minutes with the reported token total within budget. This is the standing
verify; the audit runs the bounded TC-001 instead — the 120 s per-TC audit
cap is a harness fact, not a product contract.

## TC-013 — Boundary: concurrent pack runs
- **Story**: US1 · **Traces to**: FR-001
- **Given** a stable index
- **When** two pack runs start simultaneously with the same task
- **Then** both exit 0, neither corrupts or truncates output, and their blocks are identical
- **Pass condition**: `cairn pack --task "configuration loading flow" --budget 2500 > /tmp/qa-pack-a.md & cairn pack --task "configuration loading flow" --budget 2500 > /tmp/qa-pack-b.md & wait; diff /tmp/qa-pack-a.md /tmp/qa-pack-b.md`
exits 0 with no diff output.

## Coverage matrix
<!-- Every FR appears; `check.py` fails an FR with no TC. -->
| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001      | TC-001, TC-002, TC-003, TC-009, TC-012, TC-013 | auto, auto, manual, auto, manual, auto |
| FR-002      | TC-007     | manual             |
| FR-003      | TC-004, TC-005, TC-006 | auto, auto, auto |
| FR-004      | TC-007, TC-008, TC-010 | manual, auto, auto |
| FR-005      | TC-011     | auto               |

No FR is MISSING coverage and none is untestable. TC-003, TC-007, and
TC-012 are manual because their observable is content judgment or needs a
human-confirmed workspace fixture — not because the requirement lacks an
observable effect.

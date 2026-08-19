# Spec: ui-dashboard

**Status**: done          <!-- draft while writing → active once the branch is cut
                               and the first task starts → done when all tasks are
                               ticked and `check.py` re-runs green -->
**Created**: 2026-08-20
**Branch**: `feat/ui-dashboard`

## What
A local web dashboard for cairn, launched with a single CLI command, that shows:
(1) every project cairn has indexed — with its index and embedding status,
counts, and freshness; (2) an interactive graph view of a selected project's
code structure; (3) a history of every cairn tool invocation; (4) token usage
attributed per tool; and (5) per-session tool call chains showing how agents
sequence cairn's tools. Plus suggested P2 panels (health, memory, task queue).

## Why
Everything cairn knows — projects, graph structure, tool traffic — is locked
behind CLI commands or raw SQLite. Answering "what's indexed?", "how big is the
graph?", "which tools do agents actually call, and which ones burn context
tokens?" today requires hand-written SQL. A dashboard makes cairn's state
inspectable at a glance for its owner and demo-able to others. Tool-use
history and token attribution do not exist at all yet — without recording
them, agent-interaction quality is invisible and the most context-expensive
tools cannot be identified or optimized.

## Business value
- The cairn owner gets a zero-setup visual surface for the whole system
  (projects, graphs, agent traffic) instead of ad-hoc SQL.
- Tool-usage telemetry turns "we think agents use X" into measured fact:
  call counts, latency, failure rates, and context-token cost per tool.
- Success criteria:
  - **SC-1**: `cairn dashboard` serves all five core views from the local DB,
    read-only, and first page render completes in < 2s on a warmed DB.
  - **SC-2**: tool-call recording is buffered off the hot path and adds
    < 5% latency to any recorded tool call.
  - **SC-3**: after a clean shutdown, 100% of tool calls that returned are
    present in the history (no silent drops).

## User stories
<!-- Ordered by priority; each independently demoable. -->
### US1 — Projects overview (P1)
As a cairn owner, I want a list of all indexed projects with their index and
embedding status, counts, and last-indexed time, so that I can see coverage
and freshness at a glance.

**Acceptance criteria** (each traces to an FR below):
- AC1: Given a DB with indexed projects, When I open the projects view, Then
  every project known to cairn is listed with file/symbol/edge counts and
  last-indexed timestamp.
- AC2: Given a project whose embeddings are missing/partial, When I view its
  row, Then its embedding status distinguishes embedded vs not (and shows the
  embedding model where recorded).

### US2 — Project graph view (P1)
As a developer exploring a codebase, I want an interactive graph of a selected
project, so that I can understand its structure without CLI commands.

**Acceptance criteria**:
- AC1: Given a selected project, When I open its graph view, Then the browser
  renders the project's dependency/call graph interactively (pan, zoom).
- AC2: Given the graph view, When I change scope/depth controls, Then the
  rendered graph updates accordingly (reusing cairn's existing viz query
  layer scopes).

### US3 — Tool-use history (P1)
As a cairn maintainer, I want a chronological, filterable history of cairn
tool invocations, so that I can see how agents actually use cairn.

**Acceptance criteria**:
- AC1: Given recorded tool calls, When I open the history view, Then I see
  them newest-first with tool name, timestamp, duration, status, and session.
- AC2: Given the history view, When I filter by tool name and/or session,
  Then only matching calls are shown.

### US4 — Token usage per tool (P1)
As a cairn maintainer, I want estimated context-token usage attributed per
tool call and aggregated per tool, so that I can find and optimize the most
context-expensive tools.

**Acceptance criteria**:
- AC1: Given recorded tool calls with payload sizes, When I open the token
  view, Then I see per-tool aggregates (calls, total and mean estimated
  tokens) ranked by cost.
- AC2: Given a single tool call in history, When I inspect it, Then its
  estimated request/response token counts are shown.

### US5 — Tool call chains (P1)
As an agent-behavior researcher, I want tool calls grouped per session as
ordered chains, so that I can see how agents sequence cairn's tools.

**Acceptance criteria**:
- AC1: Given recorded sessions, When I open the chains view, Then I see each
  session's calls in order, visually connected as a chain/timeline.
- AC2: Given a chain, When two tools are far apart in time (> session gap),
  Then they are presented as separate sessions rather than one chain.

### US6 — Health panel (P2, suggested)
As an operator, I want a health panel (index freshness, DB size, vector/rerank
backend status), so that I can spot degradation without running `cairn doctor`.

**Acceptance criteria**:
- AC1: Given a cairn DB, When I open the health view, Then DB size, index
  freshness, the vector backend mode, and reranker status are shown in one
  place, agreeing with `cairn doctor` on the same database.

### US7 — Memory & task queue panels (P2, suggested)
As a cairn owner, I want to browse recent memories and the LLM task queue,
so that I can inspect cairn's own knowledge state.

**Acceptance criteria**:
- AC1: Given recorded memories, When I open the memory view, Then recent
  memories are listed with type and title.
- AC2: Given queued LLM tasks, When I open the task view, Then pending/
  claimed/done tasks are listed by status.

## Requirements
<!-- SHALL statements: one verb, one system, testable. -->
- **FR-001**: The system SHALL provide a `cairn dashboard` CLI command that
  starts a local, read-only web dashboard on localhost and prints its URL.
- **FR-002**: The dashboard SHALL list every project known to the active DB
  with its index status (file/symbol/edge counts, last-indexed time) and
  embedding status (embedded vs not, embedding model where recorded).
- **FR-003**: The dashboard SHALL render an interactive graph (pan/zoom) for
  a selected project, with scope and depth controls backed by cairn's
  existing visualization query layer.
- **FR-004**: The MCP server SHALL persist a record for every tool
  invocation — tool name, timestamp, session id, duration, status
  (ok/error), and request/response payload sizes — to local storage,
  buffered so recording stays off the tool's hot path.
- **FR-005**: The dashboard SHALL present the recorded tool-call history
  newest-first, filterable by tool name and session, showing tool name,
  timestamp, duration, status, and session for each call.
- **FR-006**: The dashboard SHALL estimate context-token usage per recorded
  tool call (from payload sizes) and aggregate it per tool (calls, total,
  mean), ranked by total.
- **FR-007**: The dashboard SHALL group recorded tool calls by session and
  present each session's calls as an ordered chain/timeline, splitting
  sessions at inactivity gaps.
- **FR-008**: The dashboard SHALL expose a health panel showing DB size,
  index freshness, the vector backend mode (including hash-fallback state),
  and reranker status — agreeing with what `cairn doctor` reports for the
  same database.
- **FR-009**: The dashboard SHALL expose panels listing recent memories
  (type, title) and LLM task-queue entries by status.
- **FR-010**: The dashboard SHALL only open the DB read-only and SHALL NOT
  mutate graph/knowledge state; the sole writes cairn performs are the
  FR-004 tool-call records made by the server, not the dashboard.
- **FR-011**: Given tool-call records, When the process shuts down cleanly,
  Then all buffered records SHALL be flushed to storage (no silent drops).

## Scope
**In**: `cairn dashboard` local web UI (read-only); tool-call recording in the
MCP server; the five core views (projects, graph, history, tokens, chains);
suggested P2 panels (health, memory, tasks); tests for recording and view
data assembly.
**Out (deferred)**: authentication / multi-user / remote serving (bind
localhost only); any mutating actions from the UI (re-index, delete, edit);
charts beyond simple aggregates; time-series retention policies or rotation;
streaming/live-updating views (manual refresh is fine first).

## Assumptions & risks
- Assumption: dashboard is local, single-user, no auth — default binding is
  127.0.0.1 (the request said "UI dashboard", not a hosted product).
- Assumption: "token usage of tool" means *estimated context-token cost* of
  cairn's tool request/response payloads (cairn never calls an LLM and cannot
  see the host agent's billing); estimation method is the tech spec's call.
- Assumption: tool-call args may embed user code, so history stores arg
  summaries (truncated) rather than full payloads by default.
- Assumption: "projects" maps to whatever multi-project notion the DB already
  holds (survey to establish the exact model; the view must not invent a new
  registry).
- Risk: MCP server may already have a telemetry/OTel path that partially
  records tool calls — duplicating it would be waste; mitigation: surveyor
  establishes existing coverage first, FR-004 builds on it (extend, not
  replace).
- Risk: interactive graph rendering for large projects (10k+ nodes) can
  degrade the browser; mitigation: reuse the viz layer's existing scope/depth
  truncation, default to module scope.
- Risk: new web-framework dependency could bloat install or break CI extras;
  mitigation: researcher evaluates minimal-dependency options, tech spec pins
  the choice and where it lands (core vs optional extra).

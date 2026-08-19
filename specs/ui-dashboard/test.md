# Test Cases: ui-dashboard

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-20
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details.

## Conventions for runners
- `$DASH` = the localhost URL the dashboard command prints at launch
  (FR-001). Fixture runs use the CLI's standard database-selection flag,
  e.g. `uv run cairn dashboard --db <fixture.db>`.
- "The view's data feed" = the machine-readable source the implementation
  documents for that view. Automated pass conditions assert on the feed's
  JSON **content**, not its route — bind the route at automation time.
  Where no feed is documented, the browser-automation form (load view,
  assert visible text, screenshot) is the executable form.
- Fixture databases are built with cairn's existing commands (index /
  update, memory record, task) against small known sample projects, so
  expected counts, timestamps, statuses, and titles are known by
  construction. Automated cases run under `uv run pytest` (CLI-level cases
  use the CLI test runner convention with an explicit database flag).
- "Warmed database" = a database file that has already served at least one
  dashboard request in the same server process (cold-start page-cache I/O is
  excluded from the 2s budget; SC-1).

## TC-001 — Dashboard launches and prints its URL
- **Story**: all (entry point) · **Traces to**: FR-001
- **Given** cairn is installed and a database with at least one indexed project exists
- **When** I run the dashboard launch command (`cairn dashboard`)
- **Then** a local web server starts, the command prints a localhost URL, and that URL serves the dashboard's landing view
- **Pass condition**: run `uv run cairn dashboard --db <fixture.db>`; the output includes a URL on localhost; `curl -s -o /dev/null -w "%{http_code}" "<printed URL>"` prints `200`. GUI: opening the URL shows the dashboard landing page (screenshot).

## TC-002 — Dashboard is reachable only on localhost
- **Story**: all (entry point) · **Traces to**: FR-001 (boundary: no remote serving)
- **Given** the dashboard is running on my machine
- **When** I attempt to reach it through the machine's non-loopback address
- **Then** the connection is refused — the dashboard never listens on a non-local interface
- **Pass condition**: with the dashboard up, `curl -s -m 3 "http://<machine-LAN-IP>:<port>/"` fails or times out while the TC-001 loopback URL still returns `200`. Auto.

## TC-003 — Every indexed project is listed with counts and freshness
- **Story**: US1 · **Traces to**: FR-002, US1-AC1
- **Given** a database with three small sample projects, each indexed at a known time with known file, symbol, and edge counts
- **When** I open the projects view
- **Then** all three projects appear, each row showing file, symbol, and edge counts and the last-indexed time, matching the known values
- **Pass condition**: GUI: open `$DASH` → projects view; each project's name, counts, and timestamp are visible and match the fixture (screenshot). Auto: the projects view's data feed returns exactly the three fixture projects with counts and timestamps equal to the fixture's known values.

## TC-004 — Embedding status distinguishes embedded vs not, and shows the model
- **Story**: US1 · **Traces to**: FR-002, US1-AC2
- **Given** one project embedded with a known embedding model, one project with no embeddings, and one only partially embedded
- **When** I view their rows in the projects view
- **Then** the embedded row shows as embedded with the embedding model name, the un-embedded row shows as not embedded, and the partial row is distinguishable from both
- **Pass condition**: GUI: the three rows show visibly different embedding statuses; the embedded row displays the fixture's model name (screenshot). Auto: the projects data feed marks embedded / not / partial per the fixture and carries the model name where embeddings exist. Regression guard: embedding-model tracking already exists in cairn today (survey Q2) — the fixture uses the normal embedding flow, so this also guards that foundation.

## TC-005 — Projects view on an empty database (empty-input boundary)
- **Story**: US1 · **Traces to**: FR-002 (boundary)
- **Given** a brand-new database with no projects indexed
- **When** I open the projects view
- **Then** a friendly empty state appears (no error, no crash, no blank page)
- **Pass condition**: GUI: screenshot shows an empty-state message. Auto: the projects data feed returns an empty list and the page renders with HTTP `200`.

## TC-006 — Graph renders and supports pan and zoom
- **Story**: US2 · **Traces to**: FR-003, US2-AC1
- **Given** a selected project with a nontrivial structure
- **When** I open its graph view, then drag the canvas and scroll to zoom
- **Then** the graph is rendered and responds to both interactions — it is interactive, not a static picture
- **Pass condition**: auto: the graph data feed for the same scope returns a non-empty node and edge set. GUI walkthrough (browser automation): take a "before" screenshot on open, then simulate a canvas drag and a wheel-scroll, taking an "after" screenshot for each; the pass is a pairwise same-run comparison (after ≠ before in each pair — baseline is the run's own "before" image, not a pre-baked fixture), plus the automation driver reports the canvas transform changed after drag and the zoom level changed after scroll.

## TC-007 — Scope and depth controls change the rendered graph
- **Story**: US2 · **Traces to**: FR-003, US2-AC2
- **Given** the graph view of a project
- **When** I change the scope control (e.g. from repository-wide to a single module) and adjust the depth control
- **Then** the rendered graph updates to match — a narrower scope/depth shows a smaller graph; the scope options offered are the ones cairn's existing visualization layer already defines
- **Pass condition**: GUI: select a narrower scope → visibly fewer nodes (screenshots differ). Auto: requesting the graph data feed under two scope/depth settings returns different node sets. Regression guard: the existing visualization query layer stays green under `uv run pytest` (survey Q3 — its scopes back this control).

## TC-008 — Huge corpus stays responsive (huge-input boundary)
- **Story**: US2 · **Traces to**: FR-003 (boundary), SC-1
- **Given** a project with ten-thousand-plus indexed symbols in a warmed database
- **When** I open its graph view at the default scope
- **Then** the view renders without freezing the browser — the default truncation applies — and the first render completes within the 2-second budget
- **Pass condition**: auto (browser automation): load the view against the large fixture; time from navigation to rendered graph < 2s; the screenshot completes; no unresponsive-page error.

## TC-009 — Every tool invocation is recorded with full context
- **Story**: US3 · **Traces to**: FR-004
- **Given** the MCP server running against a test database
- **When** a known batch of tool calls is made — several succeeding, at least one failing — across two distinct sessions
- **Then** a record exists for every call, each carrying tool name, timestamp, session id, duration, status (ok/error), and request/response payload sizes
- **Pass condition**: auto: after the batch and a flush, the recorded history contains exactly one record per call, every field populated, and the failing call(s) marked as errors. GUI: the history view shows each fixture call (TC-011). Regression guard: buffered recording machinery already exists in the server today (survey Q4) — this case guards that path end-to-end and extends it to payload sizes.

## TC-010 — Recording stays off the hot path (< 5% overhead)
- **Story**: US3 · **Traces to**: FR-004 (boundary), SC-2
- **Given** one tool callable repeatedly with identical arguments
- **When** I measure its latency for N calls with recording in effect and N calls with recording disabled
- **Then** the median added latency from recording is under 5% — recording is buffered, not inline
- **Pass condition**: auto benchmark: median(recorded) / median(unrecorded) < 1.05, reported with the run. Regression guard: the buffering design exists today (survey Q4, Q5).

## TC-011 — History is newest-first with full columns
- **Story**: US3 · **Traces to**: FR-005, US3-AC1
- **Given** recorded calls made at known, distinct times
- **When** I open the history view
- **Then** calls are listed newest-first, each row showing tool name, timestamp, duration, status, and session
- **Pass condition**: GUI: rows appear in reverse chronological order of the fixture with all five fields visible (screenshot). Auto: the history data feed returns the fixture records ordered by timestamp descending, all fields populated.

## TC-012 — History filters by tool name and by session
- **Story**: US3 · **Traces to**: FR-005, US3-AC2
- **Given** the history view showing calls from at least two tools across at least two sessions
- **When** I filter by one tool name, then by one session, then by both together, then by a nonsense value
- **Then** only matching calls remain in each filter case, and the nonsense filter shows a clear "no matching calls" state
- **Pass condition**: GUI: type into the filter controls; the list shrinks to exactly the matches each time (screenshots). Auto: the filtered data feed returns exactly the matching fixture records; a no-match filter returns an empty list with HTTP `200`.

## TC-013 — History on a fresh database (empty-input boundary)
- **Story**: US3 · **Traces to**: FR-005 (boundary)
- **Given** a database with no recorded tool calls
- **When** I open the history view
- **Then** an empty state is shown — no error
- **Pass condition**: GUI: screenshot shows the empty state. Auto: the history data feed returns an empty list and the page renders HTTP `200`.

## TC-014 — Token usage aggregated per tool, ranked by cost
- **Story**: US4 · **Traces to**: FR-006, US4-AC1
- **Given** recorded calls for two tools where tool A's combined payloads are clearly larger than tool B's, with known call counts per tool
- **When** I open the token view
- **Then** each tool shows its call count, total estimated tokens, and mean estimated tokens; rows are ranked by total descending; A ranks above B; and each row is internally consistent (mean x calls ≈ total)
- **Pass condition**: GUI: the aggregate table shows the columns and the correct ranking (screenshot). Auto: the token data feed returns one row per tool with count matching the fixture, ranking by total descending, and mean x count ≈ total within rounding. Note: the estimation **formula** is deliberately not asserted — the spec delegates the method; ranking, monotonicity with payload size, and internal consistency are the observable promise.

## TC-015 — Per-call request/response token estimates on inspection
- **Story**: US4 · **Traces to**: FR-006, US4-AC2
- **Given** one recorded call with known non-zero payloads and one call with empty request and response payloads
- **When** I inspect each call from the history
- **Then** the first shows separate estimated request and response token counts; the empty-payload call shows zero (or clearly minimal) usage without breaking the view
- **Pass condition**: GUI: the call detail shows two separate estimates (screenshot). Auto: the per-call data carries both estimates; the zero-payload call yields zeros with no error.

## TC-016 — Sessions rendered as ordered chains
- **Story**: US5 · **Traces to**: FR-007, US5-AC1
- **Given** recorded calls in two known sessions — one with several calls, one with a single call
- **When** I open the chains view
- **Then** each session appears as its own visually connected chain/timeline with its calls in chronological order; the single-call session still appears as its own short chain
- **Pass condition**: GUI: two chains visible; the multi-call chain's order matches the fixture order (screenshot). Auto: the chains data feed groups calls by session, ordered by time, with both sessions present.

## TC-017 — Inactivity gap splits a session into separate chains
- **Story**: US5 · **Traces to**: FR-007, US5-AC2
- **Given** one session id whose calls arrive in two bursts — three calls a minute apart, then two more six hours later under the same session id
- **When** I open the chains view
- **Then** the two bursts are presented as two separate chains, not one
- **Pass condition**: auto: the chains feed splits that session id into two chains at the gap. GUI: two visually separate chains (screenshot). The fixture gap (6 hours vs 1 minute) is far beyond any plausible inactivity threshold, so the assertion does not depend on the exact threshold value.

## TC-018 — Health panel shows everything in one place
- **Story**: US6 · **Traces to**: FR-008, US6-AC1
- **Given** a database of known size with a known last-index time
- **When** I open the health view
- **Then** database size, index freshness, and vector/reranker backend availability all appear on one panel
- **Pass condition**: GUI: all three visible in one view (screenshot). Auto: the health data feed exposes the three values, and they agree with `uv run cairn doctor` run against the same database. Regression guard: the doctor command already provides these checks today (survey Q7) — the panel must agree with it, not invent new health semantics.

## TC-019 — Memory panel lists recent memories with type and title
- **Story**: US7 · **Traces to**: FR-009, US7-AC1
- **Given** several memories recorded with distinct types and titles via the existing memory command
- **When** I open the memory panel
- **Then** those memories are listed showing type and title, most recent first
- **Pass condition**: fixture via `uv run cairn memory record ...`; GUI: each fixture memory's type and title visible (screenshot). Auto: the memory panel data feed returns the fixture entries with type and title.

## TC-020 — Task queue panel lists entries by status
- **Story**: US7 · **Traces to**: FR-009, US7-AC2
- **Given** task-queue entries in pending, in-progress/claimed, and completed states, created via the existing task commands
- **When** I open the tasks panel
- **Then** entries are listed with their status, visibly separated or filterable by status
- **Pass condition**: fixture via `uv run cairn task ...` commands; GUI: all three statuses visible and grouped (screenshot). Auto: the tasks data feed returns each fixture entry tagged with the correct status.

## TC-021 — Standing guard: the dashboard never mutates the database
- **Story**: all · **Traces to**: FR-010 (standing regression)
- **Given** the dashboard running against a checksummed copy of a populated database
- **When** I exercise every view end-to-end — open all seven views, pan and zoom the graph, apply every history filter, change scope and depth — then stop the dashboard
- **Then** the database file's checksum is unchanged and the recorded tool-call count is unchanged: the dashboard wrote nothing. (The only writer in the system is the server's own tool-call recording, never the dashboard.)
- **Pass condition**: auto: checksum the fixture database before and after a scripted full-view pass (e.g. `shasum <fixture.db>`); the hashes are identical and the tool-call record count is identical. This case fails if any write ever creeps into the dashboard's default path. Regression guard: read-only opening already exists in cairn today (survey Q11) — this guards that the dashboard actually uses it.

## TC-022 — Clean shutdown loses nothing (flush completeness)
- **Story**: US3 · **Traces to**: FR-011, SC-3
- **Given** a running server with tool calls just made whose records may still be sitting in the buffer (before any periodic flush)
- **When** the process is shut down cleanly
- **Then** every call that had returned before shutdown is present in the recorded history — no silent drops (calls still in flight when shutdown began are not counted as drops)
- **Pass condition**: auto: make K calls, immediately stop the process cleanly, then count persisted records — exactly K present. Regression guard: flush-on-exit machinery already exists today (survey Q5) — this guards it for tool-call records specifically.

## TC-023 — First render under 2 seconds on a warmed database
- **Story**: all · **Traces to**: FR-001, SC-1
- **Given** a warmed database of realistic size and the dashboard running
- **When** I load each of the five core views (projects, graph, history, tokens, chains) for the first time
- **Then** each view's first render completes in under 2 seconds
- **Pass condition**: auto (browser automation): time navigation → rendered content per view; every measurement < 2s; report the five timings.

## TC-024 — Standing guard: history shows argument summaries, not full payloads
- **Story**: US3 · **Traces to**: FR-005 (standing guard on the spec's stated default)
- **Given** a recorded tool call whose arguments embed a large, uniquely identifiable code snippet
- **When** I view that call in the history (list and detail)
- **Then** I see a truncated summary of the arguments — the full payload is not displayed by default
- **Pass condition**: auto/GUI: the distinctive tail of the snippet is absent from both the rendered page and the history data feed's default response (screenshot plus feed check).

## TC-025 — Dashboard reads while the server writes (concurrency boundary)
- **Story**: all · **Traces to**: FR-004, FR-010 (boundary)
- **Given** the dashboard open while the MCP server concurrently records new tool calls against the same database
- **When** new calls land and I refresh the dashboard's views
- **Then** every view renders without errors or lock complaints, and the newly recorded calls appear after refresh
- **Pass condition**: auto: run a writer loop making tool calls against the test database while scriptedly fetching every view's data feed; all fetches return HTTP `200` with valid content, and the history feed eventually includes the new records; no lock or busy error surfaces.

## Coverage matrix
<!-- Every FR appears; `check.py` fails an FR with no TC. -->
| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001      | TC-001, TC-002, TC-023 | auto + GUI |
| FR-002      | TC-003, TC-004, TC-005 | auto + GUI |
| FR-003      | TC-006, TC-007, TC-008 | GUI + auto |
| FR-004      | TC-009, TC-010, TC-025 | auto |
| FR-005      | TC-011, TC-012, TC-013, TC-024 | auto + GUI |
| FR-006      | TC-014, TC-015 | auto + GUI |
| FR-007      | TC-016, TC-017 | auto + GUI |
| FR-008      | TC-018 | auto + GUI |
| FR-009      | TC-019, TC-020 | auto + GUI |
| FR-010      | TC-021, TC-025 | auto (standing) |
| FR-011      | TC-022 | auto |

No FR is untestable; the matrix is complete.

## Acceptance-criteria trace
| Story / AC | TCs |
|------------|-----|
| US1-AC1 | TC-003 |
| US1-AC2 | TC-004 |
| US2-AC1 | TC-006 |
| US2-AC2 | TC-007 |
| US3-AC1 | TC-011 |
| US3-AC2 | TC-012 |
| US4-AC1 | TC-014 |
| US4-AC2 | TC-015 |
| US5-AC1 | TC-016 |
| US5-AC2 | TC-017 |
| US6-AC1 | TC-018 |
| US7-AC1 | TC-019 |
| US7-AC2 | TC-020 |

## Success-criteria trace
| SC | TCs |
|----|-----|
| SC-1 (first render < 2s, read-only, five views) | TC-023, TC-008, TC-021 |
| SC-2 (recording adds < 5% latency) | TC-010 |
| SC-3 (100% of returned calls survive shutdown) | TC-022 |

## Notes for the orchestrator
- FR-006 is asserted method-agnostically (ranking, monotonicity with payload
  size, internal consistency) because the spec delegates the estimation
  formula to the tech spec; pinning a formula here would encode the
  solution rather than the promise.
- Automated pass conditions that reference "the view's data feed" assert on
  JSON content; the concrete routes bind at automation time (see
  Conventions). GUI forms are executable today as browser walkthroughs with
  screenshot comparison.
- Regression-guard cases (TC-004, TC-007, TC-009, TC-010, TC-018, TC-021,
  TC-022) anchor on capabilities the survey verified as already present
  (survey items Q2, Q3, Q4, Q5, Q7, Q11); their existing suites under
  `uv run pytest` are part of each pass condition.

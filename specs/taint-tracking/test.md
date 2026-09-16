# Test Cases: taint-tracking

**Spec**: [spec.md](spec.md) | **Created**: 2026-09-17
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details.

## Conventions

- All pass-condition commands run from the repository root against the
  self-contained fixture workspaces defined below; each completes in seconds.
- Assertions key on stable output tokens — fixture symbol names, the spec's
  resolution labels (`exact`, `ambiguous`), and the word "taint" in warning
  badges — never on prose wording.
- Source/sink patterns name the requirement's categories. Custom fixtures
  declare their own labels via workspace config, so their query tokens are
  exact by construction; default-set queries use the category names from the
  requirement.
- Exit status of each auto pass-condition command is the verdict: success
  means every assertion held.

## Fixtures

Each fixture is a self-contained mini workspace with known flows:

- `sql-flow` — `handle_request` reads an HTTP request parameter and passes it
  through `validate_input` into `run_query`, which executes it as SQL. Three
  inter-procedural hops; every hop resolves to exactly one same-repo
  definition (`exact`).
- `ambiguous-hop` — `handle_upload` reads an HTTP request parameter and calls
  `transform`, a name defined twice in the workspace (`ambiguous` hop), which
  passes the value to `run_migration`, which executes it as SQL.
- `clean-workspace` — same shape as `sql-flow` but the terminal function is
  `format_report`, ordinary string building. No sink exists.
- `custom-config` — `consume_job` receives a work-queue message and passes the
  payload to `render_invoice`, which renders a template. No default source or
  sink matches; the workspace config declares source `queue-msg` and sink
  `render`.
- `framework-only` — `checkout_handler` reads the request body and persists it
  via `save_order` using only framework-idiomatic calls that no generic
  call-name key in the shipped default set matches.
- `change-intersect` — `sql-flow` plus `format_label`, a pure helper that
  touches no taint path.
- `cli-shell` — `main` reads a CLI argument and passes it to `run_command`,
  which executes it in a shell.
- `category-matrix` — ten micro-flows covering the default set: each of the
  five default source categories into a shell-executing sink, and the
  HTTP-parameter source into each of the five default sinks.

## TC-001 — Known SQL-injection flow traces end to end on defaults
- **Story**: US1 · **Traces to**: FR-002, FR-001, AC1
- **Given** the `sql-flow` fixture with no taint configuration
- **When** the taint trace runs from the HTTP-parameter source category to the SQL sink category
- **Then** the full three-hop path prints, naming the reading, forwarding, and executing functions, and every hop carries its resolution label
- **Pass condition**: `cd specs/taint-tracking/fixtures/sql-flow && cairn taint --from http --to sql | tee /tmp/tc001.out >/dev/null && grep -q handle_request /tmp/tc001.out && grep -q validate_input /tmp/tc001.out && grep -q run_query /tmp/tc001.out && grep -q exact /tmp/tc001.out`

## TC-002 — Config-declared source and sink trace a flow outside the defaults
- **Story**: US1 · **Traces to**: FR-001
- **Given** the `custom-config` fixture whose workspace config declares the `queue-msg` source and the `render` sink
- **When** the taint trace runs between those two labels
- **Then** the queue-to-template path prints naming both functions
- **Pass condition**: `cd specs/taint-tracking/fixtures/custom-config && cairn taint --from queue-msg --to render | tee /tmp/tc002.out >/dev/null && grep -q consume_job /tmp/tc002.out && grep -q render_invoice /tmp/tc002.out`

## TC-003 — Default propagation crosses exact edges only
- **Story**: US1 · **Traces to**: FR-003
- **Given** the `ambiguous-hop` fixture whose middle hop resolves to two candidates
- **When** the trace runs with default precision
- **Then** no path is reported, because the ambiguous hop is not traversed
- **Pass condition**: `cd specs/taint-tracking/fixtures/ambiguous-hop && ! cairn taint --from http --to sql | grep -q run_migration`

## TC-004 — Fuzzy opt-in crosses the ambiguous hop and labels it
- **Story**: US1 · **Traces to**: FR-003
- **Given** the `ambiguous-hop` fixture
- **When** the trace runs with the fuzzy opt-in flag
- **Then** the full path prints and the ambiguous hop is labeled with its resolution label
- **Pass condition**: `cd specs/taint-tracking/fixtures/ambiguous-hop && cairn taint --from http --to sql --fuzzy | tee /tmp/tc004.out >/dev/null && grep -q run_migration /tmp/tc004.out && grep -q ambiguous /tmp/tc004.out`

## TC-005 — Clean workspace yields zero paths, default and fuzzy
- **Story**: US1 · **Traces to**: FR-002, FR-003
- **Given** the `clean-workspace` fixture where the parameter reaches no sink
- **When** the trace runs by default and again with the fuzzy opt-in
- **Then** no path is reported either time — zero false positives on clean code
- **Pass condition**: `cd specs/taint-tracking/fixtures/clean-workspace && ! cairn taint --from http --to sql | grep -q format_report && ! cairn taint --from http --to sql --fuzzy | grep -q format_report`

## TC-006 — Unmatched pattern degrades gracefully
- **Story**: US1 · **Traces to**: FR-002
- **Given** the `sql-flow` fixture
- **When** the trace runs with a source pattern that matches nothing
- **Then** no path prints and the command ends cleanly with no crash output
- **Pass condition**: `cd specs/taint-tracking/fixtures/sql-flow && ! cairn taint --from no-such-source --to sql | grep -q run_query && ! cairn taint --from no-such-source --to sql 2>&1 | grep -qi traceback`

## TC-007 — Blast warns when a change intersects a taint path
- **Story**: US2 · **Traces to**: FR-004, AC1
- **Given** the `change-intersect` fixture
- **When** blast runs for the SQL-executing function on the path, then for the off-path helper
- **Then** the on-path output carries a taint warning naming the path; the off-path output carries no warning
- **Pass condition**: `cd specs/taint-tracking/fixtures/change-intersect && cairn blast run_query | tee /tmp/tc007.out >/dev/null && grep -qi taint /tmp/tc007.out && ! cairn blast format_label | grep -qi taint`

## TC-008 — Explore warns when queried code intersects a taint path
- **Story**: US2 · **Traces to**: FR-004, AC1
- **Given** the `change-intersect` fixture
- **When** the explore tool is queried from an MCP client for the SQL-executing function on the path, then for the off-path helper
- **Then** the on-path response carries a taint warning naming the path; the off-path response carries no warning
- **Pass condition**: human observation — in an MCP client, query explore for the SQL-executing function in the change-intersect fixture and verify the response contains a taint warning naming the path; repeat for the off-path helper and verify no warning appears

## TC-009 — Standing guard: framework-only flows stay unflagged by defaults
- **Story**: US1 · **Traces to**: FR-005
- **Given** the `framework-only` fixture whose flow is recognizable only through framework-specific entry and sink idioms
- **When** the trace runs on defaults, including the fuzzy opt-in
- **Then** no path is reported — the default set stays generic and framework-aware packs remain out of it; this case fails if framework packs ever ship into the default set
- **Pass condition**: `cd specs/taint-tracking/fixtures/framework-only && ! cairn taint --from http --to sql | grep -q save_order && ! cairn taint --from http --to sql --fuzzy | grep -q save_order`

## TC-010 — Second default category pair traces with no config
- **Story**: US1 · **Traces to**: FR-001, FR-005
- **Given** the `cli-shell` fixture with no taint configuration
- **When** the trace runs from the CLI-argument source category to the shell sink category
- **Then** the path prints naming both functions with exact-hop labels — defaults generalize beyond any single category pair
- **Pass condition**: `cd specs/taint-tracking/fixtures/cli-shell && cairn taint --from cli --to shell | tee /tmp/tc010.out >/dev/null && grep -q main /tmp/tc010.out && grep -q run_command /tmp/tc010.out && grep -q exact /tmp/tc010.out`

## TC-011 — Default set covers every category the requirement names
- **Story**: US1 · **Traces to**: FR-001, FR-005
- **Given** the `category-matrix` fixture
- **When** each default category named in the requirement is probed — every source category into a shell sink, and the HTTP-parameter source into every sink category
- **Then** every probe traces a path; a category that yields no path on a correctly shaped flow is a default-set coverage gap
- **Pass condition**: human observation — run one trace probe per category pair on the category-matrix fixture, ten probes total, and verify each prints a path; any probe that returns nothing on its correctly shaped flow fails this case

## TC-012 — Standing guard: scale never breaks the trace
- **Story**: US1 · **Traces to**: FR-002
- **Given** a large production workspace
- **When** the default sweep and the fuzzy sweep run over it
- **Then** both complete without error or hang and render well-formed results regardless of whether paths are found
- **Pass condition**: human observation — from a large production workspace root, run the default trace sweep and the fuzzy sweep, and verify both finish cleanly with well-formed output; a crash, hang, or malformed result on scale fails this case

## Coverage matrix
<!-- Every FR appears; `check.py` fails an FR with no TC. -->
| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001      | TC-001, TC-002, TC-010, TC-011 | auto + manual |
| FR-002      | TC-001, TC-005, TC-006, TC-012 | auto + manual |
| FR-003      | TC-003, TC-004, TC-005 | auto |
| FR-004      | TC-007, TC-008 | auto + manual |
| FR-005      | TC-009, TC-010, TC-011 | auto + manual |

Untestable requirements: none.

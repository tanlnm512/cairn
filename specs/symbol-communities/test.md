# Test Cases: symbol-communities

**Spec**: [spec.md](spec.md) | **Created**: 2026-10-04
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details.
"Prepared workspace" = a workspace registered with the store whose graph is
built (`cairn build`) over a small code fixture sized to finish in seconds.

## TC-001 — One command computes, persists, and summarizes communities
- **Story**: US1 · **Traces to**: FR-001 (AC1)
- **Given** a prepared workspace whose graph contains code relations between its symbols
- **When** the user runs `cairn communities`
- **Then** the command succeeds and the summary prints the number of communities found, the size of each, and the top hub symbols; the results are stored so later consumers (compass guide, dashboard view — TC-009, TC-012) read them without recomputing
- **Pass condition**: `cairn communities` exits 0 in the prepared workspace and its output states a community count, a size per community, and named hub symbols

## TC-002 — Re-running on an unchanged graph reproduces the result exactly
- **Story**: US1 · **Traces to**: FR-007 (AC2)
- **Given** a prepared workspace where `cairn communities` has already run once and nothing in the code changed since
- **When** the command runs a second time
- **Then** the reported partition, community labels, ordering, and hub lists are identical to the first run — the printed output matches byte for byte
- **Pass condition**: `cairn communities > run1.txt && cairn communities > run2.txt && diff run1.txt run2.txt` exits 0 (no differences) in the prepared workspace

## TC-003 — Re-running after the code changes replaces stale results
- **Story**: US1 · **Traces to**: FR-001 (AC1)
- **Given** a prepared workspace where `cairn communities` has already run, after which a real code relation is added between previously unrelated symbols and the graph is rebuilt
- **When** the command runs again on the rebuilt graph
- **Then** the results equal what a fresh compute of that same rebuilt graph produces — byte-identical to the output from an identical twin workspace built from scratch with the added relation already present — so no membership from the pre-change run survives unless the fresh compute also reports it
- **Pass condition**: `cairn communities` on the rebuilt workspace and on the twin workspace (same fixture built from scratch with the added relation) both exit 0 with byte-identical output, and a further run on the unchanged rebuilt workspace reproduces that output exactly

## TC-004 — A graph with symbols but no code relations degrades, not crashes
- **Story**: US1 · **Traces to**: FR-001
- **Given** a built workspace whose graph has symbols but zero code relations (e.g. prose-only sources)
- **When** the user runs `cairn communities`
- **Then** the command succeeds, reports that no communities were found (or an empty listing), and exits 0 — no traceback, no error exit
- **Pass condition**: `cairn communities` exits 0 on the relation-free workspace and its output contains no error text

## TC-005 — Import-only bridges: stable partition, converging refresh
- **Story**: US1 · **Traces to**: FR-002
- **Given** a fixture of two groups whose members call only each other, with the single cross-group link being an import-style reference rather than a call or inheritance relation
- **When** `cairn communities` runs on the fixture twice back-to-back, and once more after a full rebuild of the unchanged graph
- **Then** every run succeeds and reports a partition covering the symbols, the same one every time — whatever partition the import-only link yields is stable across runs, and the full-refresh path converges to it
- **Pass condition**: three `cairn communities` runs on the import-bridged fixture (two back-to-back, one after a fresh rebuild) all exit 0 with byte-identical output

## TC-006 — Hubs rank by combined structural degree, globally and per community, top ten
- **Story**: US1 · **Traces to**: FR-003
- **Given** a prepared fixture where one shared routine receives calls from across the fixture and also calls out to several others, giving it the highest combined degree of any symbol
- **When** `cairn communities` runs
- **Then** that routine is listed first in the global hub list; the global list shows at most ten hubs; every reported community names its own hub; on a fixture with fewer than ten hub candidates all are listed with no error or padding
- **Pass condition**: `cairn communities` output on the fixture starts its global hub list with the highest-degree routine, contains at most ten global hubs, and shows a hub per community line

## TC-007 — Community labels come from member facts with no model involved
- **Story**: US1 · **Traces to**: FR-008
- **Given** a prepared workspace and an environment with no model provider credentials configured
- **When** `cairn communities` runs
- **Then** every community carries a readable label derived from its own members' facts (e.g. its top hub's name or the members' dominant source area), and no label is blank, a placeholder, or an error
- **Pass condition**: with model credentials absent from the environment, `cairn communities` exits 0 and every community in the output has a label matching a member hub name or shared member path area (standing guard: fails if labels ever need a model or go blank)

## TC-008 — Without the analytics extra: clear failure, store untouched
- **Story**: US4 · **Traces to**: FR-004 (AC1)
- **Given** an environment installed without the graph-analytics extra, pointing at a built store
- **When** the user runs `cairn communities`
- **Then** the command exits non-zero with a message naming the missing extra and the exact way to add it; the store is byte-for-byte unchanged — a checksum taken before and after the failed run is identical, and no community data appears
- **Pass condition**: `cairn communities` exits non-zero, prints an install hint naming the graph-analytics extra, and the store database checksum is identical before and after the run (standing guard: fails if the default install ever grows a hard dependency on the extra)

## TC-009 — Compass guide carries Subsystems when community data exists
- **Story**: US2 · **Traces to**: FR-005 (AC1)
- **Given** a prepared workspace where `cairn communities` has already run
- **When** a compass guide for any workspace module is generated twice on the deterministic (no-model) path, each generation running its built-in quality review
- **Then** both guides contain a "Subsystems" section listing the communities with representative hub symbols, the two outputs are identical, and the quality review accepts the guide — the new section counts as recognized content
- **Pass condition**: `cairn compass generate MODULE --dry-run` run twice (MODULE = any module of the workspace) exits 0 both times, both outputs contain the "Subsystems" heading with hub names, and the outputs are identical

## TC-010 — Compass guide omits Subsystems when no community data exists
- **Story**: US2 · **Traces to**: FR-005 (AC2)
- **Given** a built workspace on which `cairn communities` has never run
- **When** a compass guide is generated on the deterministic path
- **Then** generation succeeds, the guide has no "Subsystems" section, and the rest of the guide is unchanged from its usual shape
- **Pass condition**: `cairn compass generate MODULE --dry-run` exits 0 and its output contains no "Subsystems" heading (standing guard: fails if absent community data ever breaks guide generation)

## TC-011 — Nav inventory registers the view exactly once at the new counts
- **Story**: US3 · **Traces to**: FR-006 (AC2)
- **Given** the dashboard with the communities view added to its navigation
- **When** every view-inventory check runs (registration counts, icon per view, single active nav item, local assets only)
- **Then** all pass with the communities view registered exactly once: the nav inventory holds 15 view ids and the main-view crawl covers 17 paths — each exactly one larger than before the view existed
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_dashboard_shell.py tests/test_dashboard_restyle.py tests/test_dashboard_assets.py tests/test_dashboard_readonly.py -q` passes with the nav inventory pinned at 15 ids and the crawled main-view set at 17 paths, the communities view included exactly once

## TC-012 — Dashboard walkthrough: communities view with member drill-down
- **Story**: US3 · **Traces to**: FR-006 (AC1)
- **Given** a prepared workspace where `cairn communities` has run and the dashboard is serving (environment prep: `cairn dashboard --port 8791`)
- **When** a tester opens the printed URL in a browser, clicks the Communities entry in the side navigation, then opens one community's drill-down
- **Then** the view renders the communities with their sizes and hubs, matching the command summary (TC-001); the drill-down lists that community's member symbols; at every step a read-only DOM check and a screenshot show the same content, with no layout defects
- **Pass condition**: manual GUI walkthrough (browser) — click the nav entry, screenshot the rendered communities (sizes and hubs visible), open a member drill-down, screenshot the member list; each step's DOM read must corroborate its screenshot

## TC-013 — The new view passes the existing accessibility crawl
- **Story**: US3 · **Traces to**: NFR-006
- **Given** the dashboard with the communities view registered in the crawled view set
- **When** the accessibility inventory runs over every main view including the new one
- **Then** it passes: visible focus rings, no removed outlines, non-empty accessible names on all buttons, reduced-motion support, and real table semantics on the new view's listings
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_dashboard_accessibility.py -q` passes with the communities view included in the crawl (regression guard; the crawl is green today and must stay green one view larger)

## TC-014 — Communities completes in seconds; budget gated at scale
- **Story**: US1 · **Traces to**: NFR-003
- **Given** any built workspace store
- **When** `cairn communities` runs
- **Then** it completes in seconds, not minutes; at scale the standing budget holds — on the 1000-file generated corpus the command completes within 10 seconds wall
- **Pass condition**: `cairn communities` exits 0 on a built store well inside the runner cap (bounded proof); standing at-scale verify: the scaling budget gate run in full, `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_scaling_gate.py -m "not infra" -q`, asserts the communities wall time ≤ 10 s at the 1000-file gate point

## Coverage matrix
<!-- Every FR and applicable NFR appears; `check.py` fails a requirement with no TC. -->
| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001      | TC-001, TC-003, TC-004 | auto |
| FR-002      | TC-005     | auto               |
| FR-003      | TC-006     | auto               |
| FR-004      | TC-008     | auto               |
| FR-005      | TC-009, TC-010 | auto           |
| FR-006      | TC-011, TC-012 | auto + manual  |
| FR-007      | TC-002     | auto               |
| FR-008      | TC-007     | auto               |
| NFR-003     | TC-014     | auto (bounded; at-scale verify named in Then) |
| NFR-006     | TC-013     | auto               |
| NFR-001     | —          | n/a per spec (no new network or auth surface) |
| NFR-002     | —          | n/a per spec (no new data leaves the store) |
| NFR-004     | —          | n/a per spec (degrade behavior covered inside TC-004, TC-010) |
| NFR-005     | —          | n/a per spec (existing telemetry covers CLI runs) |

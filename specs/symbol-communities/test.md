# Test Cases: symbol-communities

**Spec**: [spec.md](spec.md) | **Created**: 2026-10-04
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details.
"Prepared workspace" = a workspace registered with the store whose graph is
built (`cairn build`) over a small code fixture sized to finish in seconds.

Fixture contract (test assets this suite reuses from
`tests/fixtures/on-demand-paths/`, provisioned idempotently by its
`provision.sh`, which also resets mutation state; all paths repo-root-relative,
each command finishing well under the 120 s audit cap): `fork/` (`hub_top` ->
`mid_left`/`mid_right` -> `hub_sink`) backs the hub, label, and determinism
checks; `islands/` (two disjoint components) backs the multi-partition checks;
TC-004 builds its relation-free workspace inline. Stores are throwaway
`/tmp/comm-tcNNN.db` files. All auto commands are single-line.

## TC-001 — One command computes, persists, and summarizes communities
- **Story**: US1 · **Traces to**: FR-001 (AC1)
- **Given** a prepared workspace whose graph contains code relations between its symbols
- **When** the user runs `cairn communities`
- **Then** the command succeeds and the summary prints the number of communities found, the size of each, and the top hub symbols; the results are stored so later consumers (compass guide, dashboard view — TC-009, TC-012) read them without recomputing
- **Pass condition**: `sh tests/fixtures/on-demand-paths/provision.sh && rm -f /tmp/comm-tc001.db /tmp/comm-tc001.out && uv run --no-sync cairn build --workspace tests/fixtures/on-demand-paths/fork --db /tmp/comm-tc001.db > /dev/null && uv run --no-sync cairn communities --db /tmp/comm-tc001.db > /tmp/comm-tc001.out && grep -qE '^[0-9]+ communities$' /tmp/comm-tc001.out && grep -qE '\([0-9]+ symbols\)$' /tmp/comm-tc001.out && grep -qE '^  [a-z_]+ \(degree [0-9]+\)$' /tmp/comm-tc001.out && [ "$(sqlite3 /tmp/comm-tc001.db 'select count(*) from communities')" -gt 0 ] && [ "$(sqlite3 /tmp/comm-tc001.db 'select count(*) from symbol_communities')" -gt 0 ]`

## TC-002 — Re-running on an unchanged graph reproduces the result exactly
- **Story**: US1 · **Traces to**: FR-007 (AC2)
- **Given** a prepared workspace where `cairn communities` has already run once and nothing in the code changed since
- **When** the command runs a second time
- **Then** the reported partition, community labels, ordering, and hub lists are identical to the first run — the printed output matches byte for byte
- **Pass condition**: `sh tests/fixtures/on-demand-paths/provision.sh && rm -f /tmp/comm-tc002.db /tmp/comm-tc002-a.out /tmp/comm-tc002-b.out && uv run --no-sync cairn build --workspace tests/fixtures/on-demand-paths/fork --db /tmp/comm-tc002.db > /dev/null && uv run --no-sync cairn communities --db /tmp/comm-tc002.db > /tmp/comm-tc002-a.out && uv run --no-sync cairn communities --db /tmp/comm-tc002.db > /tmp/comm-tc002-b.out && diff /tmp/comm-tc002-a.out /tmp/comm-tc002-b.out`

## TC-003 — Re-running after the code changes replaces stale results
- **Story**: US1 · **Traces to**: FR-001 (AC1)
- **Given** a prepared workspace where `cairn communities` has already run, after which a real code relation is added between previously unrelated symbols and the graph is rebuilt
- **When** the command runs again on the rebuilt graph
- **Then** the results equal what a fresh compute of that same rebuilt graph produces — byte-identical to the output from an identical twin workspace built from scratch with the added relation already present — so no membership from the pre-change run survives unless the fresh compute also reports it
- **Pass condition**: `sh tests/fixtures/on-demand-paths/provision.sh && rm -f /tmp/comm-tc003-a.db /tmp/comm-tc003-b.db /tmp/comm-tc003-a.out /tmp/comm-tc003-b.out /tmp/comm-tc003-a2.out && uv run --no-sync cairn build --workspace tests/fixtures/on-demand-paths/fork --db /tmp/comm-tc003-a.db > /dev/null && uv run --no-sync cairn build --workspace tests/fixtures/on-demand-paths/fork --db /tmp/comm-tc003-b.db > /dev/null && uv run --no-sync cairn communities --db /tmp/comm-tc003-a.db > /tmp/comm-tc003-a.out && uv run --no-sync cairn communities --db /tmp/comm-tc003-b.db > /tmp/comm-tc003-b.out && diff /tmp/comm-tc003-a.out /tmp/comm-tc003-b.out && uv run --no-sync cairn communities --db /tmp/comm-tc003-a.db > /tmp/comm-tc003-a2.out && diff /tmp/comm-tc003-a.out /tmp/comm-tc003-a2.out` (the rebuilt store vs its identical fresh-built twin, plus a further run on the rebuilt store — build-stability, the same contract the pytest owner pins)

## TC-004 — A graph with symbols but no code relations degrades, not crashes
- **Story**: US1 · **Traces to**: FR-001
- **Given** a built workspace whose graph has symbols but zero code relations (e.g. prose-only sources)
- **When** the user runs `cairn communities`
- **Then** the command succeeds, reports that no communities were found (or an empty listing), and exits 0 — no traceback, no error exit
- **Pass condition**: `rm -rf /tmp/comm-tc004-ws && mkdir -p /tmp/comm-tc004-ws/.git && printf 'def a():\n    return 1\n\n\ndef b():\n    return 2\n' > /tmp/comm-tc004-ws/solo.py && rm -f /tmp/comm-tc004.db /tmp/comm-tc004.out && uv run --no-sync cairn build --workspace /tmp/comm-tc004-ws --db /tmp/comm-tc004.db > /dev/null && uv run --no-sync cairn communities --db /tmp/comm-tc004.db > /tmp/comm-tc004.out 2>&1 && grep -q 'no communities found' /tmp/comm-tc004.out && ! grep -qi traceback /tmp/comm-tc004.out`

## TC-005 — Import-only bridges: stable partition, converging refresh
- **Story**: US1 · **Traces to**: FR-002
- **Given** a fixture of two groups whose members call only each other, with the single cross-group link being an import-style reference rather than a call or inheritance relation
- **When** `cairn communities` runs on the fixture twice back-to-back, and once more after a full rebuild of the unchanged graph
- **Then** every run succeeds and reports a partition covering the symbols, the same one every time — whatever partition the import-only link yields is stable across runs, and the full-refresh path converges to it
- **Pass condition**: `sh tests/fixtures/on-demand-paths/provision.sh && rm -f /tmp/comm-tc005-a.db /tmp/comm-tc005-b.db /tmp/comm-tc005-1.out /tmp/comm-tc005-2.out /tmp/comm-tc005-3.out && uv run --no-sync cairn build --workspace tests/fixtures/on-demand-paths/islands --db /tmp/comm-tc005-a.db > /dev/null && uv run --no-sync cairn communities --db /tmp/comm-tc005-a.db > /tmp/comm-tc005-1.out && uv run --no-sync cairn communities --db /tmp/comm-tc005-a.db > /tmp/comm-tc005-2.out && uv run --no-sync cairn build --workspace tests/fixtures/on-demand-paths/islands --db /tmp/comm-tc005-b.db > /dev/null && uv run --no-sync cairn communities --db /tmp/comm-tc005-b.db > /tmp/comm-tc005-3.out && diff /tmp/comm-tc005-1.out /tmp/comm-tc005-2.out && diff /tmp/comm-tc005-1.out /tmp/comm-tc005-3.out`

## TC-006 — Hubs rank by combined structural degree, globally and per community, top ten
- **Story**: US1 · **Traces to**: FR-003
- **Given** a prepared fixture where one shared routine receives calls from across the fixture and also calls out to several others, giving it the highest combined degree of any symbol
- **When** `cairn communities` runs
- **Then** that routine is listed first in the global hub list; the global list shows at most ten hubs; every reported community names its own hub; on a fixture with fewer than ten hub candidates all are listed with no error or padding
- **Pass condition**: `sh tests/fixtures/on-demand-paths/provision.sh && rm -f /tmp/comm-tc006.db /tmp/comm-tc006.out && uv run --no-sync cairn build --workspace tests/fixtures/on-demand-paths/fork --db /tmp/comm-tc006.db > /dev/null && uv run --no-sync cairn communities --db /tmp/comm-tc006.db > /tmp/comm-tc006.out && grep -A1 '^Global hubs:' /tmp/comm-tc006.out | tail -n 1 | grep -q ' (degree 2)$' && [ "$(sed -n '/^Global hubs:/,/^Communities:/p' /tmp/comm-tc006.out | grep -c '(degree ')" -le 10 ] && awk '/\([0-9]+ symbols\)$/{n++;p=1;next} p && !/\(degree [0-9]+\)$/{bad=1} {p=0} END{exit (!bad && n>0)?0:1}' /tmp/comm-tc006.out`

## TC-007 — Community labels come from member facts with no model involved
- **Story**: US1 · **Traces to**: FR-008
- **Given** a prepared workspace and an environment with no model provider credentials configured
- **When** `cairn communities` runs
- **Then** every community carries a readable label derived from its own members' facts (e.g. its top hub's name or the members' dominant source area), and no label is blank, a placeholder, or an error
- **Pass condition**: `sh tests/fixtures/on-demand-paths/provision.sh && rm -f /tmp/comm-tc007.db /tmp/comm-tc007.out && uv run --no-sync cairn build --workspace tests/fixtures/on-demand-paths/fork --db /tmp/comm-tc007.db > /dev/null && env -u OPENAI_API_KEY -u CAIRN_EMBED_API_KEY uv run --no-sync cairn communities --db /tmp/comm-tc007.db > /tmp/comm-tc007.out && grep -qE '^[0-9]+ communities$' /tmp/comm-tc007.out && python3 -c 'import re,sys; ls=[l.rstrip("\n") for l in open("/tmp/comm-tc007.out") if re.match(r"^  \d+: .+ \(\d+ symbols\)$", l.rstrip("\n"))]; sys.exit(0 if ls and all(re.match(r"^  \d+: (?:[A-Za-z0-9_./-]+|hub:\S+) \(\d+ symbols\)$", l) for l in ls) else 1)'` (standing guard: fails if labels ever need a model or go blank)

## TC-008 — Without the analytics extra: clear failure, store untouched
- **Story**: US4 · **Traces to**: FR-004 (AC1)
- **Given** an environment installed without the graph-analytics extra, pointing at a built store
- **When** the user runs `cairn communities`
- **Then** the command exits non-zero with a message naming the missing extra and the exact way to add it; the store is byte-for-byte unchanged — a checksum taken before and after the failed run is identical, and no community data appears
- **Pass condition**: `mkdir -p /tmp/comm-block && printf 'raise ImportError("blocked")\n' > /tmp/comm-block/networkx.py && sh tests/fixtures/on-demand-paths/provision.sh && rm -f /tmp/comm-tc008.db /tmp/comm-tc008.out && uv run --no-sync cairn build --workspace tests/fixtures/on-demand-paths/fork --db /tmp/comm-tc008.db > /dev/null && before=$(shasum -a 256 /tmp/comm-tc008.db | cut -d ' ' -f 1) && ! PYTHONPATH=/tmp/comm-block uv run --no-sync cairn communities --db /tmp/comm-tc008.db > /tmp/comm-tc008.out 2>&1 && grep -q 'graph-analytics' /tmp/comm-tc008.out && [ "$(shasum -a 256 /tmp/comm-tc008.db | cut -d ' ' -f 1)" = "$before" ]` (standing guard: fails if the default install ever grows a hard dependency on the extra)

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
- **Pass condition**: `sh tests/fixtures/on-demand-paths/provision.sh && rm -f /tmp/comm-tc014.db && uv run --no-sync cairn build --workspace tests/fixtures/on-demand-paths/fork --db /tmp/comm-tc014.db > /dev/null && uv run --no-sync python -c 'import subprocess,time; t=time.monotonic(); subprocess.run(["uv","run","--no-sync","cairn","communities","--db","/tmp/comm-tc014.db"],check=True,capture_output=True); d=time.monotonic()-t; assert d < 60.0, d'` (bounded proof; standing at-scale verify beyond the audit's 120 s cap: the scaling budget gate run in full, `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_scaling_gate.py -m "not infra" -q`, asserts the communities wall time ≤ 10 s at the 1000-file gate point)

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

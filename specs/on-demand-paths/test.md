# Test Cases: on-demand-paths

**Spec**: [spec.md](spec.md) | **Created**: 2026-10-04
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details.

Fixture contract (test assets this suite owns; all paths repo-root-relative,
each build finishes in seconds on a laptop):
- Fixture family root `tests/fixtures/on-demand-paths/`, provisioned by
  `tests/fixtures/on-demand-paths/provision.sh` (idempotent: `git init` +
  initial commit per workspace).
- `chain/` — four tiny modules where `chain_a` calls `chain_b`, `chain_b`
  calls `chain_c`, `chain_c` calls `chain_d`: a unique three-hop shortest
  path with no shortcuts.
- `depth/` — `dep_entry` reaches `dep_target` in two hops through either of
  two intermediates: a diamond whose equal-length routes make a first-found
  walk's reported depth order-dependent.
- `fork/` — `hub_top` calls `mid_left` and `mid_right`, and both call
  `hub_sink`: two equally short routes for one pair (a tie).
- `islands/` — two disjoint components (`isl_a1` → `isl_a2` and
  `isl_b1` → `isl_b2`) with no edge between them.
- `fanout/` — 60 independent entry symbols `fan_0`–`fan_59`, each calling
  `fan_sink` (60 one-hop pairs); names chosen so the probe tokens match the
  intended symbol sets under the same endpoint matching rules the query
  shares with the taint command.
- `fuzzy/` — `fz_start` calls `fz_shared`, a name defined twice in the
  workspace (so that hop is ambiguous, never exact), and each definition
  calls `fz_end`: the only route crosses an ambiguous hop.
- `update/` — a module `upd.py` where `upd_a` calls `upd_b`; TC-015/TC-016
  append to `upd.py` after the first build. This is the only workspace any
  TC mutates.

DB probes use the spec's own data vocabulary (`transitive_edges` closure
table). `cairn path` mirrors the endpoint surface of `cairn taint`
(`--db/--from/--to/--fuzzy/--max-depth`). All auto commands are single-line
and finish well under the 120 s audit cap.

## TC-001 — Shortest path prints as an ordered hop chain on a closure-free store
- **Story**: US1 · **Traces to**: FR-001, FR-004, AC1
- **Given** a default build of the chain workspace, carrying no materialized
  closure
- **When** the path query runs from the head of the chain to its tail
- **Then** the shortest exact-resolution path prints as an ordered hop chain
  (head, both intermediates, tail, in walk order) with a file:line reference
  for each of the three hops
- **Pass condition**: `sh tests/fixtures/on-demand-paths/provision.sh && rm -f /tmp/odp-tc001.db && uv run --no-sync cairn build --workspace tests/fixtures/on-demand-paths/chain --db /tmp/odp-tc001.db && [ "$(sqlite3 /tmp/odp-tc001.db 'select count(*) from transitive_edges')" = "0" ] && uv run --no-sync cairn path --db /tmp/odp-tc001.db --from chain_a --to chain_d > /tmp/odp-tc001.out && [ "$(grep -oE '\.py:[0-9]+' /tmp/odp-tc001.out | wc -l | tr -d ' ')" -ge 3 ] && python3 -c 'import re,sys; ls=[l for l in open("/tmp/odp-tc001.out") if re.search(r"\.py:[0-9]+",l)]; p=[next((i for i,l in enumerate(ls) if s in l),-1) for s in ("chain_a","chain_b","chain_c","chain_d")]; sys.exit(0 if -1 not in p and p==sorted(p) else 1)'`

## TC-002 — Query inside the depth bound but beyond the route length reports no path
- **Story**: US1 · **Traces to**: FR-001, AC2
- **Given** the chain workspace, whose two endpoints connect only after
  three hops
- **When** the path query runs with the depth bound set to one hop
- **Then** it reports that no path exists within that hop bound and exits 0
- **Pass condition**: `sh tests/fixtures/on-demand-paths/provision.sh && rm -f /tmp/odp-tc002.db && uv run --no-sync cairn build --workspace tests/fixtures/on-demand-paths/chain --db /tmp/odp-tc002.db && uv run --no-sync cairn path --db /tmp/odp-tc002.db --from chain_a --to chain_d --max-depth 1 > /tmp/odp-tc002.out 2>&1 && grep -qi "no path within" /tmp/odp-tc002.out`

## TC-003 — Disconnected symbols report no path without failing
- **Story**: US1 · **Traces to**: FR-001, AC2
- **Given** the islands workspace, two components with no connection between
  them
- **When** the path query runs from a symbol in one component to a symbol in
  the other, under the default depth bound
- **Then** it reports no path within the bound and exits 0 (a disconnected
  graph is an answer, not an error)
- **Pass condition**: `sh tests/fixtures/on-demand-paths/provision.sh && rm -f /tmp/odp-tc003.db && uv run --no-sync cairn build --workspace tests/fixtures/on-demand-paths/islands --db /tmp/odp-tc003.db && uv run --no-sync cairn path --db /tmp/odp-tc003.db --from isl_a1 --to isl_b2 > /tmp/odp-tc003.out 2>&1 && grep -qi "no path within" /tmp/odp-tc003.out`

## TC-004 — A tie resolves to exactly one shortest path
- **Story**: US1 · **Traces to**: FR-001
- **Given** the fork workspace, where the entry reaches the sink through
  either intermediate at the same length
- **When** the path query runs from the entry to the sink by exact name
- **Then** exactly one path is printed — one intermediate appears, never
  both, and the sink appears once
- **Pass condition**: `sh tests/fixtures/on-demand-paths/provision.sh && rm -f /tmp/odp-tc004.db && uv run --no-sync cairn build --workspace tests/fixtures/on-demand-paths/fork --db /tmp/odp-tc004.db && uv run --no-sync cairn path --db /tmp/odp-tc004.db --from hub_top --to hub_sink > /tmp/odp-tc004.out && python3 -c 'import re,sys; ls=[l for l in open("/tmp/odp-tc004.out") if re.search(r"\.py:[0-9]+",l)]; sys.exit(0 if sum("hub_sink" in l for l in ls)==1 and any("hub_top" in l for l in ls) and (any("mid_left" in l for l in ls))!=(any("mid_right" in l for l in ls)) else 1)'`

## TC-005 — Endpoint patterns yield one path per resolved pair
- **Story**: US1 · **Traces to**: FR-001
- **Given** the fork workspace
- **When** the query runs with an exact entry endpoint and a pattern
  endpoint matching both intermediates
- **Then** one one-hop path prints per resolved pair (both intermediates
  appear; the never-asked-for sink does not)
- **Pass condition**: `sh tests/fixtures/on-demand-paths/provision.sh && rm -f /tmp/odp-tc005.db && uv run --no-sync cairn build --workspace tests/fixtures/on-demand-paths/fork --db /tmp/odp-tc005.db && uv run --no-sync cairn path --db /tmp/odp-tc005.db --from hub_top --to mid > /tmp/odp-tc005.out && grep -q mid_left /tmp/odp-tc005.out && grep -q mid_right /tmp/odp-tc005.out && ! grep -q hub_sink /tmp/odp-tc005.out`

## TC-006 — Printed paths are capped when many pairs resolve
- **Story**: US1 · **Traces to**: FR-001
- **Given** the fanout workspace, where a pattern endpoint resolves 60
  one-hop pairs
- **When** the query runs from the shared entry pattern to the sink
- **Then** the command exits 0, prints at least one path, and prints fewer
  paths than resolved pairs — output is capped, never unbounded
- **Pass condition**: `sh tests/fixtures/on-demand-paths/provision.sh && rm -f /tmp/odp-tc006.db && uv run --no-sync cairn build --workspace tests/fixtures/on-demand-paths/fanout --db /tmp/odp-tc006.db && uv run --no-sync cairn path --db /tmp/odp-tc006.db --from fan_ --to fan_sink > /tmp/odp-tc006.out && python3 -c 'import re,sys; t=open("/tmp/odp-tc006.out").read(); e=set(re.findall(r"fan_\d+", t)); sys.exit(0 if 0 < len(e) < 60 else 1)'`

## TC-007 — Exact resolution by default; fuzzy includes ambiguous hops
- **Story**: US1 · **Traces to**: FR-003
- **Given** the fuzzy workspace, whose only route between the endpoints
  crosses an ambiguous (never exactly resolved) hop
- **When** the query runs twice — once default, once with fuzzy enabled
- **Then** the default run reports no path within the bound, and the fuzzy
  run finds the route across the ambiguous hop under the same hop budget
- **Pass condition**: `sh tests/fixtures/on-demand-paths/provision.sh && rm -f /tmp/odp-tc007.db && uv run --no-sync cairn build --workspace tests/fixtures/on-demand-paths/fuzzy --db /tmp/odp-tc007.db && uv run --no-sync cairn path --db /tmp/odp-tc007.db --from fz_start --to fz_end > /tmp/odp-tc007-def.out 2>&1 && grep -qi "no path within" /tmp/odp-tc007-def.out && uv run --no-sync cairn path --db /tmp/odp-tc007.db --from fz_start --to fz_end --fuzzy > /tmp/odp-tc007-fz.out && python3 -c 'import re,sys; ls=[l for l in open("/tmp/odp-tc007-fz.out") if re.search(r"\.py:[0-9]+",l)]; p=[next((i for i,l in enumerate(ls) if s in l),-1) for s in ("fz_start","fz_shared","fz_end")]; sys.exit(0 if -1 not in p and p==sorted(p) else 1)'`

## TC-008 — Same-symbol endpoints return the trivial zero-hop path (boundary)
- **Story**: US1 · **Traces to**: FR-001
- **Given** the chain workspace
- **When** the path query runs with both endpoints resolving to the same
  symbol
- **Then** the result is that symbol alone (zero hops) and exit 0 — no
  detour through its neighbors
- **Pass condition**: `sh tests/fixtures/on-demand-paths/provision.sh && rm -f /tmp/odp-tc008.db && uv run --no-sync cairn build --workspace tests/fixtures/on-demand-paths/chain --db /tmp/odp-tc008.db && uv run --no-sync cairn path --db /tmp/odp-tc008.db --from chain_a --to chain_a > /tmp/odp-tc008.out && grep -q chain_a /tmp/odp-tc008.out && ! grep -q chain_b /tmp/odp-tc008.out && ! grep -q chain_c /tmp/odp-tc008.out && ! grep -q chain_d /tmp/odp-tc008.out`

## TC-009 — An endpoint matching nothing degrades gracefully (boundary)
- **Story**: US1 · **Traces to**: FR-001
- **Given** the chain workspace
- **When** the path query runs with an endpoint pattern that matches no
  symbol
- **Then** the command exits 0 with no resolved pairs and no crash output
- **Pass condition**: `sh tests/fixtures/on-demand-paths/provision.sh && rm -f /tmp/odp-tc009.db && uv run --no-sync cairn build --workspace tests/fixtures/on-demand-paths/chain --db /tmp/odp-tc009.db && uv run --no-sync cairn path --db /tmp/odp-tc009.db --from no_such_symbol_qa --to chain_d > /tmp/odp-tc009.out 2>&1 && ! grep -qi traceback /tmp/odp-tc009.out && ! grep -qi "internal error" /tmp/odp-tc009.out`

## TC-010 — The path tool returns the CLI's chain over the served transports
- **Story**: US2 · **Traces to**: FR-002, AC1
- **Given** the chain store and the MCP server running
- **When** the `path` tool is invoked over stdio and over SSE with the same
  inputs as TC-001
- **Then** each invocation returns the same hop chain the CLI prints for
  those inputs (the HTTP-transport arm arrives with the HTTP-transport
  spec, which owns that surface)
- **Pass condition**: `uv run --no-sync pytest tests/test_mcp_path_tool.py -q`

## TC-011 — The tool registry and its count pin include the new tool
- **Story**: US2 · **Traces to**: FR-002
- **Given** the MCP server's tool registry and its tool-count self-check
- **When** the registry is listed and the self-check runs
- **Then** the `path` tool is registered and the count self-check passes
  with the updated pin — the pin reflects the new tool total, not the old one
- **Pass condition**: `uv run --no-sync pytest tests/test_mcp_path_tool.py -q`

## TC-012 — A default build materializes no closure
- **Story**: US3 · **Traces to**: FR-004, AC1
- **Given** the chain workspace
- **When** a default build runs
- **Then** the closure table holds zero rows — the most expensive derived
  work is skipped by default
- **Pass condition**: `sh tests/fixtures/on-demand-paths/provision.sh && rm -f /tmp/odp-tc012.db && uv run --no-sync cairn build --workspace tests/fixtures/on-demand-paths/chain --db /tmp/odp-tc012.db && [ "$(sqlite3 /tmp/odp-tc012.db 'select count(*) from transitive_edges')" = "0" ]`

## TC-013 — A default index import materializes no closure
- **Story**: US3 · **Traces to**: FR-004
- **Given** a workspace with a committed index, built by default and seeded
  by importing that index
- **When** the import completes
- **Then** the closure table still holds zero rows
- **Pass condition**: `sh tests/fixtures/scip-indexing/provision.sh && rm -f /tmp/odp-tc013.db && uv run --no-sync cairn build --workspace tests/fixtures/scip-indexing/covered-off --db /tmp/odp-tc013.db && uv run --no-sync cairn import-scip tests/fixtures/scip-indexing/covered/index.scip --workspace tests/fixtures/scip-indexing/covered-off --db /tmp/odp-tc013.db && [ "$(sqlite3 /tmp/odp-tc013.db 'select count(*) from transitive_edges')" = "0" ]`

## TC-014 — The opt-in flag restores the closure
- **Story**: US3 · **Traces to**: FR-004, AC2
- **Given** the chain workspace
- **When** a build runs with the closure opt-in flag
- **Then** the closure is materialized (non-empty row set) — the preserved
  row-set contract itself is owned by the standing parity gates in TC-021
- **Pass condition**: `sh tests/fixtures/on-demand-paths/provision.sh && rm -f /tmp/odp-tc014.db && uv run --no-sync cairn build --with-closure --workspace tests/fixtures/on-demand-paths/chain --db /tmp/odp-tc014.db && [ "$(sqlite3 /tmp/odp-tc014.db 'select count(*) from transitive_edges')" -gt 0 ]`

## TC-015 — Update performs no closure work when the closure is absent (standing guard)
- **Story**: US3 · **Traces to**: FR-006, AC1
- **Given** the update workspace built by default (closure absent), with new
  sources appended after the build so an update has graph work to do
- **When** an incremental update runs
- **Then** the closure table is still empty — no closure maintenance creeps
  into the default path
- **Pass condition**: `sh tests/fixtures/on-demand-paths/provision.sh && rm -f /tmp/odp-tc015.db && uv run --no-sync cairn build --workspace tests/fixtures/on-demand-paths/update --db /tmp/odp-tc015.db && printf '\ndef upd_c():\n    return 3\n' >> tests/fixtures/on-demand-paths/update/upd.py && printf '\ndef upd_a2():\n    return upd_c()\n' >> tests/fixtures/on-demand-paths/update/upd.py && uv run --no-sync cairn update --workspace tests/fixtures/on-demand-paths/update --db /tmp/odp-tc015.db && [ "$(sqlite3 /tmp/odp-tc015.db 'select count(*) from transitive_edges')" = "0" ]`

## TC-016 — Update maintains the closure when it is present
- **Story**: US3 · **Traces to**: FR-006
- **Given** the update workspace built with the closure opt-in flag, with
  the same appended sources as TC-015 so the update adds new reachability
- **When** an incremental update runs
- **Then** the closure row set grows to include the new reachability — the
  closure is maintained, not abandoned or stale
- **Pass condition**: `sh tests/fixtures/on-demand-paths/provision.sh && rm -f /tmp/odp-tc016.db && uv run --no-sync cairn build --with-closure --workspace tests/fixtures/on-demand-paths/update --db /tmp/odp-tc016.db && before=$(sqlite3 /tmp/odp-tc016.db 'select count(*) from transitive_edges') && printf '\ndef upd_c():\n    return 3\n' >> tests/fixtures/on-demand-paths/update/upd.py && printf '\ndef upd_a2():\n    return upd_c()\n' >> tests/fixtures/on-demand-paths/update/upd.py && uv run --no-sync cairn update --workspace tests/fixtures/on-demand-paths/update --db /tmp/odp-tc016.db && [ "$(sqlite3 /tmp/odp-tc016.db 'select count(*) from transitive_edges')" -gt "$before" ]`

## TC-017 — Impact depths stay shortest-path on closure-free stores
- **Story**: US4 · **Traces to**: FR-005, AC1
- **Given** two builds of the depth workspace — one with the closure, one
  without — where the entry reaches the target through either intermediate
- **When** impact analysis runs at a depth-three bound against the target
  (the diamond node that has callers) on each build
- **Then** the two outputs are byte-identical: the closure-free walk tracks
  the minimum distance per reached symbol, so its depths equal the
  closure-era shortest depths for the same graph
- **Pass condition**: `sh tests/fixtures/on-demand-paths/provision.sh && rm -f /tmp/odp-tc017-on.db /tmp/odp-tc017-off.db /tmp/odp-tc017-on.out /tmp/odp-tc017-off.out && uv run --no-sync cairn build --with-closure --workspace tests/fixtures/on-demand-paths/depth --db /tmp/odp-tc017-on.db && uv run --no-sync cairn build --workspace tests/fixtures/on-demand-paths/depth --db /tmp/odp-tc017-off.db && uv run --no-sync cairn impact --db /tmp/odp-tc017-on.db --depth 3 dep_target > /tmp/odp-tc017-on.out && uv run --no-sync cairn impact --db /tmp/odp-tc017-off.db --depth 3 dep_target > /tmp/odp-tc017-off.out && diff /tmp/odp-tc017-on.out /tmp/odp-tc017-off.out`

## TC-018 — Identical queries return identical output across runs
- **Story**: US1 · **Traces to**: FR-007
- **Given** the fork workspace's tie (two equally short routes) in an
  unchanged store
- **When** the same path query runs twice in separate processes
- **Then** the two outputs are identical, byte for byte — the tie breaks the
  same way every time
- **Pass condition**: `sh tests/fixtures/on-demand-paths/provision.sh && rm -f /tmp/odp-tc018.db && uv run --no-sync cairn build --workspace tests/fixtures/on-demand-paths/fork --db /tmp/odp-tc018.db && uv run --no-sync cairn path --db /tmp/odp-tc018.db --from hub_top --to hub_sink > /tmp/odp-tc018-a.out && uv run --no-sync cairn path --db /tmp/odp-tc018.db --from hub_top --to hub_sink > /tmp/odp-tc018-b.out && diff /tmp/odp-tc018-a.out /tmp/odp-tc018-b.out`

## TC-019 — A max-depth-4 query answers within the two-second budget (bounded)
- **Story**: US1 · **Traces to**: NFR-003
- **Given** a built store
- **When** a max-depth-4 path query runs
- **Then** it answers within two seconds wall. Standing at-scale verify
  (beyond the audit's 120 s cap, run as scaling-gate work): the 1000-file
  scaling corpus measures the same depth-4 query against the same two-second
  budget — `uv run --no-sync pytest tests/test_scaling_gate.py -q`
- **Pass condition**: `sh tests/fixtures/on-demand-paths/provision.sh && rm -f /tmp/odp-tc019.db && uv run --no-sync cairn build --workspace tests/fixtures/on-demand-paths/chain --db /tmp/odp-tc019.db && uv run --no-sync python -c 'import subprocess,time; t=time.monotonic(); subprocess.run(["uv","run","--no-sync","cairn","path","--db","/tmp/odp-tc019.db","--from","chain_a","--to","chain_d","--max-depth","4"],check=True,capture_output=True); assert time.monotonic()-t < 2.0'`

## TC-020 — A fuzzy query over a wide fan-out stays bounded (boundary)
- **Story**: US1 · **Traces to**: FR-003
- **Given** the fanout workspace (60 one-hop pairs) queried with fuzzy
  enabled, the mode at risk of fan-out explosion
- **When** the fuzzy path query runs with the default hop budget
- **Then** it terminates (bounded by the depth limit), exits 0, and reports
  paths reaching the sink
- **Pass condition**: `sh tests/fixtures/on-demand-paths/provision.sh && rm -f /tmp/odp-tc020.db && uv run --no-sync cairn build --workspace tests/fixtures/on-demand-paths/fanout --db /tmp/odp-tc020.db && uv run --no-sync cairn path --db /tmp/odp-tc020.db --from fan_ --to fan_sink --fuzzy --max-depth 4 > /tmp/odp-tc020.out && grep -q fan_sink /tmp/odp-tc020.out`

## TC-021 — Existing graph-behavior pins stay green (standing guard)
- **Story**: US3, US4 · **Traces to**: FR-004, FR-005, FR-006
- **Given** the behavior pins that guard the closure row-set contract,
  traversal parity, and incremental derived-index maintenance
- **When** the demotion and the min-depth resolution land
- **Then** those pins pass unchanged — demotion is never a silent behavior
  change
- **Pass condition**: `uv run --no-sync pytest tests/test_scaling_gate.py tests/test_traversal_parity.py tests/test_incremental_derived.py -q -m "not infra"`

## TC-022 — Taint behavior is untouched by the shared traversal core (standing guard)
- **Story**: US1 · **Traces to**: FR-003, FR-001
- **Given** the taint suite pinning taint's endpoints, fuzzy tier, and
  formatting
- **When** the path query lands on the shared traversal machinery
- **Then** every taint test passes untouched — sharing the core changes no
  taint behavior
- **Pass condition**: `uv run --no-sync pytest tests/ -k taint -q`

## Coverage matrix
<!-- Every FR and applicable NFR appears; `check.py` fails a requirement with no TC. -->
| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001      | TC-001, TC-002, TC-003, TC-004, TC-005, TC-006, TC-008, TC-009 | auto |
| FR-002      | TC-010, TC-011 | auto |
| FR-003      | TC-007, TC-020, TC-022 | auto |
| FR-004      | TC-001, TC-012, TC-013, TC-014, TC-021 | auto |
| FR-005      | TC-017, TC-021 | auto |
| FR-006      | TC-015, TC-016, TC-021 | auto |
| FR-007      | TC-018 | auto |
| NFR-003     | TC-019 | auto |
| NFR-001     | — | N/A (spec records: no new network or auth surface) |
| NFR-002     | — | N/A (spec records: no new data leaves the store) |
| NFR-004     | — | N/A (spec records: no new persistence or recovery contract) |
| NFR-005     | — | N/A (spec records: CLI output plus existing telemetry suffice) |
| NFR-006     | — | N/A (spec records: no user-facing UI surface touched) |

AC coverage: US1 AC1 → TC-001 · US1 AC2 → TC-002, TC-003 · US2 AC1 →
TC-010 · US3 AC1 → TC-012, TC-015 · US3 AC2 → TC-014 · US4 AC1 → TC-017.

Notes: TC-010/TC-011 share one owner module (`tests/test_mcp_path_tool.py`),
which this suite contracts to prove both the transport parity and the
registry-pin contracts. The US2 AC1 HTTP arm is deferred with the HTTP
transport to its own spec, per this spec's Scope. No property library ships
in this repo, so FR-007's determinism contract is pinned with fixed
boundary examples (TC-018) rather than a property run.

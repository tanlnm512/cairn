# Plan: graft-parity

**Spec**: [spec.md](spec.md) | **Created**: 2026-09-16
**Branch**: `feat/graft-parity` (survey baseline `811b9e12a6c9c6ea5fb1740a1927c1ed7c9cef96`)

## Milestones
<!-- Each milestone = a phase in task.md. -->
| Phase | Milestone | Delivers (demoable) | FRs | Depends on |
|-------|-----------|---------------------|-----|------------|
| 1 | Always-fresh query substrate | Every listed graph MCP tool and CLI query command probes `(size, mtime)` drift and refreshes exactly those paths through `reindex_paths`; disabled or read-only refresh answers from storage with a probe-derived staleness banner | FR-001, FR-002 | — |
| 2 | Diff blast radius | `cairn blast` turns changed hunks into span-intersecting seed symbols, walks reverse edges precisely by default, and renders all required formats and terminal cases | FR-003, FR-004 | Phase 1 |
| 3 | Orientation and audit surfaces | Deterministic `cairn map`/MCP `repo_map`, MCP `file_api` without bodies, and enclosing-symbol `cairn grep`; MCP inventory moves from 22 to 24 tools | FR-005, FR-006, FR-007 | Phase 1 |
| 4 | Durable knowledge and portable viz | Human `## Notes` survive all wiki/compass writers and are ignored by the critic; `viz --export` emits one self-contained HTML graph artifact | FR-008, FR-009 | — |
| 5 | Precision and broader discovery | Opt-in pyright edge upgrades, Rust generic-tier indexing, and config-gated submodule/nested-repo/worktree discovery with prefixed identities | FR-010, FR-011, FR-012 | Phases 1 and 3 |

## Dependencies

```
Phase 1 — freshness spine (exclusive owner of query dispatch)
  ├──> Phase 2 — blast reads a graph refreshed at query time
  ├──> Phase 3 — map/file_api/grep dispatch after the shared MCP core settles
  │        FR-005 domain ∥ FR-006 signature storage ∥ FR-007 grep domain
  │        then one serial MCP dispatch/tool-count slice
  └──> Phase 5 FR-012 — worktree refresh consumes the Phase 1 drift contract

Phase 4 — notes ∥ viz (no query-dispatch, scanner, schema, or builder overlap)

Phase 3 ──> Phase 5 FR-010
           (file_api signature storage and LSP both end in graph storage/build paths)

Phase 5 — FR-010 ∥ FR-011, then FR-011 ──> FR-012
           (Rust and worktree discovery both modify scanner wiring)
```

- Phase 1 is first because FR-001/FR-002 and FR-005/FR-006 share
  `src/cairn/mcp_server/tools_graph.py` and
  `src/cairn/mcp_server/_server_core.py`. Landing the freshness behavior first
  prevents concurrent edits to every graph tool entry point.
- Phase 2 consumes the Phase 1 contract that a query graph reflects drifted
  working-tree files before reverse-edge traversal.
- Phase 3's domain computations are parallel, but its MCP registration is
  serial: `repo_map` and `file_api` both add functions to
  `tools_graph.py` and both must update `_EXPECTED_TOOL_COUNT` in
  `src/cairn/mcp_server/server.py`.
- FR-006's persisted signature representation must exist before `file_api`
  consumes it. FR-005 does not depend on that representation and can proceed
  in parallel at the domain layer.
- Phase 4 is independent of Phases 1-3 and can occupy a separate implementer
  wave; its two areas are also independent of one another.
- Phase 5 waits for Phase 3 before the LSP build pass because FR-006 signature
  storage and FR-010 both touch graph storage/build files. Rust parser work can
  start earlier; only its scanner integration and FR-012's scanner integration
  are strictly ordered.

## Parallelization map
<!-- Which work areas are independent (different files/subsystems, no shared
     state) and can be developed concurrently, and which are strictly
     sequential. The task-breaker turns this into [P] markers per task. -->

Default is parallel. Serial entries below must carry `(after T###)` in
`task.md`; the file sets are exclusive inside each parallel wave.

| Area / FRs | Intended file set | Scheduling |
|---|---|---|
| Freshness · FR-001, FR-002 | `src/cairn/graph/watcher.py` (probe reuse), `src/cairn/graph/incremental.py` (`reindex_paths` call only), `src/cairn/mcp_server/_server_core.py`, `src/cairn/mcp_server/tools_graph.py`, `src/cairn/cli/query.py`, `src/cairn/cli/ask_context.py`, `src/cairn/cli/serve.py`, freshness tests | **Serial spine.** No other implementation wave may edit these query-dispatch files until Phase 1 is merged. |
| Blast core · FR-003 | new `src/cairn/graph/blast.py`, new `src/cairn/cli/blast.py`, blast tests; reuse `impact_analysis` without editing `src/cairn/graph/traversal.py` | After Phase 1; parallel with Phase 4, Phase 5 FR-011 parser work, and Phase 3 domain modules. |
| Blast rendering/CLI · FR-004 | the FR-003 files plus blast renderer and CLI-option tests | **Strictly after the FR-003 radius model** whose result shape the renderers consume. |
| Repo map domain · FR-005 | new `src/cairn/graph/repo_map.py`, new `src/cairn/cli/map.py`, repo-map tests; read-only reuse of `src/cairn/graph/stats.py` | After Phase 1; parallel with FR-006 signature storage and FR-007 grep domain. |
| File signatures · FR-006 storage | `src/cairn/graph/schema.py`, `src/cairn/graph/builder.py`, signature migration/degradation tests | After Phase 1; parallel with FR-005 and FR-007 domains. No MCP dispatch edits in this slice. |
| Grep domain · FR-007 | new `src/cairn/graph/grep.py`, new `src/cairn/cli/grep.py`, grep tests | After Phase 1; parallel with FR-005 and FR-006 domains. |
| MCP orientation dispatch · FR-005, FR-006 | `src/cairn/mcp_server/tools_graph.py`, `src/cairn/mcp_server/_server_core.py`, `src/cairn/mcp_server/server.py` (`_EXPECTED_TOOL_COUNT`: 22 → 24), MCP registry tests | **Serial after Phase 1, FR-005 domain, and FR-006 signature storage.** Register `repo_map`, then `file_api` (or vice versa) in one owned sequence; no parallel editor of these three files. |
| CLI/MCP registry and docs · FR-003–FR-007, FR-009–FR-012 | `src/cairn/cli/__init__.py`, `README.md`, `docs/cli-reference.md`, `docs/mcp-tools.md`, `docs/architecture.md` | **Serial at each milestone boundary:** update only that milestone's command/tool/build/config entries after its adapters exist; no parallel editor owns these registration/doc files. |
| Notes preservation · FR-008 | `src/cairn/wiki/generator.py`, `src/cairn/compass/generator.py`, `src/cairn/cli/wiki.py`, `src/cairn/compass/critic.py`, notes-preservation tests | Parallel with all non-Phase-4 areas; disjoint from viz files. |
| HTML export · FR-009 | `src/cairn/viz/renderers.py`, `src/cairn/viz/__init__.py`, `src/cairn/cli/hooks_viz.py`, HTML-export tests; inlined asset sourced from `src/cairn/dashboard/static/` without modifying it | Parallel with FR-008 and all non-viz areas. |
| LSP pass · FR-010 | new `src/cairn/graph/lsp.py`, `src/cairn/graph/builder.py`, `src/cairn/cli/core.py`, LSP tests | **After Phase 3** (`schema.py`/`builder.py` collision with FR-006); parallel with FR-011. |
| Rust generic tier · FR-011 | new `src/cairn/parsers/generic_tree_sitter.py`, new `src/cairn/parsers/rust.py`, `src/cairn/parsers/_registry.py`, `src/cairn/graph/scanner.py`, `pyproject.toml`, Rust indexing tests; D-### dependency decision in `tech-spec.md` | Parser/registry/dependency work may start with Phase 2; scanner integration is the serial scanner owner before FR-012. |
| Worktree/submodule discovery · FR-012 | `src/cairn/graph/config.py`, new `src/cairn/graph/worktree.py`, `src/cairn/graph/scanner.py`, discovery/worktree tests; reuse `_detect_changed` and `reindex_paths` | **After Phase 1 and FR-011 scanner integration** because both edit `src/cairn/graph/scanner.py`. Helper/config tests may be written in parallel; scanner changes stay chained. |

Strict-order proof:

- **Query dispatch**: FR-001/FR-002 rewrite entries in the same graph tool and
  CLI query files that FR-005/FR-006 must extend. Phase separation is required.
- **Blast model → renderer**: FR-004 formats the exact radius/seed/truncation
  object produced by FR-003.
- **MCP inventory**: both new tools mutate `tools_graph.py`, shared server
  helpers, and the same `_EXPECTED_TOOL_COUNT` constant. A single serial
  registration sequence lands the final count of 24 once.
- **Signature storage → `file_api`**: the tool cannot define its degradation
  boundary until the persisted signature representation and migration exist.
- **Rust → worktree discovery**: both change language/discovery behavior in
  `src/cairn/graph/scanner.py`; FR-012 must preserve the FR-011 scanner
  contract while adding prefixed repo identities.
- **Registry/docs**: every new top-level command and tool-count reference lands
  in the same small set of registration and documentation files, so one
  integrator owns them after domain modules are present.

## Checkpoints
<!-- Exit condition per phase; verify before starting the next. -->

- **After Phase 1** — All eight named MCP graph tools and six named CLI query
  commands exercise the same drift probe; only changed paths are passed to
  `reindex_paths`; `CAIRN_NO_REFRESH=1`, `--no-refresh`, and read-only mode
  return stored answers plus a probe-derived banner without the watcher extra.
  ```
  CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_watcher_service.py -q
  CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests -q -m "not infra" -k "freshness or staleness or no_refresh"
  ```

- **After Phase 2** — Working-tree and `--base` diffs seed only symbols whose
  spans intersect changed hunks; precise is default and `--fuzzy` is opt-in;
  all four formats, empty-radius exit 0, and missing-ref fetch-depth guidance
  are observable.
  ```
  CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_traversal_parity.py -q
  CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests -q -m "not infra" -k "blast"
  ```

- **After Phase 3** — `repo_map` is deterministic and reports every cap drop;
  `file_api` exposes every symbol with no bodies and degrades to name+span;
  `grep` reports enclosing-symbol groups, in-edge ranking, flag behavior, and
  truncation; MCP inventory verification expects 24 tools.
  ```
  CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests -q -m "not infra" -k "repo_map or file_api or grep or tool_count"
  ```

- **After Phase 4** — Regeneration and enrichment return a byte-identical
  `## Notes` section, the critic ignores that section, and HTML export is one
  file with no network-loaded asset references while retaining the selected
  graph rendering.
  ```
  CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_enrich.py -q
  CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests -q -m "not infra" -k "notes or viz_export or self_contained_html"
  ```

- **After Phase 5** — Pyright upgrades only resolvable ambiguous edges and
  never downgrades exact edges; a missing/failing server is a noticed no-op;
  Rust edges remain unresolved; nested-repo inclusion defaults off and uses
  prefixed ids; a store-less linked worktree seeds from the main checkout and
  refreshes drift.
  ```
  CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_traversal_parity.py tests/test_incremental_derived.py -q
  CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests -q -m "not infra" -k "lsp or pyright or rust or submodule or nested_repo or worktree"
  ```

- **Final gate** — The non-infra baseline suite passes on the complete
  implementation tree after all phase-specific checks:
  ```
  uv run pytest -q -m "not infra"
  ```

## Risks & mitigations

- Risk: concurrent MCP edits repeat conflicts in every graph tool. → Phase 1
  owns the shared dispatch files; Phase 3 registration follows in one sequence
  and changes the inventory constant once to 24.
- Risk: signature storage changes migrations while `file_api` is being wired.
  → Build and verify the FR-006 storage/degradation contract before the tool
  dispatch slice; keep LSP behind Phase 3 because it also ends in build/storage.
- Risk: new top-level commands collide in CLI registration and documentation.
  → Domain modules are parallel, but one integration task exclusively owns
  `src/cairn/cli/__init__.py` and the shared command/tool docs.
- Risk: Rust grammar dependency widens packaging. → The D-### decision and
  graceful absence behavior land with the dependency change; Rust edges are
  never labeled exact.
- Risk: worktree seeding could mutate the main checkout store. → FR-012 seeds
  a worktree-scoped store, then uses the Phase 1 drift refresh; tests assert
  the main store remains unchanged.
- Risk: LSP behavior is timing- and server-dependent. → Bound the pass to
  pyright, ambiguous Python member calls, and deterministic no-op degradation;
  tests use a scripted JSON-RPC server plus absence/failure cases and assert no
  exact downgrade.
- Risk: a supposed parallel area discovers another shared file. → The task is
  removed from its parallel wave and chained after the existing owner; it never
  edits outside the file set recorded here.

## Delivery

Solo cadence, one PR per milestone. Merge Phase 1 before any wave that consumes
or edits its query-dispatch files. Phases 2 and 3, and Phase 4, may be developed
concurrently but are delivered in dependency order with the serial MCP, scanner,
CLI-registry, and docs slices respected. Every PR uses the workspace shipping
procedure: `pre-commit run --all-files`, a conventional commit title, the audit
checklist, CI, and the final baseline command above before merge.

# Survey: graft-parity

**Created**: 2026-09-16 | **Baseline**: HEAD @ 811b9e12a6c9c6ea5fb1740a1927c1ed7c9cef96 (`git rev-parse HEAD`, this session)
Reference comparison: /tmp/graft (read-only; never copied). Context files
specs/context/structure.md and specs/context/tech.md exist and were read; spot-checked
against this session's greps — no material staleness found (version/tool-count lines still
say v0.20.x era; `_EXPECTED_TOOL_COUNT = 22` at src/cairn/mcp_server/server.py:56 matches
this tree), so they were not rewritten.

## Items

```
item FR-001: "Graph MCP tools / CLI query commands reindex (size, mtime)-drifted files via reindex_paths before answering"
  evidence:   Drift probe exists but is NOT wired into any query surface. Probe:
              src/cairn/graph/watcher.py:59 `def _detect_changed(conn, workspace)` — compares
              files-table (size, mtime) against disk (st_size at watcher.py:94, st_mtime at
              watcher.py:97); an identical copy at src/cairn/graph/incremental.py:871-873.
              It is called only from `ensure_fresh_force` (watcher.py:113) via
              `_do_catch_up` (watcher.py:118), which is boot catch-up — `rg -n "_detect_changed|reindex_paths"
              src/cairn/mcp_server src/cairn/cli/query.py src/cairn/cli/ask_context.py` returns 0 matches.
              The 8 MCP graph tools (src/cairn/mcp_server/tools_graph.py:52 find_definition, :87 get_callers,
              :187 get_callees, :269 impact_analysis, :409 explore, :577 semantic_search, :734 search_symbols,
              :828 cross_repo_deps) open `_conn()`, run the query, close — no freshness probe, no
              reindex_paths call. The CLI commands (src/cairn/cli/query.py:16 find_def, :48 callers, :79 search,
              :110 callees, :143 impact; src/cairn/cli/ask_context.py:46 context) do the same.
              Staleness signaling today is pending_sync-table-based, not probe-based:
              src/cairn/mcp_server/_server_core.py:242 `_staleness_banner` SELECTs `pending_sync`
              (populated only by the watcher; only call site with results: tools_graph.py:135).
              reindex_paths itself exists: src/cairn/graph/incremental.py:24.
  status:     TODO   (probe + reindex_paths machinery DONE-in-code; per-query wiring absent)
  verify:     CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_watcher_service.py -q   # PASS 11 passed (probe machinery)
  gap:        wire a bounded (size, mtime) probe into the 8 MCP tools + 5 CLI query commands; reindex
              exactly drifted paths via reindex_paths before answering; new tests.

item FR-002: "Refresh disabled (CAIRN_NO_REFRESH=1 / --no-refresh) or read-only store answers from stored graph with probe-based staleness banner, no [watch] extra"
  evidence:   `CAIRN_NO_REFRESH` and `--no-refresh` do not exist: `rg -n "CAIRN_NO_REFRESH|no.refresh"
              src/cairn` → 0 matches. Read-only mode exists: src/cairn/mcp_server/_server_core.py:92
              `_read_only_mode()` checks CAIRN_READ_ONLY (lines 92-98); `_conn` honors it
              (_server_core.py:164-174). The only banner is `_staleness_banner`
              (_server_core.py:242) which its own docstring says stays inert without the
              watcher/watchdog: "Without ``watchdog`` ... no rows are ever written and the
              banner stays inert" (_server_core.py:249-258) — i.e. it does NOT satisfy the
              no-watch-extra requirement and it is not computed from a (size, mtime) probe.
              The only other read_only use in tools_graph.py is gating memory-ref writes (line 457).
  status:     TODO
  verify:     rg -n "CAIRN_NO_REFRESH|no.refresh" src/cairn -g '!dashboard/static/**'; rg -n "def _staleness_banner|def _read_only_mode" src/cairn/mcp_server/_server_core.py   # first: 0 matches (gap proven); second: 242, 92
  gap:        no-refresh opt-out on both surfaces; probe-based banner path independent of pending_sync/watchdog.

item FR-003: "cairn blast computes reverse-edge radius of a git diff (worktree vs HEAD / --base merge-base), seeded from symbols whose spans intersect changed hunks"
  evidence:   No `blast` command exists: `rg -n '"blast"|name="blast"|def blast' src/cairn/cli`
              → 0 matches (this session). Reusable machinery that exists: git-diff changed-file
              detection src/cairn/graph/incremental.py:348 `incremental_update` (uses
              `git diff --name-only HEAD`, incremental.py:826); recursive reverse traversal
              src/cairn/graph/traversal.py:156 `impact_analysis` (precise default, fuzzy opt-in,
              cycle detection, limit/truncated); symbol spans stored as line_start/line_end
              (src/cairn/graph/schema.py:42-43). No code parses diff HUNK line ranges for
              span intersection: `rg -n "hunk|unified|merge.base" src/cairn -g '!dashboard/static/**'` → 0 matches.
  status:     TODO   (impact_analysis + changed-file detection DONE-in-code; command + hunk/span seeding absent)
  verify:     CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_traversal_parity.py -q   # PASS (72 passed incl. test_incremental_derived.py in same run)
  gap:        new CLI command, hunk parsing, merge-base resolution, span-intersection seeding.

item FR-004: "cairn blast renders text|markdown|mermaid|json, precise default + --fuzzy, empty-radius exit-0 message, CI fetch-depth guidance for missing --base"
  evidence:   No blast renderer exists (see FR-003: 0 matches in src/cairn/cli). Format
              precedents to reuse: `cairn viz` format choice is mermaid|dot|json
              (src/cairn/cli/hooks_viz.py:41) with `--output` text write at hooks_viz.py:80;
              mermaid renderer src/cairn/viz/renderers.py:8 `to_mermaid`, DOT :63 area, JSON
              per src/cairn/viz/__init__.py:7 exports; markdown wrapper `embed`
              (renderers.py, exported viz/__init__.py:7). Precise/fuzzy flag precedent:
              src/cairn/cli/query.py impact command `--fuzzy` option (impact at :143).
              No fetch-depth guidance text exists: `rg -n "fetch.depth|shallow" src/cairn -g '!dashboard/static/**'` → 0 matches.
  status:     TODO
  verify:     rg -n '"blast"|name="blast"' src/cairn/cli --no-heading; rg -n "def to_mermaid|def embed" src/cairn/viz/renderers.py   # first: 0 matches; second: renderers present
  gap:        all four renderers, empty-radius contract, missing-ref error path.

item FR-005: "cairn map (CLI + MCP repo_map): deterministic directory-cluster orientation with counts, in-degree hubs, hotspots, dropped counts"
  evidence:   Absent: `rg -n 'repo_map|name="map"|def map' src/cairn/cli src/cairn/mcp_server` → 0 matches.
              MCP tool count is pinned at src/cairn/mcp_server/server.py:56 `_EXPECTED_TOOL_COUNT = 22`
              (assert at server.py:171-173) — adding repo_map means bumping this and its tests.
              Deterministic counting substrate exists: src/cairn/graph/stats.py (per-area stats),
              in-degree aggregation precedent in src/cairn/wiki/generator.py:84-86
              ("most-referenced classes" by `incoming`).
  status:     TODO
  verify:     rg -n "_EXPECTED_TOOL_COUNT" src/cairn/mcp_server/server.py; rg -n "repo_map|name=\"map\"" src/cairn/cli src/cairn/mcp_server   # 56 + 171; second: 0 matches
  gap:        cluster grouping, hub/hotspot ranking, cap+dropped reporting, CLI command, MCP tool, count bump.

item FR-006: "MCP file_api tool: kind, qualified name, signature text, line span for every symbol in a file, no bodies; degrade to name+span"
  evidence:   Absent: `rg -n "file_api" src/cairn` → 0 matches. Data substrate is present and
              richer than name+span: symbols table stores name, qualified_name, kind,
              line_start/line_end (src/cairn/graph/schema.py:35-46) plus ALTER-migration columns
              parameters/return_type/body (src/cairn/graph/schema.py:440-446,
              SYMBOL_PARAMETERS_MIGRATION :440, SYMBOL_RETURN_TYPE_MIGRATION :441,
              SYMBOL_BODY_MIGRATION :446); builder inserts them at src/cairn/graph/builder.py:786-802.
              No stored "signature text" column exists — parameters/return_type/body would need
              synthesis or parsers would need to persist signature text.
  status:     TODO   (storage substrate PARTIAL: spans/qualified names DONE-in-code; signature text absent)
  verify:     rg -n "file_api" src/cairn; rg -n "SYMBOL_PARAMETERS_MIGRATION|SYMBOL_BODY_MIGRATION" src/cairn/graph/schema.py   # first: 0 matches; second: 440, 446
  gap:        MCP tool, per-file symbol enumeration, signature-text synthesis + degradation, tool-count bump.

item FR-007: "cairn grep PATTERN: regex over indexed files, group hits by enclosing symbol via spans, rank by incoming edges, -i/--fixed/--in, truncation counts"
  evidence:   Absent as a command: `rg -n 'def grep|name="grep"' src/cairn/cli` → 0 matches
              (only bench helper src/cairn/bench/agent_suite.py:126 `def grep`, unrelated).
              Substrate: spans (schema.py:42-43), incoming-edge counts precedent
              (wiki/generator.py:84-86), indexed-file inventory in files table
              (schema.py:30-34, UNIQUE(repo_id, path)).
  status:     TODO
  verify:     rg -n "def grep|name=\"grep\"" src/cairn/cli --no-heading   # 0 matches (bench copy at src/cairn/bench/agent_suite.py:126 is not a CLI)
  gap:        command, enclosing-symbol grouping, ranking, flags, truncation reporting.

item FR-008: "## Notes section preserved byte-identical across wiki/compass regeneration+enrichment; deterministic critic skips Notes content"
  evidence:   No Notes machinery exists: `rg -n '## Notes|"Notes"' src/cairn/wiki src/cairn/compass
              src/cairn/okf` → 0 matches. Regeneration overwrites the whole body:
              src/cairn/wiki/generator.py:75-101 builds body from scratch (## Overview ...
              ## Dependents) per `generate_wiki` (:31); compass template body at
              src/cairn/compass/generator.py:263 `_template_body`. Enrichment is append-only
              via task queue (src/cairn/cli/wiki.py:328 `enrich`, docstring :329 "appending new
              sections to promoted pages") but has no section-preservation logic. Critic
              checks the ENTIRE body: src/cairn/compass/critic.py:54 `critic_concept` uses
              `body = concept.body or ""` (line 68) and extracts refs from all of it
              (_extract_file_refs/_extract_symbol_refs calls, critic.py:71/76); no section
              exclusion. Byte-identity today is only the failure path:
              tests/test_wiki_enrich.py:359 (critic-failing cycle leaves page byte-identical).
  status:     TODO   (append-only enrich PARTIAL; Notes preserve + critic skip absent)
  verify:     CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_enrich.py -q   # PASS 8 passed (existing enrich contract)
  gap:        Notes section splice/preserve on every writer; critic Notes exclusion; tests.

item FR-009: "cairn viz --export FILE.html writes one self-contained HTML file, no network assets, rendering selected scope"
  evidence:   No HTML renderer/export: `rg -n "html|HTML" src/cairn/viz` → 0 matches;
              `cairn viz --output` writes the chosen TEXT format only (mermaid/dot/json,
              src/cairn/cli/hooks_viz.py:41, write at :80). Renderers are text-only:
              src/cairn/viz/renderers.py:8 to_mermaid; viz/__init__.py:7 exports only
              to_mermaid/to_dot/to_json/embed. Bundled local JS assets exist in the
              dashboard (src/cairn/dashboard/static/vis-network.min.js) but no code
              inlines them into an export file.
  status:     TODO
  verify:     rg -n "html|HTML" src/cairn/viz --no-heading; rg -n "def to_mermaid" src/cairn/viz/renderers.py   # first: 0 matches; second: 8
  gap:        HTML template, asset inlining (mermaid/vis), --export flag, no-network assertion test.

item FR-010: "build --lsp uses pyright to upgrade ambiguous Python member-call edges to exact; missing/failing server is a no-op notice; no exact downgrade"
  evidence:   No LSP/pyright code: `rg -n "pyright" src/cairn pyproject.toml tests` (excluding
              dashboard static) → 0 matches; no `--lsp` flag: `rg -n "\-\-lsp" src/cairn` → 0 matches.
              Ambiguous-edge substrate exists: edges.resolution column + values comment
              (src/cairn/graph/schema.py:420-425, EDGE_RESOLUTION_MIGRATION :425); resolver
              leaves ambiguous edges unresolved (src/cairn/graph/builder.py:11 docstring:
              "``ambiguous`` and left unresolved"). Build entry: src/cairn/graph/builder.py
              (build pipeline), CLI `cairn build` in src/cairn/cli/core.py.
  status:     TODO
  verify:     rg -n "pyright" src/cairn pyproject.toml tests -g '!dashboard/static/**' -g '!*.mjs' -g '!*.js'   # exit 1, 0 matches
  gap:        pyright JSON-RPC client, ambiguous-edge selection, upgrade pass, no-op degradation, --lsp flag.

item FR-011: "Index Rust .rs via generic tree-sitter extractor; name-based call edges labeled unresolved; grammar dependency recorded as D-###"
  evidence:   No Rust support: `rg -ni "rust|\.rs" src/cairn/parsers src/cairn/graph/scanner.py
              pyproject.toml` → 0 matches. pyproject pins 14 grammars, none Rust
              (pyproject.toml:29-42: tree-sitter core + java, python, swift, typescript,
              javascript, dart, objc, go, php, ruby, c-sharp, c, cpp). One vendored grammar
              precedent exists: vendor/tree-sitter-kotlin (directory listing this session).
              Parser registry: src/cairn/parsers/_registry.py; per-language adapter pattern:
              src/cairn/parsers/go.py, ruby.py, etc. Edge-label contract for unresolved:
              schema.py:420-425.
  status:     TODO
  verify:     rg -ni "rust" pyproject.toml src/cairn/parsers src/cairn/graph/scanner.py --no-heading; ls vendor   # first: 0 matches; second: tree-sitter-kotlin
  gap:        generic tree-sitter extractor tier, .rs wiring, unresolved-only edges, grammar dep + D-### decision record.

item FR-012: "discover_repos optionally includes initialized submodules/nested child repos (persisted config, default off, prefixed repo id); linked worktree with no store seeds from main checkout + reindex drift"
  evidence:   discover_repos is immediate-children-only: src/cairn/graph/scanner.py:161
              `def discover_repos(workspace)` — iterates `sorted(root.iterdir())`, appends
              children containing `.git` (lines 173-181), single-repo root fallback (:183-186).
              No submodule/nested-repo recursion (child dirs of a repo are never descended)
              and no worktree awareness: `rg -n "\bworktree\b|git worktree|\.gitmodules" src/cairn` → 0 matches.
              No persisted include flag: graph config keys are exclude/include/repo_namespaces/
              ingest only (src/cairn/graph/config.py CairnConfig, context file verified against
              config.py this session). Drift machinery to reuse for worktree refresh:
              watcher.py:59 _detect_changed + incremental.py:24 reindex_paths.
  status:     TODO
  verify:     rg -n "def discover_repos" src/cairn/graph/scanner.py; rg -n "\bworktree\b|git worktree|\.gitmodules" src/cairn -g '!dashboard/static/**'   # 161; second: 0 matches
  gap:        config flag, submodule/nested discovery, prefixed repo ids, worktree store-seeding path.
```

## Supporting evidence (load-bearing symbols)

- `reindex_paths` — src/cairn/graph/incremental.py:24; delete+reparse+resolver-repair contract in its docstring.
- `_detect_changed` (size/mtime probe) — src/cairn/graph/watcher.py:59 (size :94, mtime :97); duplicated inline at src/cairn/graph/incremental.py:871-873.
- `_staleness_banner` — src/cairn/mcp_server/_server_core.py:242; pending_sync-only, watcher-fed (docstring :249-258); sole results-aware call site src/cairn/mcp_server/tools_graph.py:135.
- `_read_only_mode` / `_conn` — src/cairn/mcp_server/_server_core.py:92 and :164; CAIRN_READ_ONLY wiring from `cairn serve` (src/cairn/cli/serve.py:65-82).
- `impact_analysis` — src/cairn/graph/traversal.py:156; precise default, fuzzy opt-in, closure-index fast path, limit/truncated.
- symbols/edges schema — src/cairn/graph/schema.py:35-46 (spans), :48-56 (edges incl. target_name), :420-425 (resolution values), :440-446 (parameters/return_type/body migrations).
- MCP tool inventory gate — src/cairn/mcp_server/server.py:56 `_EXPECTED_TOOL_COUNT = 22`, assert :171-173; FR-005/FR-006 additions must bump it.
- wiki/compass writers — src/cairn/wiki/generator.py:31/75-101 (whole-body regen), src/cairn/compass/generator.py:263 (template body), src/cairn/cli/wiki.py:328 (append-only enrich); critic src/cairn/compass/critic.py:54 over full body.
- viz renderers — src/cairn/viz/renderers.py:8 to_mermaid (+to_dot/to_json/embed per viz/__init__.py:7); CLI format/output surface src/cairn/cli/hooks_viz.py:41,:80.
- scanner discovery — src/cairn/graph/scanner.py:161 discover_repos (immediate children + single-repo fallback only).

## Counts observed this session (never inherited)

- MCP graph-layer tools named by FR-001: 8 (tools_graph.py lines 52, 87, 187, 269, 409, 577, 734, 828).
- CLI query commands named by FR-001: 5 (query.py def/callers/search/callees/impact at :16/:48/:79/:110/:143; `context` separately in ask_context.py:46).
- `_staleness_banner` result-aware call sites in tools_graph.py: 1 (line 135; import at :26).
- tree-sitter grammar pins in pyproject.toml: 14 (lines 29-42), Rust grammars among them: 0.
- vendored grammars under vendor/: 1 (tree-sitter-kotlin).
- `_EXPECTED_TOOL_COUNT`: 22 (server.py:56).
- tests directory file count: 222 files incl. __init__/conftest (`ls tests | wc -l`).
- matches for `blast` command / `repo_map` / `file_api` / CLI `grep` / `pyright` / `worktree` / `CAIRN_NO_REFRESH` / `--no-refresh` in the relevant src trees: 0 each (commands run this session; see per-item verify).

## Verify commands run this session

- `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_watcher_service.py -q` → PASS (11 passed).
- `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_traversal_parity.py tests/test_incremental_derived.py -q` → PASS (72 passed).
- `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_enrich.py -q` → PASS (8 passed).
- All per-item `rg` verify commands above executed this session; the "0 matches" results are observed exits, not assumptions.

## Self-check

`python3 /Users/tanle/.agents/skills/spec-to-prod/scripts/check.py specs/graft-parity --repo . --survey-only` — result recorded below after run (0 FAIL required).

PASS (0 fail, 0 warn) — run 2026-09-16, this session.

# Survey: wiki-generation

**Created**: 2026-08-31 | **Baseline**: 0.16.0 @ 264647ae4cf286e7efed52afc87d98589b81258a
Phase-A output — the single source of truth for code state. Every citation
below is `file:symbol:line` (or `file:line`) pasted from this session's
grep/read output against the baseline commit. specs/context/ existed from
2026-08-28 (baseline fe7a7f09) — it was re-counted where this survey touched
it; drift corrections are recorded at the bottom and applied to context files.

## Items

```
item FR-001: "deterministic wiki page plan from the code graph (overview + top modules by incoming degree, --pages cap default 10; page id/title/description/module/seeds/input-hash)"
  evidence:   No page planner exists. Substrate that does exist:
              `src/cairn/graph/stats.py:get_stats:17` (repos/files/symbols/edges/imports
              counts, by_kind, by_repo), `src/cairn/graph/stats.py:group_by_top_level:67`
              (symbols bucketed by first 2-3 path segments — the only "module" notion;
              ranked by symbol count, NOT by incoming degree),
              `src/cairn/wiki/generator.py:_graph_derived_wiki:45` (single stats page;
              its top-classes query at generator.py lines 59-67 ranks symbols by
              `COUNT(e.id) AS incoming ... ORDER BY incoming DESC LIMIT 10` — symbol
              degree, not module degree), `src/cairn/viz/query.py:get_module_graph:190`
              (dashboard module scope; candidates ranked `ORDER BY degree DESC, s.name ASC`
              at lines 218-226 — fan-in + fan-out per symbol, `WHERE f.path LIKE ?`).
              No function anywhere ranks modules/directories by incoming reference
              degree; no `--pages` flag exists (`src/cairn/cli/wiki.py:wiki_generate:21`
              has only --repo/--db/--knowledge/--dry-run/--show-rejections); no page
              plan structure or input hash exists.
  status:     PARTIAL
  verify:     CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_tasks_safety.py tests/test_compass_critic.py -q  # 24 passed; planner substrate (imported by the wiki generator) works
  gap:        No module-degree ranking (files table has no module/directory column —
              module must be derived from files.path; schema at
              src/cairn/graph/schema.py lines 24-33: id, repo_id, path, language, hash,
              line_count, indexed_at). No page-plan record (id/title/description/
              module/seeds/input-hash), no --pages cap, no overview+top-modules
              outline. Everything FR-001 names is new code over the query substrate above.

item FR-002: "one pending task per page under a `wiki-page` task kind; facts carry seeds; output spec requires markdown article ending in `## Sources` footer; Mermaid-fence instructions only when diagrams requested; no references outside the graph"
  evidence:   `src/cairn/llm/tasks.py:create_task:64` is kind-agnostic
              (`create_task(bundle, task_kind, resource, facts=None, parent_attempt=0)`,
              id = `uuid.uuid4().hex[:12]`, writes via `bundle.write_concept` — any
              new kind works with zero queue changes). The existing bare kind is the
              one-line spec at `src/cairn/llm/tasks.py:_output_spec:501` line 527:
              `"wiki": "Write an architectural wiki article in markdown. Only reference graph-verified symbols.",`
              — no Sources footer, no Mermaid gating, no no-external-refs rule.
              Facts pass through verbatim (`facts: Dict[str, Any]` on
              `src/cairn/llm/tasks.py:Task:42`; rendered into the task body by
              `src/cairn/llm/tasks.py:_render_body:484` as
              "## Facts (graph-grounded — do not invent beyond these)"). The only
              facts mutation is the memory-* privacy strip (tasks.py lines 81-87).
              No `wiki-page` or `wiki-catalog` kind exists anywhere in src/.
  status:     PARTIAL
  verify:     CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_tasks_safety.py -q  # 8 passed
  gap:        New `wiki-page` kind + full output-spec text (Sources footer requirement,
              conditional Mermaid-fence instructions gated on a facts.diagrams value,
              reference-scope restriction); queueing loop that creates one task per
              planned page. Existing bare `wiki` output spec is a placeholder.

item FR-003: "on critic-passing completion of wiki-page (or revise), promote to a Wiki-Article concept under the wiki area with `sources` frontmatter populated from the verified footer + lineage extensions (page id, input hash)"
  evidence:   `src/cairn/llm/tasks.py:complete_task:210` (signature
              `(bundle, task_id, result, conn=None, claimer=None)`) writes a
              Task-Result concept (lines 267-275), runs the critic when conn is
              provided (line 290 `critic_result = critic_concept(result_concept, conn)`),
              and has exactly two promotion branches: compass (tasks.py lines 307-328,
              writes `type="Compass"` concept at `compass/<module>`) and flow
              (tasks.py lines 330-352, `compass/flow-{safe_id}`). A passed task whose
              kind matches neither returns `promoted: False` (line 362) — i.e. a wiki
              completion today promotes nothing. Critic verdict machinery:
              `src/cairn/compass/critic.py:critic_concept:38` →
              `src/cairn/compass/critic.py:CriticResult:28`
              (fields `errors: List[str]`, `warnings: List[str]`,
              `quality_score: float = 0.0`, `passed: bool = False`); unresolved file
              refs become ERRORS (critic.py line 51), unknown symbol refs become
              WARNINGS (line 57), prose-heavy/low-ref is a WARNING (lines 126-144);
              `passed = len(errors) == 0 and quality >= threshold` with
              threshold 0.7 if warnings else 0.5 (critic.py lines 94-95).
              Backtick extraction lives in `src/cairn/refs.py:extract_file_refs:38`,
              `src/cairn/refs.py:extract_symbol_refs:50`,
              `src/cairn/refs.py:file_exists:67`, `src/cairn/refs.py:symbol_exists:83`;
              `BACKTICK_RE` at refs.py:19. The OKF `sources` field exists and is
              NEVER populated today: `src/cairn/okf/concept.py:OKFConcept:59`
              declares `sources: Optional[List[Dict[str, Any]]] = None` (line 77);
              parse path pops `sources` from frontmatter (concept.py line 120);
              `src/cairn/okf/concept.py:to_markdown:166` emits it only
              `if self.sources` (lines 190-191). Grep over src/ + tests/ this
              session: `sources=` appears ONLY at concept.py:136 (the parse path) —
              no producer sets it. Existing wiki-area concept shape:
              `src/cairn/wiki/generator.py:_graph_derived_wiki:45` writes
              `type="Wiki-Architecture"`, `concept_id=f"wiki/architecture/{repo}"`
              (generator.py lines 97, 103), no sources.
  status:     PARTIAL
  verify:     CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_compass_critic.py -q  # 16 passed
  gap:        No Wiki-Article promotion branch in complete_task; no Sources-footer
              parser (critic verifies backticks of the whole body, it does not parse
              a `## Sources` section); sources frontmatter never populated by any
              code path; no lineage extensions (page id / input hash) writer.

item FR-004: "on critic-failing completion, spawn a revise task under the existing bounded retry cycle; no promotion for that attempt"
  evidence:   The revise cycle is kind-agnostic and already fires for ANY kind,
              including a bare `wiki` task: `src/cairn/llm/tasks.py:complete_task:210`
              lines 368-403 — `if task.attempt < MAX_REVISE_CYCLES:` spawns
              `create_task(bundle, task_kind=revise_kind, resource=task.resource,
              facts={**task.facts, "errors": critic_result.errors,
              "parent_task_id": task_id}, parent_attempt=task.attempt)`;
              a kind ending `-synthesize` maps to `-revise`, any other kind gets
              `-revise` appended (lines 373-376). `MAX_REVISE_CYCLES = 3`
              (tasks.py:28); at max cycles the task is DROPPED with
              `event="dropped"` telemetry and `dropped: True` in the outcome
              (lines 404-419). No concept is written on the failing attempt beyond
              the Task-Result sibling (marked `critic_status: "failed"`, lines
              293-297). `CLAIM_STALE_SECONDS = 3600` leak handling at tasks.py:34.
  status:     DONE
  verify:     CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_tasks_safety.py -q  # 8 passed (critic branch + revise spawn + drop coverage)
  gap:        None for the mechanism — it works unmodified for a future wiki-page
              kind (the FR's behavior is the existing generic branch; only FR-002's
              kind must exist for it to trigger).

item FR-005: "persisted wiki manifest (per-page plan entry, input hash, task id, state); generate without --force skips pages whose hash matches and concept is promoted"
  evidence:   No wiki manifest exists anywhere in src/ (rg 'manifest' src/cairn/
              this session matches only `src/cairn/bench/datasource.py`
              (`MANIFEST_SCHEMA = "cairn-bench-datasource-n"` and its JSON I/O) —
              a perf-corpus artifact, unrelated to wiki). No --force flag exists
              (cli/wiki.py generate has no such option). The atomic-write pattern
              the spec references is `src/cairn/paths.py:set_config_values:292`
              (tmpfile via tempfile.mkstemp in target dir, flush + os.fsync,
              `os.replace(tmp, CONFIG_FILE)` at line 315, unlink-on-error,
              returns False on OSError); `OKFConcept.to_file` uses the same
              tmp + os.replace shape (src/cairn/okf/concept.py:to_file:145,
              os.replace at line 159).
  status:     TODO
  verify:     grep -rn manifest src/cairn/wiki/ src/cairn/llm/ src/cairn/cli/wiki.py  # no manifest code in the wiki/task surface
  gap:        Manifest storage location/format, per-page state machine, hash
              computation over plan inputs, skip logic, --force flag — all new.

item FR-006: "`cairn wiki status` (aggregate manifest + live task states) and `cairn wiki retry` (re-queue exactly failed/dropped; never touch promoted)"
  evidence:   `src/cairn/cli/wiki.py:wiki:9` defines the group with exactly two
              subcommands today: `src/cairn/cli/wiki.py:wiki_generate:21` and
              `src/cairn/cli/wiki.py:wiki_search:74`. CLI conventions to follow:
              option defaults from `.main` imports
              (`from .main import DEFAULT_DB_PATH, get_db, main` at cli/wiki.py:6;
              `--knowledge` default `str(DEFAULT_DB_PATH.parent / ".knowledge")`,
              cli/wiki.py:16, 73); the task group shows the list/filter shape
              (`src/cairn/llm/tasks.py:list_tasks:101` accepts status= and kind=
              filters; cli/task.py wires `--status`/`--kind`). Exit-code convention:
              `sys.exit(1)` on missing resources/errors (cli/task.py lines 44, 76,
              97, 109; cli/knowledge.py throughout). Display helpers available in
              `src/cairn/cli/display.py` (`rail` at display.py:517 — clack-style
              vertical rail used by core.py:38, 301; success/warning/error/info
              at lines 66-82).
  status:     TODO
  verify:     grep -n 'wiki.command' src/cairn/cli/wiki.py  # exactly generate + search registered
  gap:        Both commands, their manifest reads, live-task-state join, retry
              re-queue semantics (attempt counters preserved), and tests.

item FR-007: "`--refine-catalog` queues a `wiki-catalog` refinement task; validated result (entries must map to real modules/files, else revert to deterministic entry) becomes the page plan"
  evidence:   Queueing precedent that generate --llm would mirror:
              `src/cairn/cli/compass.py` lines 96-99 —
              `facts = _gather_facts(conn, module, repo)` then
              `t = create_task(bundle, "compass-synthesize", module, facts=facts)`
              followed by claim/complete instructions echoed to the user
              (compass.py lines 100-105). No `wiki-catalog` kind, no
              `--refine-catalog` flag, no catalog validator exist (rg across src/
              this session: zero matches for `wiki-catalog` / `refine`).
              The critic's reference-vs-graph machinery
              (`src/cairn/refs.py:file_exists:67`,
              `src/cairn/refs.py:symbol_exists:83`) is the closest existing
              validation primitive, but no "page entry maps to a real module"
              validator exists.
  status:     TODO
  verify:     grep -rn 'wiki-catalog\|refine.catalog' src/ tests/  # no matches
  gap:        Flag, task kind, entry validation, deterministic-entry fallback, and
              the ordering rule (page tasks spawn only after the catalog task
              resolves — today's queue has no task-spawns-tasks-on-completion
              hook beyond the revise cycle).

item FR-008: "MCP tool `wiki_generate` (repo, pages, refine-catalog, diagrams, force) returning page plan + queued task ids; server expected tool count 27 → 28"
  evidence:   Count re-verified this session: `@mcp.tool` registrations = 5 in
              tools_compass.py + 10 in tools_graph.py + 5 in tools_knowledge.py +
              8 in tools_memory.py = 27 distinct decorated functions (the 28th raw
              `@mcp.tool` grep hit is the docstring line at
              src/cairn/mcp_server/tools_graph.py:6). `_EXPECTED_TOOL_COUNT = 27`
              at src/cairn/mcp_server/server.py:55;
              `src/cairn/mcp_server/server.py:verify_tool_count:162` asserts
              `actual == _EXPECTED_TOOL_COUNT` (lines 169-173) and `run()` calls it
              at server.py line 201. The pinned test asserts the number:
              tests/test_status_resource_health.py line 281
              `assert _EXPECTED_TOOL_COUNT == 27`; also
              tests/test_server_robustness.py line 192 and
              tests/test_agent_surface.py (tool-count reference at line 11).
              Registration is import-side-effect: server.py lines 49-52
              `from . import tools_compass  # noqa: F401` etc. against the shared
              FastMCP singleton — `src/cairn/mcp_server/_server_core.py` defines
              `mcp = FastMCP("cairn", ...)` at line 78 with helpers
              `src/cairn/mcp_server/_server_core.py:_conn:159` (read-only pooled),
              `src/cairn/mcp_server/_server_core.py:_rw_conn:212` (writable),
              `src/cairn/mcp_server/_server_core.py:_bundle:222` (per-workspace
              OKFBundle). Tool pattern (tools_knowledge.py lines 31-58):
              `@mcp.tool(annotations=ToolAnnotations(...))` stacked over
              `@instrument`, primitive args only, lazy imports inside the body,
              `_clamp` for LLM-supplied ints. Structured-output pattern exists:
              src/cairn/mcp_server/structured.py:31 `class GetCallersResult(BaseModel)`
              + `structured_output=True` on the decorator
              (tools_graph.py line 80). No wiki_* tool exists in any tools_*.py.
  status:     TODO
  verify:     CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_status_resource_health.py -q  # 11 passed (pins _EXPECTED_TOOL_COUNT == 27 at line 281)
  gap:        The tool itself, plus coordinated bumps: server.py:55 27→28,
              tests/test_status_resource_health.py:281, docs/mcp-tools.md "The 27
              tools by layer" heading (line 21).

item FR-009: "dashboard wiki view (page list with states + page detail rendering markdown body and sources) following the existing dashboard data/route/template pattern"
  evidence:   Route table at src/cairn/dashboard/app.py lines 925-965 (Starlette
              `Route(...)` list inside
              `src/cairn/dashboard/app.py:create_app:191`); current GET views
              include `Route("/memory", memory, name="memory")` (line 942) and
              `Route("/tasks", tasks, name="tasks")` (line 943). Store selection:
              `src/cairn/dashboard/app.py:resolve_selection:310` returns
              `(db, knowledge_root, store_key)` from a `store` registry key.
              Data-layer precedent:
              `src/cairn/dashboard/data.py:get_task_queue:613` (wraps
              `list_tasks(OKFBundle(knowledge_dir), status=...)` as plain dicts)
              and `src/cairn/dashboard/data.py:get_recent_memories:587` (iterates
              `bundle.list_concepts(prefix=...)`, skips unreadable files). Handlers
              `memory`/`tasks` at app.py lines 663-694 render via the shared
              `render(request, name, context)` helper (app.py lines 301-308, which
              injects the embed banner). Templates: tasks.html is a table of
              `{{ t.id }}/{{ t.kind }}/{{ t.status }}` badges
              (src/cairn/dashboard/templates/tasks.html lines 18-45); NO template
              renders markdown today — rg for markdown/md_to_html across
              src/cairn/dashboard/templates/ returns nothing; bodies are shown
              via HTML tables only. Import-guard constraint:
              tests/test_dashboard_app.py line 41
              `test_importing_dashboard_never_loads_server_stack` — starlette/
              uvicorn/jinja2 imports must stay inside create_app/data functions.
  status:     TODO
  verify:     CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_dashboard_app.py::test_importing_dashboard_never_loads_server_stack -q  # 1 passed
  gap:        New route(s), data function reading wiki/ concepts + manifest, wiki
              template(s), and a markdown-rendering decision (nothing in the
              dashboard renders markdown today — verified absence).

item FR-010: "wiki pages first-class knowledge (searchable via knowledge search, compass routing over wiki area; OKF frontmatter unchanged except populated `sources`)"
  evidence:   Wiki concepts are already plain OKF concepts under `wiki/`, so
              bundle-wide search reaches them:
              `src/cairn/okf/bundle.py:search:182` scores title/description/body/
              tags with no area filter (used by `cairn wiki search`, cli/wiki.py
              lines 74-86); compass routing has a wiki layer:
              src/cairn/compass/router.py line 39 (`# L2: wiki`), line 108
              (`results["wiki"] = _search_wiki(query, bundle)`),
              `src/cairn/compass/router.py` `_search_wiki` at line 237. Caveat
              found: the BUSINESS-docs searcher is scoped away from wiki —
              src/cairn/knowledge/search.py line 89: "we only want knowledge/
              prefixed concepts" (`bundle.list_concepts(prefix="knowledge/")`).
              Frontmatter: OKF v0.2 (`OKF_VERSION = "0.2"`, concept.py:23);
              `to_markdown` preserves all current fields and adds sources only
              when set (concept.py lines 166-197), so "unchanged except sources"
              holds structurally. Today's only wiki producer writes
              `type="Wiki-Architecture"` with tags `[repo, "architecture"]`
              (wiki/generator.py lines 97-104).
  status:     PARTIAL
  verify:     CAIRN_LIB=/tmp/__no_such_lib__ uv run cairn wiki generate --dry-run  # observed: 1 concept wiki/architecture/cairn, passed: False quality 0.00 errors 0 warnings 0 — deterministic page fails its own 5-section quality heuristic (informational; nothing written)
  gap:        New Wiki-Article type/concept_id convention must land inside the
              already-wired search paths (nothing to build for search itself);
              the only substantive piece is populating `sources` (FR-003). If
              "knowledge search" is read as the MCP knowledge_search tool, wiki/
              concepts are OUT of its scope by design (search.py:89) — the
              compass-layer search_knowledge/ask_compass is the wired path.

item FR-011: "docs (CLI reference, MCP tools reference, knowledge/memory docs) + CHANGELOG [Unreleased] entry"
  evidence:   Touchpoints confirmed this session: docs/cli-reference.md section
              `## Compass / wiki / tasks / dataflow` at line 73 (sections: Build
              & lifecycle:8, Query:20, Embeddings & rerank:35, Knowledge:42,
              Memory:54, Serving & surfaces:65, Health & ops:82);
              docs/mcp-tools.md `## The 27 tools by layer` at line 21 (plus
              Server:8, Choosing tools:67, CLI fallback:75);
              docs/knowledge-and-memory.md `## Compass, wiki, and the LLM task
              queue` at line 82 (plus OKF bundle:6, Doc ingestion:15, Memory
              tiers:55); CHANGELOG.md `## [Unreleased]` at line 14 with an open
              `### Added` subsection (line 16).
  status:     TODO
  verify:     grep -n 'wiki' docs/cli-reference.md docs/mcp-tools.md | head  # current wiki mentions are the deterministic commands only
  gap:        All four documents need their new sections/entries; mcp-tools.md
              heading text itself carries the count ("The 27 tools").
```

## Supporting evidence (load-bearing machinery tech will cite)

### Task queue — `src/cairn/llm/tasks.py` (read in full this session)
- Layout: `TASK_DIR = "_tasks"` (tasks.py:27); tasks are OKF concepts
  `type="Task"` at `_tasks/<id>.md`; results at `_tasks/<id>.result.md`
  (`Task.result_concept_id` property, tasks.py lines 60-61). Claim markers are
  `.claim` siblings (`bundle.root / f"{TASK_DIR}/{task_id}.claim"`, tasks.py
  line 132).
- `src/cairn/llm/tasks.py:Task:42` fields: id, task_kind, resource,
  facts (dict), status (pending | in-progress | done | failed), assigned_to,
  result_path, attempt, created_at, completed_at, claimed_at.
- `src/cairn/llm/tasks.py:create_task:64` — returns the persisted Task;
  memory-* facts get privacy-stripped (lines 81-87); everything else passes
  through.
- `src/cairn/llm/tasks.py:claim_task:124` — atomic via
  `os.open(claim_marker, os.O_CREAT | os.O_EXCL)` (line 136); stale-marker
  reclaim after `CLAIM_STALE_SECONDS = 3600` (tasks.py:34,
  `src/cairn/llm/tasks.py:_try_remove_stale_marker:189`); re-reads status to
  confirm still pending (lines 157-164); sets in-progress + assigned_to +
  claimed_at; emits `TASK_LIFECYCLE` telemetry event "claimed".
- `src/cairn/llm/tasks.py:complete_task:210` — ownership guard
  (`claimer is not None and task.assigned_to and claimer != task.assigned_to`
  → dropped with "ownership mismatch", lines 237-248); result persisted as
  Task-Result concept; critic runs only `if conn is not None` (line 286);
  critic exception → treated as non-failure with
  `errors: ["critic execution failed"]`, nothing promoted (lines 420-429);
  no conn → plain completion, `promoted: False` (lines 431-439).
- Promotion branches (critic-passed): compass at lines 307-328
  (concept_id `compass/{module with / → -}`, warnings prepended as
  `> [critic-warning] ...` marker lines 312-316), flow at lines 330-352
  (concept_id `compass/flow-{safe_id}`). A wiki branch does not exist.
- Revise spawn (critic-failed): lines 368-403 — kind mapping
  (`*-synthesize` → `*-revise`, else append `-revise`), facts extended with
  `errors` + `parent_task_id`, `parent_attempt=task.attempt`; drop path lines
  404-419.
- Round-trip: `src/cairn/llm/tasks.py:_task_to_concept:457` — extension keys
  `task_kind, assigned_to, attempt, result_path, completed_at, claimed_at,
  facts`; status rides the OKF v0.2 first-class `status` field (line 477).
  `src/cairn/llm/tasks.py:_concept_to_task:534` — status falls back to
  `ext.get("status", "pending")` for pre-v0.2 files (lines 537-538).
- `src/cairn/llm/tasks.py:list_tasks:101` — skips `.result` ids, filters by
  status and kind. `read_result` at tasks.py:442; `get_task` at 451.

### Critic — `src/cairn/compass/critic.py` (read in full)
- `src/cairn/compass/critic.py:critic_concept:38`
  `(concept, conn, llm_judge=None) -> CriticResult`. Verifies ONLY backtick
  refs: file refs → errors when `not _file_exists(conn, ref)` (lines 49-51);
  symbol refs → warnings (lines 54-57); prose-heavy/low-ref guard → warning
  (`PROSE_HEAVY_MIN_CHARS = 400`, `PROSE_HEAVY_MIN_REFS = 2`, lines 113-114,
  `src/cairn/compass/critic.py:_prose_heavy_warning:126` — headings excluded
  from prose count, lines 133-136). Quality heuristic: fraction of 5 known
  section headings present, `min(sections / 5.0, 1.0)` (lines 73-90);
  `passed = len(errors) == 0 and quality >= (0.7 if warnings else 0.5)`
  (lines 94-95).
- `src/cairn/compass/critic.py:validate_paths:149` — bundle-wide stale scan
  (marks, never deletes).
- Consumers: complete_task (tasks.py line 290) and the deterministic
  generators (wiki/generator.py line 41, compass generator, cli/wiki.py
  dry-run).

### OKF layer
- `src/cairn/okf/concept.py:OKFConcept:59` — required `type`; common
  title/description/resource/tags/timestamp; v0.2 families generated_by,
  sources (line 77), verified, status, stale_after; `concept_id`; body;
  extensions dict. Parse via `src/cairn/okf/concept.py:from_file:92` (sources
  popped at line 120; bare `verified` mapping normalized to a list, lines
  122-125). Serialize via `src/cairn/okf/concept.py:to_markdown:166`
  (`generated: {by, at}` wire format lines 182-185; sources emitted only when
  truthy, lines 190-191; extensions merged last, line 195).
  `src/cairn/okf/concept.py:to_file:145` — atomic write
  (tmp `{path}.{pid}.tmp` + os.replace, lines 152-164).
- `src/cairn/okf/bundle.py:OKFBundle:91` —
  `src/cairn/okf/bundle.py:read_concept:119`,
  `src/cairn/okf/bundle.py:write_concept:124` (validates concept_id stays in
  root via `_validate_concept_path` at bundle.py:106; invalidates the lazy
  search index; appends to log.md),
  `src/cairn/okf/bundle.py:list_concepts:144` (rglob *.md, skips
  index.md/log.md, prefix filter, sorted),
  `src/cairn/okf/bundle.py:search:182` (lazy in-memory index, invalidated on
  write), `src/cairn/okf/bundle.py:lock:102` (flock cross-process mutex,
  `_okf_bundle_lock` at bundle.py:45, 5s default timeout, thread-local
  re-entrancy).
- `sources=` producer census (this session): src/ + tests/ grep returns
  concept.py:136 only — no code constructs a concept with sources.
- concept_id scheme: bundle-root-relative path without `.md`
  (e.g. `wiki/architecture/cairn`, `_tasks/<12-hex>`).

### Existing wiki surface
- `src/cairn/wiki/generator.py:generate_wiki:25` and
  `src/cairn/wiki/generator.py:generate_wiki_with_critic:31` — deterministic,
  no LLM; single concept per repo; critic run is informational (write proceeds
  regardless; cli/wiki.py prints verdicts). Generator's graph inputs:
  `get_stats(conn)` + `cross_repo_deps(conn, repo)` imported from
  `..graph.queries` (generator.py line 20) — re-exports; implementations at
  `src/cairn/graph/stats.py:get_stats:17` and
  `src/cairn/graph/cross_repo.py:cross_repo_deps:125`
  (returns {dependencies, dependents} ranked by count).
- `src/cairn/cli/wiki.py` — group `wiki` (line 9); generate flags --repo/--db/
  --knowledge/--dry-run/--show-rejections (lines 14-20); wiring
  `conn = get_db(db)` + `bundle = OKFBundle(knowledge)` (lines 25-27); repos
  default to `SELECT id FROM repos` (line 28). search wraps bundle.search.

### Graph queries usable by a catalog planner
- `src/cairn/graph/stats.py:group_by_top_level:67` — directory buckets
  (first 2-3 path segments, legacy absolute-path strip) with symbol counts,
  sorted descending. `get_tree` at stats.py:62 delegates to it.
- Incoming-degree precedent (symbol level): wiki/generator.py lines 59-67
  (`LEFT JOIN edges e ON e.target_id = s.id ... ORDER BY incoming DESC`).
- Dashboard module scope: `src/cairn/viz/query.py:get_module_graph:190` —
  degree = fan-in + fan-out subselects (lines 218-226), vendored-path and
  test-symbol exclusion, `_MODULE_CAP` truncation with honest metadata.
- Schema (src/cairn/graph/schema.py): repos:15, files:24 (NO module/directory
  column — module is path-derived), symbols:35, edges:49, imports:59;
  indexes at lines 67-74. "Module" elsewhere in the codebase means a file
  path or path prefix (compass resources are module paths; get_module_graph
  matches `f.path LIKE %module%`).

### CLI conventions
- Registration: each `cli/*.py` does `from .main import ... main` and
  decorates `@main.group()` / `@<group>.command()`; `src/cairn/cli/__init__.py`
  imports every module for side effects (lines 21-44; wiki imported at line 43).
  Its docstring claims "The 49 commands live in split modules" — recount this
  session: 47 `@main.command|@main.group` decorator lines across cli/*.py
  (the docstring counts subcommands, not decorators; treat 49 as stale prose).
- Shared helpers: `DEFAULT_DB_PATH, get_db` re-exported from graph.schema
  (cli/main.py line 18); `--knowledge` default
  `str(DEFAULT_DB_PATH.parent / ".knowledge")` pattern (cli/wiki.py:16,
  cli/task.py:25); `sys.exit(1)` + `click.echo(..., err=True)` on errors.
- Display: `src/cairn/cli/display.py:rail:517` (vertical-rail flow used by
  `cairn init`/`cairn build`), plain echo in the task/wiki commands today.
- Representative CliRunner test: tests/test_knowledge_cli.py — `cli_env`
  fixture at lines 25-31 (chdir tmp_path; CAIRN_DB + CAIRN_KNOWLEDGE into tmp),
  `CliRunner().invoke(knowledge, [...])`, asserts on exit_code + stdout + stored
  docs via `resolve_store()`.

### MCP conventions
- Singleton + helpers in `src/cairn/mcp_server/_server_core.py` (mcp at line
  78; `src/cairn/mcp_server/_server_core.py:_conn:159`,
  `src/cairn/mcp_server/_server_core.py:_rw_conn:212`,
  `src/cairn/mcp_server/_server_core.py:_bundle:222`).
- server.py: metric/embed buffering wired BEFORE tools imports (lines 32-44);
  tools registered by import side effect (lines 49-52);
  `_EXPECTED_TOOL_COUNT = 27` (line 55);
  `src/cairn/mcp_server/server.py:_count_fastmcp_tools:148` (counts
  `mcp._tool_manager.list_tools()`, returns 0 on SDK drift);
  `src/cairn/mcp_server/server.py:verify_tool_count:162` called from
  `src/cairn/mcp_server/server.py:run:176` (line 201). Deliberately not an
  import-time assert.
- Tool body pattern (tools_knowledge.py knowledge_add, lines 31-58):
  `@mcp.tool(annotations=ToolAnnotations(readOnlyHint=..., destructiveHint=...,
  idempotentHint=...))` over `@instrument`; primitives only; lazy function-body
  imports; `_clamp` for ints; prose string return (or Pydantic model when
  `structured_output=True` — see src/cairn/mcp_server/structured.py:31
  `class GetCallersResult(BaseModel)` and tools_graph.py line 80).
- Consumers of the count: tests/test_status_resource_health.py:281
  (`assert _EXPECTED_TOOL_COUNT == 27`), tests/test_server_robustness.py:192,
  tests/test_agent_surface.py:11, docs/mcp-tools.md:21 heading.

### Dashboard
- `src/cairn/dashboard/app.py:create_app:191` (Starlette factory; all heavy
  imports inside the factory — the import-guard test enforces it);
  `src/cairn/dashboard/app.py:resolve_selection:310` (store-key →
  `<store>/.kg` + `<store>/.knowledge`); route table lines 925-965; plain-def
  handlers run in a threadpool (comment at lines 349-350).
- Data pattern: `src/cairn/dashboard/data.py:get_task_queue:613` and
  `src/cairn/dashboard/data.py:get_recent_memories:587` — both take
  knowledge_dir, wrap OKFBundle reads, return plain dicts, skip unreadable
  concepts. `get_read_only_db` at data.py:1069 (mode=ro enforced;
  tests/test_dashboard_app.py:56 pins it).
- Templates: 16 files in src/cairn/dashboard/templates/ (base.html + views
  incl. tasks.html, memory.html). None render markdown (verified by grep —
  tasks/memory render HTML tables; tasks.html lines 18-45).

### Atomic-write pattern (FR-005 manifest precedent)
- `src/cairn/paths.py:set_config_values:292`: mkdir parent → tempfile.mkstemp
  in target dir → write + flush + os.fsync (comment lines 309-314: fsync
  BEFORE replace so a crash can't persist a zero-length file) →
  `os.replace(tmp, CONFIG_FILE)` (line 315) → unlink tmp on any exception
  (lines 316-321) → warn + return False on OSError (lines 322-326).
  OKFConcept.to_file (concept.py:145) is the second instance of the shape.

### Test infrastructure
- `tests/conftest.py` — suite-wide autouse hermetic fixture
  (`_hermetic_env`, tests/conftest.py line 37): HOME/CAIRN_HOME into tmp
  sandbox, all CAIRN_* cleared then re-pinned, `CONFIG_FILE` re-pointed,
  agent CLIs blocked from shutil.which (`_AGENT_CLIS` tuple at line 33).
  `fresh_db` fixture at line 106 (in-memory SQLite, full schema, Row factory,
  FKs off).
- Task-queue test pattern: tests/test_tasks_safety.py — seeds a minimal graph
  (`_seed_graph`, lines 27-38), builds an OKFBundle under tmp_path
  (`_create_bundle`, lines 44-50), drives claim/complete directly and asserts
  on the returned outcome dicts; class-grouped
  (`TestCompleteTaskCriticIntegration`).
- Suite tripwires: tests/test_suite_hygiene.py —
  `test_no_json_loads_on_interleaved_cli_output` (line 30),
  `test_hermetic_fixture_remains_autouse` (line 57),
  `test_agent_cli_names_covered_by_fixture` (line 71).
- Wiki coverage today: NO test imports `cairn.wiki` or the wiki CLI
  (rg across tests/ this session: zero matches for
  `cairn.wiki|wiki_generate|generate_wiki`). Wiki appears only as a task-kind
  string in telemetry/dashboard tests (tests/test_metrics_extensions.py lines
  422-463; tests/test_dashboard_data.py:841; tests/test_dashboard_app.py:852)
  and the critic file's docstring (tests/test_compass_critic.py:1).
- Canonical invocation (used for every verify run this session):
  `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest <path> -q`.
  CAIRN_LIB is the shared-lib override read at src/cairn/paths.py:117 —
  pinning it to a nonexistent path keeps the semantic-lib probe hermetic.
  Note: a single-file plain `uv run pytest tests/test_tasks_safety.py -q`
  also passed this session (8 passed) — the CAIRN_LIB pin matters for
  suite-level/embedding-adjacent runs, not every single file.

### Docs touchpoints
- docs/cli-reference.md:73 `## Compass / wiki / tasks / dataflow` — wiki
  commands documented there today are generate/search only.
- docs/mcp-tools.md:21 `## The 27 tools by layer` — heading text carries the
  count; FR-008 must rewrite it.
- docs/knowledge-and-memory.md:82 `## Compass, wiki, and the LLM task queue`.
- CHANGELOG.md:14 `## [Unreleased]`, open `### Added` at line 16 — entries are
  appended as prose bullets (Keep-a-Changelog format, line 7).

## Context drift found (specs/context/ refresh, applied)
1. structure.md cli/ module list omitted `wiki.py` and `_helpers.py` (both
   present in src/cairn/cli/ this session; wiki.py predates the 2026-08-28
   baseline — last touched by commit e48b488, an ancestor of fe7a7f09).
   Refreshed.
2. structure.md mcp_server row says "27 tools across 4 layers" — re-verified,
   still 27 (unchanged).
3. tech.md says `uv run pytest <path> -q` is the standard invocation —
   the pipeline's canonical form is
   `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest <path> -q`
   (CAIRN_LIB override at src/cairn/paths.py:117). Refreshed.
4. tech.md "The 49 commands" figure in cli/__init__.py docstring is 47 by
   decorator recount — noted above; left in structure.md untouched since the
   docstring itself is the cited source and it still says 49.

## Rules
- Every citation above is from this session's grep/read output against
  264647ae4cf286e7efed52afc87d98589b81258a. Unknowns would be written
  `unknown — verify`; none remained at write time.
- Status derives from evidence, not intent; every verify command above was
  run this session with the result recorded in its comment.

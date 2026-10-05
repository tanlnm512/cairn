# Survey: symbol-communities

**Created**: 2026-10-04 | **Baseline**: 0.21.2 @ 2894c1f
The survey node's output — the single source of truth for code state. Every citation
in the other four docs must trace to a line here. Evidence is pasted
verbatim from grep/read output in the session that wrote it.

## Items

```
item FR-001: "`cairn communities` command computing Louvain over the symbol graph, persisting to derived tables `communities` + `symbol_communities` (additive schema, full refresh)"
  evidence:   grep -rn "communities\|louvain" src/cairn tests --include="*.py"  → 0 hits (absence)
              CLI registration: src/cairn/cli/main.py:main:136 is the click group; every command module
              decorates onto it (`@main.command("taint")` at src/cairn/cli/taint.py:14) and is imported
              for its side effect in src/cairn/cli/__init__.py:14-41 (28 `from . import X  # noqa: F401`
              lines; :12-13 "registration is the point").
              Additive derived-table pattern: src/cairn/graph/schema.py:214-216 — "Additive-only: plain
              CREATE TABLE IF NOT EXISTS rides the idempotent executescript in _apply_schema with NO
              MIGRATIONS entry" (exemplar table `embeddings_mv` schema.py:217; derived-table exemplars
              `dataflow` schema.py:343-349, `transitive_edges` schema.py:420). MIGRATIONS list at
              schema.py:521; `_apply_schema` runs it at schema.py:629-632 via get_db (schema.py:928).
              Full-refresh precedent: src/cairn/graph/dataflow.py:485 `cur.execute("DELETE FROM
              transitive_edges")` then one sorted executemany ("a single DELETE, then one sorted
              executemany", docstring dataflow.py:474).
  status:     TODO
  verify:     grep -rn "communities\|louvain" src/cairn tests --include="*.py" | wc -l   (0 = absent)
  gap:        new cli module + two CREATE TABLE IF NOT EXISTS blocks in schema.py; no `communities`
              command, tables, or writer exist anywhere

item FR-002: "Clustering input is structural edge kinds only (`calls`/`call`/`extends`/`implements`), kind-weighted; imports and references excluded"
  evidence:   src/cairn/graph/traversal.py:8-12 (verbatim):
              ```
              # Edge kinds that represent in-codebase structural relationships. Service/
              # topology edge kinds (http_call, service_call) are excluded by default; pass
              # ``include_service_edges=True`` to follow them. Both ``"calls"`` and
              # ``"call"`` spellings are included.
              STRUCTURAL_EDGE_KINDS: Tuple[str, ...] = ("calls", "call", "extends", "implements")
              ```
              Closure traverses exactly this set: src/cairn/graph/dataflow.py:build_transitive_closure:460
              docstring :466-468 — "Only **structural** edge kinds (``calls``/``call``/``extends``/
              ``implements``, per :data:`traversal.STRUCTURAL_EDGE_KINDS`) are seeded and extended".
              Taint call-seeding narrows to call edges only: src/cairn/graph/taint.py:95-97
              `_CALL_EDGE_KINDS: tuple[str, ...] = ("calls", "call")`.
              Imports are a separate table, never in `edges`: src/cairn/graph/schema.py:60-66
              `CREATE TABLE IF NOT EXISTS imports (`; edges.kind is free-text (schema.py:50-58), no
              kind filter exists in repo_map's degree counts (see FR-003).
              No kind-weighting machinery exists: grep "weight" over traversal.py/dataflow.py returns
              only depth/closure budget comments — unknown — verify at implementation time what
              weighting schema (per-kind multiplier vs. binary) prior art suggests; none is in-repo.
  status:     PARTIAL
  verify:     grep -n "STRUCTURAL_EDGE_KINDS" src/cairn/graph/traversal.py src/cairn/graph/dataflow.py src/cairn/pack.py
  gap:        the shared kind set exists and is closure/taint-consumed; nothing clusters over it and
              no per-kind weights exist anywhere

item FR-003: "Hub symbols (`god nodes`) by highest combined structural degree, global and per community, top-K with K defaulting to 10"
  evidence:   Nearest existing machinery — `cairn map` per-cluster hubs by in-degree:
              src/cairn/graph/repo_map.py:_clusters:111 sorts members `key=lambda symbol:
              (-symbol.incoming, symbol.path, symbol.qualified_name)` and slices `[:hub_cap]`
              (:131-134); DEFAULT_HUB_CAP = 3 (repo_map.py:11); `cairn map --max-hubs`
              (src/cairn/cli/map.py:30-34) prints "hub ... (in-degree N)" (cli/map.py:81-84).
              CAVEAT: repo_map's in-degree counts ALL edge kinds — the subquery at
              src/cairn/graph/repo_map.py:52-56 is `SELECT COUNT(*) FROM edges AS incoming WHERE
              incoming.target_id = symbols.id` with no kind filter — NOT the structural set.
              Structural-only precedents: src/cairn/pack.py:_closure_centrality:273 (reverse-reach
              count over transitive_edges) and src/cairn/pack.py:_structural_indegree:282 (direct
              in-degree `WHERE target_id = ? AND kind IN (...STRUCTURAL_EDGE_KINDS)`); availability
              probe src/cairn/graph/dataflow.py:closure_available:602.
              No global top-K hub report exists: grep -rn "god\|hub" src/cairn --include="*.py"
              hits only "github" substrings plus the repo_map/pack_enrich sites above.
  status:     PARTIAL
  verify:     grep -rn "DEFAULT_HUB_CAP\|STRUCTURAL_EDGE_KINDS" src/cairn/graph/repo_map.py src/cairn/pack.py
  gap:        no combined in+out structural degree, no global ranking, no per-community hubs, no
              K-default-10 constant; repo_map hubs are all-kind in-degree capped at 3 per cluster

item FR-004: "WHERE `[graph-analytics]` is not installed, fail with an actionable install hint and leave store state unchanged"
              evidence:   Extras live in pyproject.toml `[project.optional-dependencies]` (pyproject.toml:68);
              tomllib inventory ran this session: ['ann', 'bench', 'dev', 'ingest', 'otlp', 'scip',
              'semantic', 'test', 'watch'] — no `graph-analytics`; networkx appears in no
              dependencies/extras list (tomllib scan: networkx direct: False False). It reaches the
              lockfile transitively: uv.lock:4788-4789 (torch deps) and uv.lock:3470-3471
              (pymupdf-layout deps, the ingest arm).
              Runtime-probe convention (lazy import + actionable hint), src/cairn/bench/swe_bench.py:fetch_dataset_rows:89:
              ```
              try:
                  from datasets import load_dataset
              except ImportError as exc:
                  raise ImportError(
                      "fetching SWE-bench rows needs the optional 'datasets' package; "
                      "install it with the bench extra: pip install 'cairn[bench]'"
                  ) from exc
              ```
              (swe_bench.py:95-101). Store-unchanged discipline: get_db opens the DB writable
              (schema.py:928), so the probe must fire before any write; the read-side analog of
              "absent table → degrade, never error" is src/cairn/dashboard/data.py:_knowledge_table_present:951.
  status:     PARTIAL
  verify:     python3 -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); print(sorted(d['project']['optional-dependencies'].keys()))"
  gap:        no `[graph-analytics]` extra and no probe for it; the bench probe is the convention to copy

item FR-005: "Compass deterministic body gains a `Subsystems` section when community data exists (omitted otherwise); critic section vocabulary recognizes it"
  evidence:   Module-compass deterministic body: src/cairn/compass/generator.py:_template_body:277 emits
              exactly 5 sections — "# What Does This Module Do?" (:278), "# Common Modification
              Patterns" (:288), "# Build-Failure Patterns" (:298), "# Cross-Module Dependencies"
              (:315), "# Tribal Knowledge" (:321). Flow-compass body:
              src/cairn/compass/generator.py:_flow_template_body:482 ("# What Does This Flow Do?"
              :491, "# Call Sequence" :498, "# Failure-Prone Steps" :505, "# Modules Spanned" :522), then "# Tribal Knowledge" appended unconditionally (:544) — 5 flow sections total.
              No "Subsystems" heading exists anywhere: grep -rn "Subsystems" src tests --include="*.py"
              hits only src/cairn/telemetry/sink.py:324 (unrelated docstring prose).
              Critic vocabulary: src/cairn/compass/critic.py:75-85 `_DEFAULT_SECTION_VOCAB` = 9
              headings (5 module + 4 flow, verbatim tuple); src/cairn/compass/critic.py:critic_concept:88
              scores quality as "fraction of the recognized section headings present" (:127-134) —
              "A compass is complete at 5 sections even though the default pool spans both compass
              shapes" — threshold `= 0.7 if warnings else 0.5` (:138).
              Heading consumers beyond the critic: the LLM-synthesis prompt enumerates the section
              list (src/cairn/llm/tasks.py:821-822 module, :833 flow); skillgen asserts
              "What Does This Module Do?" in compass_body (tests/test_skillgen_assembly.py:125);
              graft parity pins compass bytes (tests/test_graft_parity_notes.py:134).
              Owner test for template/critic coupling:
              tests/test_compass_generator.py:test_deterministic_template_passes_own_critic:204 —
              `generate_compass` output must pass `critic_concept` with zero errors.
  status:     TODO
  verify:     CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_compass_critic.py tests/test_compass_generator.py -q   (42 passed this session)
  gap:        section absent from _template_body; `_DEFAULT_SECTION_VOCAB` lacks it; the 5-section
              completeness heuristic and tasks.py prompt enumerations must move in lockstep

item FR-006: "Dashboard `/communities` view in the nav inventory with member drill-down; all view-inventory and accessibility pins updated to the new count"
  evidence:   Nav inventory (the pin surface, exhaustive):
              1. src/cairn/dashboard/shell.py:18-24 `NAV_SECTIONS` — 5 sections, 14 view ids;
                 :26-41 `NAV_LABELS` — 14 ids; :43-47 `PALETTE_EXTRA_VIEWS` — 1 palette-only entry.
                 AST-counted this session: NAV_SECTIONS 5 / NAV_LABELS 14 / PALETTE_EXTRA_VIEWS 1.
              2. tests/test_dashboard_shell.py:test_nav_sections_cover_every_view_exactly_once:38 —
                 `assert len(ids) == len(set(ids)) == 14` (:41, the count pin); section labels pinned
                 `== [None, "Explore", "Knowledge", "Activity", "System"]` (:50); positional pins
                 `sections[1]["items"][1]` = graph (:52, :82).
              3. tests/test_dashboard_shell.py:test_every_nav_view_renders_an_icon:141 — every
                 NAV_LABELS id must render `<svg` via the icon macro (:159); the macro has exactly 14
                 when/elif branches, one per nav id (src/cairn/dashboard/templates/_icons.html:8-34).
              4. tests/test_dashboard_accessibility.py:28-45 `_MAIN_VIEWS` — 16 paths ("/" plus 15);
                 the button-name crawl parametrizes over it:
                 tests/test_dashboard_accessibility.py:test_every_button_has_a_non_empty_accessible_name:283.
              5. tests/test_dashboard_restyle.py:23-40 `_MAIN_VIEWS` — same 16; :44 `_NAV_VIEWS`
                 (16 minus "/"); sidebar active-pin:
                 tests/test_dashboard_restyle.py:test_sidebar_flags_exactly_one_active_nav_item:189 —
                 `aria-current="page"` count == 1 per nav view (:196).
              6. tests/test_dashboard_assets.py:23-38 `_MAIN_VIEWS` — same 16; local-assets crawl:
                 tests/test_dashboard_assets.py:test_every_main_view_references_only_local_assets:219.
              7. tests/test_dashboard_readonly.py:27-56 `ROUTES` — the read-only byte-identical
                 contract iterates it (:228, :338, :619); a new view route should join this list.
              8. Packaging is glob-auto-covered, not enumerated:
                 tests/test_dashboard_packaging.py:test_package_data_globs_cover_every_dashboard_data_file:36
                 replays setuptools globbing over templates/ + static/, so a new template file is
                 covered without editing the test.
              Route registration: each route module exposes `register(routes, context)`
              (src/cairn/dashboard/routes/memory.py:register:14); create_app imports the modules
              (src/cairn/dashboard/app.py:332) and calls register (:357-365 — core x2, graph,
              history, memory, knowledge, wiki, settings, editor), then Mount("/static") (:366-372).
              Data readers live in src/cairn/dashboard/data.py (per-view `get_*` functions).
  status:     TODO
  verify:     CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_dashboard_shell.py tests/test_dashboard_accessibility.py tests/test_dashboard_restyle.py -q   (10 passed + 31 passed this session at the 14/16 counts)
  gap:        no /communities route/module/template/icon branch; six count pins to move: shell count 14
              (shell.py NAV_LABELS + test :41), icon branches 14 (_icons.html), and the three 16-path
              _MAIN_VIEWS tuples (accessibility :28-45, restyle :23-40, assets :23-38) plus the
              readonly ROUTES list

item FR-007: "Community results deterministic for an unchanged graph (fixed Louvain seed; same partition, labels, ordering)"
  evidence:   No communities code exists (see FR-001 absence grep). Determinism conventions to reuse:
              src/cairn/bench/corpus.py:generate_corpus:12 — "The generator is deterministic given the
              same ``seed`` and ``n_files``" (:30), `seed: int = DEFAULT_SEED` (:17),
              `rng = random.Random(seed)` (:32).
              networkx verified in this session's venv (py3.14): `import networkx` → version 3.6.1;
              `from networkx.algorithms.community import louvain_communities` resolves (module
              networkx.algorithms.community.louvain) and its signature has `seed`. NOTE: it is NOT
              top-level — `hasattr(networkx, 'louvain_communities')` is False on 3.6.1.
              The py<3.11 lock arm pins networkx 3.4.2 (uv.lock:3470) — not installed in this
              session's venv, so its louvain_communities presence is unknown — verify.
  status:     TODO
  verify:     uv run --no-sync python -c "from networkx.algorithms.community import louvain_communities; import inspect; print('seed' in inspect.signature(louvain_communities).parameters)"   (True on 3.6.1)
  gap:        no seeded double-run test and no communities module; louvain import path + seed param
              verified only on the 3.6.1 arm

item FR-008: "Community labels deterministic and LLM-free (derived from member/edge facts)"
  evidence:   No label machinery exists (FR-001 absence grep). In-repo precedent for deterministic
              LLM-free derivation from graph facts: src/cairn/compass/generator.py:_template_body:277
              builds every line from `symbols`/`key_files`/`cross_deps` with zero model calls (the
              LLM path is a separate opt-in callable, generate_compass :110-115); repo_map labels
              clusters by dominant path segment (`_dominant_segment`, src/cairn/graph/repo_map.py:99,
              threshold `_DOMINANT_SEGMENT_SHARE = 0.60` :13) — a deterministic label from member
              file paths, matching the spec's "dominant file paths" example.
  status:     TODO
  verify:     grep -rn "communities\|louvain" src/cairn tests --include="*.py" | wc -l   (0 = absent)
  gap:        label derivation does not exist; the spec's hub/path-derived examples both have
              deterministic in-repo precedents to copy (repo_map _dominant_segment, compass template)

item NFR-001: "Security — not applicable: no new network or auth surface"
  evidence:   Nothing exists to add one (FR-001 absence grep → 0); the feature surface is CLI +
              SQLite derived tables + a dashboard read view; dashboard reads go through the existing
              read-only connection discipline (src/cairn/dashboard/data.py:1912 `def get_read_only_db`).
  status:     DONE
  verify:     grep -rn "communities\|louvain" src/cairn tests --include="*.py" | wc -l   (0)
  gap:        none — n/a holds while no listener/auth code is introduced

item NFR-002: "Privacy — not applicable: no new data leaves the store"
              evidence:   Same absence ground as NFR-001; the telemetry stack is local-only with OTLP strictly
              opt-in behind the `otlp` extra (pyproject.toml:139-148 comment: "Strictly lazy... the
              default install stays OTel-free"), untouched by this spec.
  status:     DONE
  verify:     grep -rn "communities\|louvain" src/cairn tests --include="*.py" | wc -l   (0)
  gap:        none

item NFR-003: "Performance — `cairn communities` on the 1000-file scaling corpus within 10s wall"
  evidence:   The corpus and gate exist: src/cairn/bench/scaling_suite.py:run_scaling_suite:84 —
              `sizes: Sequence[int] = (100, 500, 1000, 5000)` (:87), fresh corpus + DB per size
              (:94-98), corpus generated seeded (src/cairn/bench/corpus.py:generate_corpus:12);
              structural edges synthesized over it (scaling_suite.py:31
              `def synthesize_structural_edges(`). Budget-gate precedent at the same 1000-file point:
              scaling_suite.py:15 "Closure-op budget at the 1000-file gate point: wall seconds and
              peak traced"; tests/test_scaling_gate.py:_assert_closure_budget:69 asserts
              `point.closure_seconds <= wall_budget` (:73) with GATE_FILES = 1000 (:15).
              No communities timing exists (command absent).
  status:     PARTIAL
  verify:     CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_scaling_gate.py -q -m "not infra"
  gap:        nothing to time until the command exists; a communities budget row would mirror the
              closure-budget gate shape (suite point + asserted budget constant)

item NFR-004: "Reliability — not applicable: derived tables rebuildable; absence degrades to omitted sections/views, never errors"
  evidence:   Read-path degrade convention exists: src/cairn/dashboard/data.py:_knowledge_table_present:951 —
              "A store predating the relationship index renders empty panels, never an error";
              compass template emits explicit no-data fallback lines (generator.py:286 "- (no symbols
              detected in this module)", :296, :319); closure absence degrades to DFS via
              src/cairn/graph/dataflow.py:closure_available:602 ("False on never-built databases...
              falls back to DFS").
  status:     DONE
  verify:     CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_dashboard_accessibility.py -q   (empty-store views render 200; 16 passed this session as part of the 31)
  gap:        none — the degrade contract is established; new readers must reuse it

item NFR-005: "Observability — not applicable: existing telemetry covers CLI runs"
  evidence:   Every CLI invocation is wrapped centrally: src/cairn/cli/main.py:_RecordingGroup:71
              (the group class, `@click.group(cls=_RecordingGroup)` at :125) times and records via
              src/cairn/cli/main.py:_record_invocation:44 ("Buffer one usage row via the cli-metrics
              builder; never raises") inside invoke (:84-102, success and error paths).
  status:     DONE
  verify:     grep -n "_RecordingGroup\|_record_invocation" src/cairn/cli/main.py
  gap:        none — a new `communities` subcommand inherits recording for free

item NFR-006: "Accessibility — the new dashboard view shall pass the same accessibility inventory the existing views are crawled against"
  evidence:   The crawl is the 16-path `_MAIN_VIEWS` inventory (tests/test_dashboard_accessibility.py:28-45)
              — global :focus-visible ring (:138-150), no `outline: none` anywhere (:153-160),
              button-name audit parametrized per view
              (tests/test_dashboard_accessibility.py:test_every_button_has_a_non_empty_accessible_name:283),
              reduced-motion block (:333-357), real table semantics (:365-375). Suite is green today:
              `pytest tests/test_dashboard_accessibility.py tests/test_dashboard_restyle.py -q` →
              "31 passed, 1 warning in 8.29s" this session.
  status:     PARTIAL
  verify:     CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_dashboard_accessibility.py -q
  gap:        /communities is not in `_MAIN_VIEWS`; adding it extends the crawl automatically once
              registered in the tuple — the new view's markup must satisfy the same audits
```

## Supporting evidence

- Dashboard nav-as-data source: src/cairn/dashboard/shell.py:88 `def shell_context(` builds
  `nav.sections` + `palette.views` from NAV_SECTIONS/NAV_LABELS/PALETTE_EXTRA_VIEWS
  (:123-131); palette extras appended at :121-122. AST-counted this session:
  NAV_SECTIONS 5 / NAV_LABELS 14 / PALETTE_EXTRA_VIEWS 1.
- The three `_MAIN_VIEWS` tuples are byte-identical 16-path lists
  (tests/test_dashboard_accessibility.py:28-45, tests/test_dashboard_restyle.py:23-40,
  tests/test_dashboard_assets.py:23-38) — python re-count this session: 16 each, same order.
- Icon branches: src/cairn/dashboard/templates/_icons.html:8-34 — 14 `when/elif name == ...`
  arms (workspaces, projects, graph, history, tokens, chains, health, memory, knowledge, wiki,
  tasks, database, embeddings, settings); the icon test renders NAV_LABELS through it and pins
  `<svg` count == len(NAV_LABELS) (tests/test_dashboard_shell.py:141-159).
- Compass LLM path shares the deterministic facts: src/cairn/compass/generator.py:generate_compass:96
  resolves module → symbols → key files → cross-deps → quick commands, then either the
  llm_synthesize callable or _template_body (:110-115); generate_flow MCP tool critic-gates with
  the default vocab (src/cairn/mcp_server/tools_compass.py:289 `result = critic_concept(concept, conn)`).
- Edge inventory: `edges` table has free-text `kind` (src/cairn/graph/schema.py:50-58) with
  index idx_edges_kind (schema.py:74); imports are a separate table (schema.py:60-66), so
  "imports excluded" holds by construction for any edges-kind-filtered query.
- Derived-table exemplars to copy: `dataflow` (schema.py:343-350, JSON-list payload columns,
  maintained incrementally by src/cairn/graph/dataflow.py:541 `def maintain_dataflow_index(`),
  `transitive_edges` (schema.py:420), both full-refreshed by DELETE + executemany
  (dataflow.py:485, :594).
- repo_map (existing "clusters + hubs" surface `cairn map`): src/cairn/graph/repo_map.py:160
  `def build_repo_map(` with DEFAULT_CLUSTER_CAP = 16 / DEFAULT_HUB_CAP = 3 /
  DEFAULT_HOTSPOT_CAP = 12 (:10-12); degrees are all-kind (subqueries repo_map.py:46-56, no kind
  filter) — distinct from FR-002's structural set.
- Runtime-probe precedents for optional extras: src/cairn/bench/swe_bench.py:fetch_dataset_rows:89
  (`pip install 'cairn[bench]'` hint, :95-101); embeddings import guards
  (src/cairn/graph/embeddings.py:640, :838, :1255, :1263).
- Scaling harness: sizes (100, 500, 1000, 5000) at src/cairn/bench/scaling_suite.py:87; the
  1000-file gate is enforcement-tested (tests/test_scaling_gate.py:15 `GATE_FILES = 1000`).
- CLI command-count context: src/cairn/cli/__init__.py:14-41 imports 28 command modules for
  registration side effects; `cairn = "cairn.cli:main"` entry point (pyproject.toml:175-176).

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.

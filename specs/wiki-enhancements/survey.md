# Survey: wiki-enhancements

**Created**: 2026-08-31 | **Baseline**: 0.16.2 @ e002f9b (HEAD, branch feat/wiki-enhancements)
DELTA survey. The prior baseline
`specs/archive/2026-08-31-wiki-generation/survey.md` (commit 264647a) surveyed
this territory before the wiki feature landed; since then PRs #79-#85 added
`src/cairn/wiki/{catalog,manifest,pipeline,refine,sources}.py`,
`src/cairn/dashboard/markdown.py`, `src/cairn/mcp_server/tools_wiki.py`, wiki
dashboard routes/templates, `cli/wiki.py` status/retry + `--llm` flags, a
wiki promotion branch in `llm/tasks.py`, and `compass/critic.py`
section-vocab changes (diffstat 264647a..e002f9b re-run this session: 59
files, +6727/−128). Every citation below is from THIS session's grep/read
against e002f9b: citations reused from the prior survey were re-verified in
place (marked **[re-verified]**), new ground is marked **[new]**. Where this
survey leans on prior-survey conclusions without re-citing a line, the file
was still re-read this session. No numbers are carried over unverified.

## Items

```
item FR-001: "planner ranks modules whose indexed files are majority test files
  (test/spec path segments) below every non-test module at equal degree"
  evidence:   [new] The planner exists and is the only ranker:
              `src/cairn/wiki/catalog.py:build_page_plan:135` — buckets files
              into modules via `catalog.py:_module_of:29` (first 2-3 path
              segments, "Mirrors graph.stats.group_by_top_level exactly",
              including the legacy absolute-path strip), computes cross-module
              incoming degree via `catalog.py:_module_incoming_degree:53`
              (edges entering the prefix from outside it), and ranks at
              catalog.py:171: `ranked = sorted(module_files, key=lambda m:
              (-degrees[m], m))` — degree DESC, module name ASC. Page ids via
              `catalog.py:_slug:114` (`re.sub(r"[^a-z0-9]+", "-", ...)`,
              stripped). THE RAW MATERIAL FOR A MAJORITY-TEST TEST ALREADY
              EXISTS IN MEMORY: build_page_plan builds `module_files:
              Dict[str, List[str]]` at catalog.py:162-166 from
              `SELECT path FROM files WHERE repo_id = ?` (catalog.py:151-155)
              — every module's full file-path list is in hand at ranking time;
              a `test`/`spec` path-segment check over those lists is the whole
              computation. There is NO test-segment logic anywhere in
              catalog.py (grep `test|spec` segments this session: zero
              matches). Rank order is pinned by tests/test_wiki_planner.py:
              TestPlanOrdering:161 with
              `test_modules_ranked_by_cross_module_incoming_degree_desc:172`
              and `test_equal_degree_modules_tiebroken_by_module_name_asc:178`
              (plus TestPageRecordContract:100, TestPlanCap:185,
              TestDeterminism:205, TestEmptyGraph:228) — no test-module
              demotion test exists. `_page:119` hashes the entry
              (input_hash) over canonical JSON without the hash.
  status:     PARTIAL
  verify:     CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_planner.py -q  # 15 passed (this session)
  gap:        The demotion itself: no module is classified test-majority and
              no class split exists in the sort key. Adding it changes the
              pinned ordering semantics of TestPlanOrdering (equal-degree
              cases gain a class tier above name ASC).

item FR-002: "renderer renders inline code spans as code elements and GFM pipe
  tables as tables, preserving the escape-first contract"
  evidence:   [new] `src/cairn/dashboard/markdown.py:render_markdown:31` —
              escape-first pipeline: each line is `html.escape(raw,
              quote=False)` (markdown.py:52) BEFORE any construct matches;
              whitelisted blocks are `_HEADING_RE:18`, `_FENCE_RE:19`,
              `_LIST_ITEM_RE:20` and paragraphs; emit helpers
              `_emit_fence:23` (plain fence → `<pre><code>`, mermaid →
              `<pre class="mermaid">` at :26), `flush_paragraph:39`
              (`<p>`), `flush_list:44` (`<ul>/<li>`). INLINE CODE SPANS:
              none — a backticked span inside a paragraph line is just
              escaped text inside `<p>` (no inline pass exists; the module
              docstring :1-10 names headings/paragraphs/lists/fenced-code as
              the whole whitelist). TABLES: none — a pipe row is
              paragraph text; no table regex or emit path exists in the
              file (read in full, 91 lines). Pins: tests/test_dashboard_app.py
              `test_render_markdown_whitelists_blocks_and_escapes_inline_html:3758`
              (asserts `<h2>`, `<p>`, `<li>`, `&lt;script&gt;`, `&amp;`) and
              `test_render_markdown_fenced_code_and_mermaid_fence:3778`
              (asserts `x &lt; 1 &amp;&amp; y &gt; 2` and
              `'<pre class="mermaid">'`); the detail view loads mermaid.js
              client-side (tests assert cdn.jsdelivr.net/npm/mermaid@11 at
              test_dashboard_app.py:3735; template block at
              src/cairn/dashboard/templates/wiki_page.html:23-34). Import
              guard still applies (pure stdlib docstring markdown.py:3-4;
              test_importing_dashboard_never_loads_server_stack at
              tests/test_dashboard_app.py:41 [re-verified — passed in batch]).
  status:     PARTIAL
  verify:     CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest "tests/test_dashboard_app.py::test_render_markdown_whitelists_blocks_and_escapes_inline_html" "tests/test_dashboard_app.py::test_render_markdown_fenced_code_and_mermaid_fence" -q  # 2 passed (this session)
  gap:        Both constructs are absent: no inline-span pass (paragraph
              lines are emitted as one joined escaped string at
              flush_paragraph:39-42) and no table block (no separator-row
              detection, no <table> emitter). Both new passes must keep
              line-level html.escape FIRST (the pinned no-inline-HTML
              assertions at :3771-3773).

item FR-003: "WHEN a wiki page is promoted, THEN record the workspace HEAD
  commit sha in the concept's extensions and the manifest row"
  evidence:   [new] Promotion branch exists:
              `src/cairn/llm/tasks.py:complete_task:210` wiki branch keyed
              `task.task_kind.startswith("wiki-page")` (tasks.py:385) writes
              `type="Wiki-Article"` at `wiki/pages/{repo}/{page_id}`
              (tasks.py:409-426) with extensions EXACTLY four keys —
              tasks.py:419-424: `page_id`, `input_hash`
              (`task.facts.get("input_hash")`), `task_id`, `refine_catalog`
              (`task.facts.get("refine_catalog")`). NO commit sha anywhere:
              grep `commit_sha` over src/ this session — zero matches.
              Facts assembly: `src/cairn/wiki/pipeline.py:_queue_pages:52`
              sets facts at pipeline.py:77-86 to
              title/description/module/seeds/input_hash/repo (+`diagrams`
              when requested, :85-86) — no sha; the manifest row written at
              pipeline.py:89-94 is `{**entry, task_id, state:"queued",
              attempts}` — no sha. Manifest rows never store promotion
              state: manifest.py docstring :14-16 ("'Promoted' is never
              trusted from a stored row — readers derive it by reading the
              wiki/pages/{repo}/{page_id} concept"); PAGE_STATES at
              manifest.py:31-37 has no sha field. GIT RESOLUTION TODAY:
              `src/cairn/utils/git.py:get_current_commit:29` already wraps
              `_run_git(["rev-parse", "HEAD"], repo_path)` (:31) with a 10s
              timeout and None-on-failure (`_run_git:8`) — and has ZERO
              callers in src/ (grep this session: only `get_remote_url` is
              called, builder.py:473,494); the other git use is
              `src/cairn/graph/incremental.py:766`
              (`_run_git(["diff", "--name-only", "HEAD"], ...)`, from
              ..utils.git at incremental.py:16) [re-verified machinery,
              newly cited]. Repo-dir reachability: the repos table stores
              path WORKSPACE-RELATIVE — schema.py:15-22 (id, name, path,
              language, git_remote, indexed_at) with builder.py:468-471
              comment "repos.path is stored WORKSPACE-RELATIVE ... The
              absolute root is reconstructed at read time via
              resolve_repo_path()"; `src/cairn/graph/scanner.py:resolve_repo_path:204`
              maps (workspace, repo_name) → Path; workspace roots resolve
              via `src/cairn/paths.py:resolve_workspace:335` /
              `resolve_store:355` — NOT stored in the graph DB itself.
              Pipeline callers that could resolve HEAD into facts:
              `src/cairn/cli/wiki.py` generate --llm branch (wiki.py:49-107,
              calls run_wiki_generate at :68) and
              `src/cairn/mcp_server/tools_wiki.py:wiki_generate:19` (calls
              run_wiki_generate at :39) — the mitigation's shape (facts
              carry the sha; promotion copies it) matches how input_hash
              already flows facts → extensions (tasks.py:421).
  status:     PARTIAL
  verify:     grep -rn commit_sha src/ --include="*.py"  # no matches (exit 1, this session); CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_promotion.py -q  # 20 passed
  gap:        No sha in facts (pipeline.py:77-86), no sha in manifest rows
              (pipeline.py:89-94), no sha in promotion extensions
              (tasks.py:419-424), no manifest schema field for it. The
              resolver (`get_current_commit`) exists but is dead code with
              zero callers; wiring it needs a workspace-root resolution at
              the pipeline layer (repos.path is relative; scanner.resolve_repo_path
              needs the workspace, which the DB does not carry).

item FR-004: "provide `cairn task drop <id>` (pending/in-progress only; done
  refused) and `cairn task list --kind-prefix PREFIX`"
  evidence:   [new] `src/cairn/cli/task.py` has exactly four subcommands:
              task_list:15 (`--status` help "pending|in-progress|done|failed"
              at :16, `--kind` at :17), task_show:32, task_claim:63,
              task_complete:81 — no drop, no --kind-prefix (grep
              `--kind-prefix|kind_prefix` over src/ + tests/: zero matches).
              Exact-match filter:
              `src/cairn/llm/tasks.py:list_tasks:101` filters
              `if kind and task.task_kind != kind: continue` (tasks.py:118).
              Status lifecycle: Task dataclass comment tasks.py:47 —
              `status: str = "pending"  # pending | in-progress | done |
              failed`; NO dropped status exists on the concept.
              "Dropped" today is three artifacts only: (a) outcome dicts —
              complete_task returns `dropped: True` for
              not-in-progress/not-found (tasks.py:224-232), ownership
              mismatch (:237-248), and max-revise-cycles exhaustion
              (:479-494); (b) telemetry — TASK_LIFECYCLE event="dropped"
              (tasks.py:481-486); (c) the result sibling's
              `extensions["critic_status"] == "failed"` (tasks.py:323-327).
              The task concept itself stays status "done" even in a dropped
              chain (status set at tasks.py:250 before the critic runs).
              Non-claimability lever: `claim_task:124` re-reads status and
              only claims `pending` (tasks.py:157-164), claim marker via
              `os.open(O_CREAT|O_EXCL)` (tasks.py:136) [re-verified]. How a
              dropped chain is represented downstream:
              cli/wiki.py `_result_critic_status:208` reads that failed
              critic_status and `_page_state:218` maps a terminal done chain
              with a failed result to display state "failed"
              (cli/wiki.py:230-232); pipeline.py `_chain_dropped:115-123`
              does the same for refine chains; pinned by
              tests/test_wiki_cli.py:test_dropped_chain_derives_failed_for_status_and_retry:237.
  status:     TODO
  verify:     CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_tasks_safety.py tests/test_wiki_cli.py -q  # 14 passed (this session); grep -rn "kind_prefix|--kind-prefix" src/ tests/ --include="*.py"  # no matches
  gap:        Both operations are new: a drop command mutating task status
              (a status value "dropped" does not exist in the enum at
              tasks.py:47, in list_tasks filters, or in the CLI --status
              help at task.py:16 — note claim_task already refuses anything
              non-pending, so a dropped status would be unclaimable via the
              existing guard), and a prefix filter (list_tasks `kind` param
              is exact-match; the CLI passes it straight through). A drop
              refusal rule for done tasks has no precedent to copy — the
              closest guards are claim's pending-only check (tasks.py:158)
              and complete's in-progress-only check (tasks.py:224).

item FR-005: "critic reports each unresolved path once per completion
  regardless of citation form; output-spec lookup serves the wiki spec to
  any kind whose name starts with `wiki-page`"
  evidence:   [new] Dedupe: `src/cairn/compass/critic.py:critic_concept:53`
              accumulates file-ref errors in a plain loop —
              `for ref in file_refs: if not _file_exists(conn, ref):
              errors.append(f"Hallucinated/unresolved file path: {ref}")`
              (critic.py:70-73) — and the extractor
              `src/cairn/refs.py:extract_file_refs:38` appends EVERY
              BACKTICK_RE (:19) match that looks like a path, no set/dedupe
              (refs.py:40-49). The new footer-merge doubles forms rather
              than dedupes: complete_task resolves the Sources footer and
              merges `errors=critic_result.errors + source_errors`
              (tasks.py:302-316, merge at :312) where resolve_sources
              appends `f"Unresolved Sources footer entry: {entry}"`
              (src/cairn/wiki/sources.py:71) — the footer is part of the
              body, so a dead path cited there yields BOTH strings.
              `parse_sources_footer` (sources.py:24) also returns repeat
              entries in order without dedupe. Pins:
              tests/test_wiki_promotion.py:TestResolveSources:280 with
              `test_unresolved_entry_is_reported_as_error:293`;
              tests/test_compass_critic.py:TestCriticConceptIntegration:133
              (hallucinated file ref flagged as error). Spec-prefix
              fallback: `src/cairn/llm/tasks.py:_output_spec:576` — dict of
              exact kind keys (:577-629: compass-synthesize/-revise,
              flow-synthesize/-revise, the legacy bare `wiki` at :602,
              `wiki-page` at :603, `wiki-page-revise` at :609,
              `wiki-catalog` at :615, `wiki-catalog-revise` at :621,
              memory-critic/extract), then the exact-match lookup at
              tasks.py:630: `spec = specs.get(task_kind, "Process per the
              cairn skill.")` — the single line a startswith("wiki-page")
              rule slots into; a kind like `wiki-page-enrich` currently
              falls to the default string. The function ALREADY has a
              startswith precedent one line below: the Mermaid appendix
              `if task_kind.startswith("wiki-page") and facts and
              facts.get("diagrams")` (tasks.py:631-632). Pins:
              tests/test_wiki_promotion.py:TestWikiOutputSpecRegistration:78
              (`test_all_four_wiki_kinds_have_registered_specs:81`,
              `test_wiki_page_spec_requires_sources_footer_and_in_graph_refs:88`,
              `test_wiki_page_revise_spec_...:95`).
  status:     PARTIAL
  verify:     CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_compass_critic.py tests/test_wiki_promotion.py -q  # 36 passed (this session)
  gap:        Neither FR behavior exists: no dedupe anywhere on the error
              path (extractor :40-49, critic loop :70-73, footer merge
              tasks.py:312, footer parser sources.py:41-53), and the spec
              lookup is exact-match dict get (tasks.py:630) — the two known
              wiki kinds are served by explicit entries only; any new
              `wiki-page*` kind silently loses the Sources-footer
              instructions. Both changes touch lines already pinned by the
              suites above.

item FR-006: "provide `cairn wiki export --dir DIR` writing every promoted
  page as markdown (frontmatter preserved), reporting the count, refusing a
  non-empty directory without --force"
  evidence:   [new] Nothing named export exists in the wiki surface — grep
              `export` over src/cairn/cli/wiki.py, src/cairn/wiki/,
              src/cairn/mcp_server/tools_wiki.py this session: zero matches.
              The wiki group (src/cairn/cli/wiki.py:14) registers exactly
              generate:19, search:150, status:236, retry:265. Conventions
              for a new subcommand [re-verified in current file]:
              `from .main import DEFAULT_DB_PATH, get_db, main`
              (cli/wiki.py:7); `--knowledge` default
              `str(DEFAULT_DB_PATH.parent / ".knowledge")` (:22, :152, :238,
              :267); error exits `click.echo(..., err=True); sys.exit(1)`
              (malformed manifest at :174-176, manifest save failure at
              :308-310, planner failure at :61-63). Page iteration:
              manifest rows keyed `{repo}/{page_id}` via
              `_split_page_key:179`, promoted-page derivation
              `_is_promoted:185` (reads `wiki/pages/{repo}/{page_id}`);
              bundle-wide prefix iteration exists:
              `src/cairn/okf/bundle.py:list_concepts` (prefix filter,
              rglob *.md, skips index.md/log.md, sorted)
              [re-verified — signature unchanged]. File-writing round-trip:
              `src/cairn/okf/concept.py:from_file:92` (sources popped from
              frontmatter at :120), `to_markdown:166` (emits
              `fm["sources"]` only `if self.sources`, :190-191; extensions
              merged last), `to_file:145` (atomic tmp + os.replace)
              [re-verified — same lines as prior survey].
  status:     TODO
  verify:     grep -rn "export" src/cairn/cli/wiki.py src/cairn/wiki/ src/cairn/mcp_server/tools_wiki.py  # no matches (exit 1, this session); CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_cli.py -q  # 6 passed
  gap:        The command, its --dir/--force options, the non-empty-directory
              refusal, and the count report are all new; the reading and
              writing primitives it needs (manifest iteration, concept
              round-trip, atomic file write) all exist as cited.

item FR-007: "wiki status and the dashboard detail display fresh/stale by
  comparing the recorded sha with the workspace HEAD (unknown when either
  is unavailable)"
  evidence:   [new] No sha or staleness display exists anywhere. CLI:
              `src/cairn/cli/wiki.py:wiki_status:236` derives per-page state
              via `_page_state:218` (promoted → in-progress → queued →
              failed → stored state, :222-233) and aggregates
              `_DISPLAY_STATES = ("queued", "in-progress", "promoted",
              "failed")` (:11) into `counts` (:247) printed at :261-262 —
              no commit/sha column; manifest rows carry no sha to compare
              (FR-003). Dashboard data:
              `src/cairn/dashboard/data.py:get_wiki_pages:657` returns dicts
              with EXACTLY page_id/title/state/promoted (:675-681);
              `get_wiki_page:690` returns page_id/title/state/html
              (`render_markdown(concept.body)`, :715)/sources (:712-718) —
              no sha/stale field. Templates render state badges only:
              wiki.html:19 and wiki_page.html:7
              (`<span class="badge badge-{{ p.state }}">`).
              HEAD reachability at display time (surveyed): the wiki
              handlers get the db path but DISCARD it —
              `_, selected_knowledge, store_key = resolve_selection(...)`
              (app.py:699-701 and :710-712; `resolve_selection:310` returns
              `(db, knowledge_root, store_key)`); a read-only graph
              connection is available via
              `src/cairn/dashboard/data.py:get_read_only_db:1157` (mode=ro,
              raises MissingDatabaseError on a missing file) [re-verified],
              which can query the repos table — but repos.path is
              workspace-RELATIVE (schema.py:15-22 + builder.py:468-471
              comment) and the DB stores no workspace root; reconstructing
              it needs `paths.resolve_workspace:335`/`resolve_store:355`
              (the store dir is derivable from the db path's parent) plus
              `scanner.resolve_repo_path:204`. HEAD resolution itself is
              `utils/git.py:get_current_commit:29` (unused today — FR-003).
  status:     TODO
  verify:     CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_dashboard_data.py -q  # 82 passed (this session); grep -rn "stale" src/cairn/cli/wiki.py src/cairn/dashboard/data.py  # no staleness logic (only concept.py stale_after machinery elsewhere)
  gap:        Both surfaces need the sha field to exist first (FR-003), then
              a compare+display: status output column/aggregates
              (cli/wiki.py:247-262), data-layer fields
              (data.py:675-681, 712-718), template badge(s)
              (wiki.html:19, wiki_page.html:7), and a HEAD-resolution path
              that neither the CLI (has --db/--knowledge only) nor the
              dashboard wiki handlers (discard the db element) currently
              exercises — the pieces (get_read_only_db, resolve_workspace,
              resolve_repo_path, get_current_commit) exist but are unwired.

item FR-008: "provide a `wiki-page-enrich` task kind — queued via
  `cairn wiki enrich [<page-id>] [--repo R] [--all]` — whose completion
  replaces the promoted body through the existing promotion branch and
  revise cycle (prior body preserved in the task result)"
  evidence:   [new] The promotion branch ALREADY routes any
              `wiki-page*` kind: `task.task_kind.startswith("wiki-page")`
              gates both the critic's Sources-section scoring
              (tasks.py:291, `section_vocab=("## Sources",)` at :294) and
              the promotion/extensions write (tasks.py:385, :419-424) —
              `wiki-page-enrich` enters unmodified, including the
              critic-failure revise spawn (tasks.py:445-463, kind mapping
              `*-synthesize`→`*-revise` else append `-revise` at :448-451 —
              enrich would revise as `wiki-page-enrich-revise`) and max-
              cycle drop (:479-494) [re-verified in current file]. BUT
              `_output_spec` has NO enrich entry: the dict (:577-629) holds
              wiki-page and wiki-page-revise only; `wiki-page-enrich` falls
              to `"Process per the cairn skill."` (tasks.py:630) — see
              FR-005. Audit trail: `read_result` at tasks.py:517 reads the
              Task-Result sibling (`{TASK_DIR}/{task_id}.result`) — every
              completion persists the full result body (tasks.py:267-275)
              before the critic, so the prior body rides the result record
              if facts carry it. Queueing surface: NO enrich subcommand —
              cli/wiki.py registers generate/search/status/retry only;
              grep `wiki-page-enrich` over src/ + tests/: zero matches;
              grep `enrich` matches only the unrelated query-enrichment
              machinery (src/cairn/graph/query_enrich.py etc.). Manifest
              interaction: today generate SKIPS promoted+unchanged pages
              (`should_skip:163` requires hash equality AND a readable
              promoted concept, manifest.py:176-183) and `--force`
              re-queues everything (pipeline.py:70-75); retry re-queues
              failed pages only (cli/wiki.py:265-314, attempts counter
              bumped at :305) — an explicit per-page enrich override does
              not exist; the manifest row does carry the facts needed to
              re-queue one page (title/description/module/seeds/input_hash,
              written at pipeline.py:89-94 and reused by retry's facts at
              cli/wiki.py:292-301).
  status:     PARTIAL
  verify:     CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_promotion.py tests/test_mcp_wiki_tool.py -q  # 26 passed (this session); grep -rn "wiki-page-enrich" src/ tests/  # no matches
  gap:        The kind has no output spec (falls to the default string at
              tasks.py:630 — FR-005's prefix rule or an explicit entry is
              required), no queueing command (`cairn wiki enrich` does not
              exist), no old-body-in-facts writer, and no per-page override
              of the skip logic (generate's skip and retry's failed-only
              selection are the only re-queue paths today). The
              critic/promotion/revise machinery it must reuse is in place
              and keyed on the prefix already.

item FR-009: "WHERE `--lang en|zh` is passed to generate or enrich, the task
  facts carry the language and the output spec instructs writing in it
  (default en)"
  evidence:   [new] No --lang exists: grep `--lang|"lang"` over src/ +
              tests/ this session — zero matches. Facts pass-through is
              generic: create_task stores facts verbatim (only memory-*
              kinds are privacy-stripped, tasks.py:81-87), `Task.facts`
              (tasks.py:46), rendered into the task body by
              `_render_body:559` as `**{key}:** {val}` lines under
              "## Facts" (:565-569) [re-verified], and available to the
              output spec via `_output_spec(task_kind, task.facts)`
              (tasks.py:571, signature `_output_spec(task_kind, facts=None)`
              at :576). THE PRECEDENT for a conditional instruction is the
              diagrams-gated Mermaid appendix: facts["diagrams"] set by the
              pipeline (pipeline.py:85-86), instruction appended
              startswith-gated at tasks.py:631-632, pinned by
              tests/test_wiki_promotion.py:TestMermaidGating:101
              (`test_diagrams_fact_passes_through_and_gates_mermaid_instructions:104`).
              Current generate flag set (cli/wiki.py:19-39) [re-verified in
              current file]: --repo, --db, --knowledge, --dry-run,
              --show-rejections, --llm, --pages (default 10), --diagrams,
              --refine-catalog, --force. MCP tool mirrors:
              tools_wiki.py:wiki_generate:19 (repo, pages=10,
              refine_catalog, diagrams, force; `_clamp(pages, 1, 50)` at
              :35).
  status:     TODO
  verify:     CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest "tests/test_wiki_promotion.py::TestMermaidGating" -q  # 2 passed (this session); grep -rn "\-\-lang" src/ tests/ --include="*.py"  # no matches
  gap:        The flag on both surfaces, the facts key, the spec appendix,
              and the default-en behavior are all new; the
              optional-facts-append pattern to copy is tasks.py:631-632 and
              every queueing path (CLI :68, MCP :39) already funnels
              facts through _queue_pages:77-86.

item FR-010: "install-agents AGENTS.md template includes a wiki section
  (generate → claim → complete → ask_compass consumption)"
  evidence:   [new] Template content lives in
              `src/cairn/agent_install/_common.py`: the shared body is the
              module-level `_INSTRUCTIONS_BODY:284` (a `"""..."""` literal,
              ends ~:406); `_claude_instructions:409` (CLAUDE.md) and
              `_agents_instructions:419` (AGENTS.md) prepend headers to the
              same body — the AGENTS.md header carries the tool-count blurb
              `"- 28 tools across 4 layers: graph (9), knowledge base +
              compass (5), memory (8), knowledge (6)\n"` (_common.py:427)
              [re-verified count: `_EXPECTED_TOOL_COUNT = 28` at
              src/cairn/mcp_server/server.py:56 — the file that added the
              28th tool is tools_wiki.py:wiki_generate:19]. The WRITER:
              `src/cairn/agent_install/clients/zcode.py:105-107` writes
              `ws / "AGENTS.md"` from `_agents_instructions(...)` (claude
              writes CLAUDE.md the same way at clients/claude.py:143-145).
              Wiki mentions in the body today are incidental ONLY: the LLM
              Task Queue blurb "To generate compass/wiki with LLM quality"
              (_common.py:366) with the task list/show/claim/complete line
              (:368), and the Knowledge Files listing
              "`wiki/` -- architectural documentation" (~:384) — there is
              NO wiki workflow section (no wiki generate command, no
              ask_compass-consumption text for wiki). Doc-drift tests:
              tests/test_agent_surface.py
              `test_tool_count_string_matches_server:392` (parses
              _EXPECTED_TOOL_COUNT from server source, asserts
              `f"{expected} tools across 4 layers"` in
              _agents_instructions() output at ~:421 and the stale
              `{expected - 1} tools` absent at ~:427, plus the on-disk
              AGENTS.md agrees; source-parse fallback
              `_render_agents_instructions_from_source:198`) and
              `test_skill_tool_index_lists_all_registered_tools:446`
              (`assert len(registered) == 28` at :457) — both passed this
              session; they pin the tool COUNT, nothing about a wiki
              section.
  status:     PARTIAL
  verify:     CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_agent_surface.py -q  # 7 passed (this session)
  gap:        The wiki workflow section does not exist in
              _INSTRUCTIONS_BODY (its LLM Task Queue and Knowledge Files
              sections are the only insertion points citing wiki, at
              _common.py:366 and ~:384). A section addition perturbs the
              source-parsed template tests above (the AST fallback at
              test_agent_surface.py:198-230 re-renders the body from
              source, so any well-formed edit stays compatible) — no test
              pins section text beyond the tool-count line.
```

## Supporting evidence (load-bearing machinery tech will cite)

### Wiki pipeline — `src/cairn/wiki/pipeline.py` [new]
- `run_wiki_generate:168` — plan → optional refine step → `_queue_pages`;
  returns `{"plan", "queued_task_ids"}` (+ `catalog_task_id`/`catalog_pending`
  on the refine path). Raises `WikiPlannerError` on an empty graph.
- `_queue_pages:52` — skip condition `not force and row is not None and
  row.get("input_hash") == entry["input_hash"] and (should_skip(row, entry,
  bundle, repo) or page_id in live_pages)` (:70-75); `_live_task_pages:39`
  groups chains by resource over `startswith("wiki-page")` pending/in-progress
  tasks; attempts counter preserved across re-queues (:93); manifest saved
  after the queue decisions (:96).
- `_refine_catalog_step:126` — pending catalog task → return early; latest
  done chain whose result landed (`_chain_dropped:115` checks the result
  sibling's `critic_status == "failed"`) is parsed via `_parse_outline:104`;
  `validate_refined_outline` (src/cairn/wiki/refine.py:77; entries must match
  a real files.path prefix via `_module_in_graph:27` and every seeds.files
  path via `refs.file_exists`; invalid entries fall back to the deterministic
  entry at the same index) feeds `_queue_pages`.

### Wiki manifest — `src/cairn/wiki/manifest.py` [new]
- `MANIFEST_SCHEMA = "cairn-wiki-manifest-2"` (:29), doc at
  `<knowledge>/_wiki/manifest.json` (:39-40), keyed `{repo}/{page_id}`;
  `load_manifest:103` (missing file → empty doc; schema-1 upgraded in memory
  via `_migrate_v1:76`; malformed JSON raises ValueError);
  `save_manifest:129` (mkstemp in target dir, fsync before `os.replace`
  (:151), unlink-on-error, False on OSError — the paths.py:set_config_values
  shape [re-verified as the repo's atomic-write idiom]); `should_skip:163`
  (hash equality AND readable `wiki/pages/{repo}/{page_id}` concept);
  `PAGE_STATES = ("planned","queued","in_progress","promoted","failed")`
  (:31-37).

### Task queue — `src/cairn/llm/tasks.py` [re-verified in current file; wiki branch new]
- Constants: `TASK_DIR = "_tasks"` (:27), `MAX_REVISE_CYCLES = 3` (:28),
  `CLAIM_STALE_SECONDS = 3600` (:34).
- `complete_task:210` order of operations: ownership guard (:237-248) →
  status done + completed_at (:250-251) → memory-* result privacy strip
  (:261-264) → Task-Result sibling write (:267-275) → claim-marker removal
  (:279-283) → critic (only when conn is not None, :286) → wiki Sources
  resolution + merge (:302-321) → critic_status on the result (:323-328) →
  promotion branches (compass :338-359, flow :361-383, wiki :385-427) →
  revise spawn (:445-463) / drop (:479-494); critic exception →
  `errors: ["critic execution failed"]`, nothing promoted (:495-504); no
  conn → plain completion (:506-514).
- Wiki promotion writes `sources=wiki_sources` (resolved footer entries as
  `{"path": e}` or `{"symbol": e}`, tasks.py:317-321) — the ONLY producer
  of sources frontmatter in the codebase (prior survey's "no producer"
  finding is now stale; concept.py round-trip unchanged: `sources` field
  :77, popped at :120, emitted at :190-191 [re-verified]).
- `list_tasks:101` skips `.result` ids, filters status (exact) + kind
  (exact, :118). `read_result:517`; `get_task:526`;
  `_task_to_concept:532` (extensions task_kind/assigned_to/attempt/
  result_path/completed_at/claimed_at/facts; status on the OKF v0.2
  `status` field, :552); `_concept_to_task:636` (status fallback
  `ext.get("status", "pending")`, :640).

### Critic — `src/cairn/compass/critic.py` [re-verified in current file]
- `critic_concept:53` now takes `section_vocab: Optional[Sequence[str]]`
  (:57) — wiki calls it with `("## Sources",)` (tasks.py:294); default
  `_DEFAULT_SECTION_VOCAB:40` spans both compass shapes; quality =
  fraction of vocab present, `total = 5.0 if section_vocab is None else
  float(max(len(vocab), 1))` (:97-100); `threshold = 0.7 if warnings else
  0.5`, `passed = len(errors) == 0 and quality >= threshold` (:104-105).
- Error/warning accumulation unchanged: file refs → errors (:70-73), symbol
  refs → warnings (:76-79), prose-heavy guard `_prose_heavy_warning:136`
  (PROSE_HEAVY_MIN_CHARS 400 / MIN_REFS 2, :123-124). `validate_paths:159`
  unchanged.

### Dashboard wiki surface [new]
- Routes: `Route("/wiki", wiki, name="wiki")` (app.py:980),
  `Route("/wiki/{page_id}", wiki_page, name="wiki_page")` (:981) inside
  `create_app` [re-verified route-table pattern]; handlers wiki:698-707,
  wiki_page:709-732 (404 HTML when the page dict is None).
- Data: `get_wiki_pages:657`, `get_wiki_page:690` (both take knowledge_dir
  only, join manifest rows with `wiki/pages/` concepts via
  `_read_wiki_concept:648`, promoted derived from concept readability);
  `get_task_queue:617`, `get_recent_memories:591` unchanged patterns
  [re-verified].
- Templates: wiki.html (list table, badge at :19, empty state :25),
  wiki_page.html (badge :7, `{{ page.html | safe }}` :11, Sources list
  :13-20, mermaid CDN script :23-34).
- Renderer: `src/cairn/dashboard/markdown.py` — pure stdlib, escape-first
  (see FR-002).
- Pins: tests/test_dashboard_app.py wiki block :3582-3820-ish
  (`test_wiki_routes_registered_with_pinned_names:3665`,
  `test_wiki_templates_ship_with_the_dashboard:3674`,
  `test_wiki_is_linked_in_sidebar_and_launcher:3679`,
  `test_wiki_route_lists_pages_with_state_badges:3692`);
  tests/test_dashboard_data.py wiki tests at :2680, :2702, :2724, :2738,
  :2744, :2764.

### CLI wiki surface [re-verified in current file]
- Group `wiki` (cli/wiki.py:14); generate (:19-147) with the --llm branch
  (:49-107: plans every repo BEFORE queueing so a planner failure leaves
  the queue untouched, :57-63; catalog path prints claim/complete
  instructions :83-85; summary line :102-106); search (:150-164); status
  (:236-262: `_load_manifest_or_exit:167` exits 1 on malformed JSON;
  `_wiki_chains:193` groups every wiki-page task by resource across revise
  hops; derived-state precedence promoted → in-progress → queued →
  row-failed → chain-failed at `_page_state:218-233`); retry (:265-314:
  re-queues failed pages only as FRESH chains with attempts+1 :305,
  promoted untouched, manifest saved with exit 1 on failure :308-310).
- Exit-code/display conventions match the prior survey [re-verified]:
  sys.exit(1) + err=True on errors; plain click.echo output; task-group
  cross-references in help text.

### MCP wiki tool [new]
- `tools_wiki.py:wiki_generate:19` — `@mcp.tool(annotations=...)` over
  `@instrument`, primitives only, lazy body imports, `_clamp` at :35,
  prose return; delegates to `run_wiki_generate` (:39) with `_conn()` /
  `_bundle()` from `_server_core` [re-verified singleton pattern].
- Count: `_EXPECTED_TOOL_COUNT = 28` (server.py:56) enforced by
  `verify_tool_count` (assert at server.py:171) [re-verified mechanism];
  consumers re-verified: tests/test_agent_surface.py:457
  (`len(registered) == 28`), tests/test_status_resource_health.py and
  tests/test_tool_annotations.py updated in the landing diff (both green in
  batch run).

### Git/HEAD machinery (FR-003/FR-007 substrate) [new citations, re-verified files]
- `src/cairn/utils/git.py`: `_run_git:8` (subprocess, 10s timeout,
  None-on-failure), `get_remote_url:24`, `get_current_commit:29`
  (`rev-parse HEAD`). Callers: get_remote_url at builder.py:473,494 only;
  `_run_git(["diff","--name-only","HEAD"])` at incremental.py:766 (imported
  :16). get_current_commit: ZERO callers.
- Path resolution: repos.path workspace-relative (schema.py:15-22;
  builder.py:468-471); `scanner.resolve_repo_path:204`,
  `scanner.resolve_file_path:216`; workspace roots via
  `paths.resolve_workspace:335` / `resolve_store:355`;
  `DEFAULT_DB_PATH = resolve_store().db` (graph/schema.py:451) — the store
  dir (`<home>/<key>/.kg` + `.knowledge`) is derivable from the db path,
  the workspace root is not stored in the graph DB.

### Test infrastructure (this session's runs, canonical invocation)
- Combined batch (the 10 suites named in the brief):
  `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest
  tests/test_wiki_planner.py tests/test_wiki_promotion.py
  tests/test_wiki_manifest.py tests/test_wiki_cli.py
  tests/test_wiki_refine.py tests/test_mcp_wiki_tool.py
  tests/test_dashboard_app.py tests/test_dashboard_data.py
  tests/test_tasks_safety.py tests/test_compass_critic.py -q`
  → **307 passed, 1 warning** (StarletteDeprecationWarning in
  test_dashboard_app.py::test_create_app_serves_landing_and_static).
- Per-file collected counts (collect-only, this session):
  test_wiki_planner 15, test_wiki_promotion 20, test_wiki_manifest 33,
  test_wiki_cli 6, test_wiki_refine 12, test_mcp_wiki_tool 6,
  test_dashboard_app 109, test_dashboard_data 82, test_tasks_safety 8,
  test_compass_critic 16. Individually re-run: planner 15 passed,
  promotion 20 passed, tasks+cli 14 passed, critic 16 passed,
  dashboard_data 82 passed, mcp_wiki_tool 6 passed,
  manifest+refine 45 passed, the two render_markdown pins 2 passed,
  TestMermaidGating 2 passed, test_agent_surface 7 passed.
- Hermetic fixture + fresh_db unchanged [re-verified via passing runs;
  conftest not re-read line-by-line this session — pattern claims from the
  prior survey stand as its citations].

### Docs touchpoints (unchanged by the wiki landing where cited) [re-verified]
- pyproject version 0.16.2 (pyproject.toml:7). cli/__init__.py:7 docstring
  still says "The 49 commands live in split modules" (stale prose — the
  prior survey's 47-decorator grep pattern `@main.command|@main.group`
  returns 47 again this session; wiki subcommands decorate the subgroup, so
  the figure counts neither old nor new subcommands).
- specs/context/ refreshed this session (see Context drift below).

## Context drift found (specs/context/ refresh, applied this session)
1. structure.md mcp_server row: "27 tools across 4 layers" → 28
   (tools_wiki.py added; server.py:56). Refreshed; file list gains
   tools_wiki.py.
2. structure.md cli/ row: "wiki group (generate/search only as of
   2026-08-31 survey)" → generate/search/status/retry. Refreshed.
3. structure.md peripheral row: wiki/ described as "wiki generator" →
   generation pipeline (catalog/manifest/pipeline/refine/sources +
   generator). Refreshed.
4. structure.md package facts: v0.16.0 → 0.16.2 (pyproject.toml:7).
   Refreshed. Refresh stamp updated to e002f9b.
5. structure.md agent_install row's `_common` line refs drifted
   (instruction bodies now `_INSTRUCTIONS_BODY` at :284, `_claude_instructions`
   :409, `_agents_instructions` :419; CLIENTS 9 entries at :25). Refreshed.
6. tech.md: no substantive drift (test-runner line already canonical);
   refresh stamp updated to e002f9b.

## Rules
- Every citation above is from this session's grep/read output against
  e002f9b (HEAD of feat/wiki-enhancements). Items marked [re-verified] were
  cited by the prior baseline survey and were re-opened and re-checked in
  this session's output before reuse; items marked [new] are ground the
  prior survey never covered (the wiki feature's own files).
- Status derives from evidence, not intent; every verify command above was
  run this session with the result recorded in its comment.
- Unknowns: none remained at write time (any would read `unknown — verify`).

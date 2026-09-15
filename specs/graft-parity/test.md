# Test Cases: graft-parity

**Spec**: [spec.md](spec.md) | **Created**: 2026-09-16

Black-box, business-language verification traced to requirements. Case text
stays in product terms (CLI verbs, MCP tool names, printed output); exact
runnable commands live in the pass conditions.

## Runner conventions

- New modules run hermetically against the working tree:
  `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest <module> -q`
  (same convention as the survey's verified commands).
- Every module builds only bounded temporary workspaces (a handful of small
  files, throwaway graph stores) and completes well under the two-minute
  per-case audit cap.
- After a command, `# → ...` states the expected observable; it is not part
  of the command.
- MCP-surface cases invoke the tools through the same server surface the
  product exposes; no test may read or write the user's live graph store.

## TC-001 — An edited file's answer is fresh on every CLI query verb
- **Story**: US1 · **Traces to**: FR-001, AC1
- **Given** an indexed workspace in which a public routine has just been
  renamed on disk, with no manual refresh run
- **When** each CLI query verb named by the requirement runs against the
  renamed routine, queried once by its new name and once by its old name
- **Then** every verb answers from the current tree: the new name resolves
  and the old name is reported absent, with no stale hit and no manual
  refresh step.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_freshness.py -q` # → all pass

## TC-002 — An edited file's answer is fresh on every MCP graph tool
- **Story**: US1 · **Traces to**: FR-001, AC1
- **Given** the same freshly edited workspace as TC-001
- **When** each of the eight MCP graph tools named by the requirement is
  invoked for the renamed routine
- **Then** each tool reflects the edit before answering — none returns the
  pre-edit symbol as a live result.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_freshness.py -q` # → all pass

## TC-003 — Only drifted files refresh (standing guard)
- **Story**: US1 · **Traces to**: FR-001
- **Given** an indexed workspace where exactly one of several files has been
  edited since indexing
- **When** any covered query runs
- **Then** exactly the drifted file is refreshed — untouched files are not
  reindexed — so the refresh is bounded by observed drift, not a full rebuild.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_freshness.py -q` # → all pass

## TC-004 — Refresh opt-outs answer from the stored graph with a drift banner
- **Story**: US1 · **Traces to**: FR-002, AC2
- **Given** an indexed workspace with one edited file, and refresh disabled
  — once via the environment opt-out, once via the command-line opt-out
- **When** a covered query runs
- **Then** the answer comes from the stored graph (the edit is NOT
  reflected) and the output carries a staleness notice naming the drifted
  file, for both opt-out mechanisms.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_freshness.py -q` # → all pass

## TC-005 — A read-only store answers with a drift banner and no writes
- **Story**: US1 · **Traces to**: FR-002, AC2
- **Given** an indexed workspace with one edited file, served read-only
- **When** a covered query runs
- **Then** the stored-graph answer is returned with a staleness notice
  naming the drifted file, the store is not modified, and the query does not
  fail because of the edit.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_freshness.py -q` # → all pass

## TC-006 — The drift banner needs no watcher installation (standing guard)
- **Story**: US1 · **Traces to**: FR-002, AC2
- **Given** the default installation, with the optional watcher components
  unavailable, and an edited indexed file with refresh disabled
- **When** a covered query runs
- **Then** the probe-based staleness notice still fires and names the
  drifted file — the banner path never depends on the watcher extra.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_freshness.py -q` # → all pass

## TC-007 — A clean tree produces no staleness notice
- **Story**: US1 · **Traces to**: FR-002
- **Given** an indexed workspace whose files all match their indexed state
- **When** a covered query runs with refresh disabled
- **Then** the answer carries no staleness notice — the banner is quiet
  unless drift exists.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_freshness.py -q` # → all pass

## TC-008 — A working-tree diff yields the reverse-dependency radius
- **Story**: US2 · **Traces to**: FR-003, AC1
- **Given** an indexed workspace where an uncommitted edit changes one
  routine that other routines call
- **When** the blast-radius command runs with no base option
- **Then** it reports the changed routine as the seed and lists its
  transitive dependents from the current tree.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_blast.py -q` # → all pass

## TC-009 — Seeding honors changed hunks, not whole files
- **Story**: US2 · **Traces to**: FR-003, AC1
- **Given** an indexed file containing two routines; only the first is
  edited, and only the second has dependents
- **When** the blast-radius command runs on the working-tree diff
- **Then** the radius is empty for the untouched routine's dependents —
  symbols outside changed hunks of a changed file are not seeds.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_blast.py -q` # → all pass

## TC-010 — A named base compares against the merge base
- **Story**: US2 · **Traces to**: FR-003, AC1
- **Given** an indexed branch that diverged from a main line with one
  committed change to a routine that the main line's other routines call
- **When** the blast-radius command names the main line as its base
- **Then** the radius is computed from the merge-base diff: the changed
  routine seeds the radius and its dependents are listed, without the
  working tree needing uncommitted edits.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_blast.py -q` # → all pass

## TC-011 — Every declared output format renders
- **Story**: US2 · **Traces to**: FR-004, AC2
- **Given** a diff with a non-empty blast radius
- **When** the blast-radius command runs once per declared format
  (plain text, review markdown, diagram source, structured JSON)
- **Then** each run exits successfully and emits that format — readable
  prose for text, a markdown section for markdown, a parseable diagram for
  the diagram source, and machine-readable radius data for JSON.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_blast.py -q` # → all pass

## TC-012 — Precise edges by default; fuzzy widens on request
- **Story**: US2 · **Traces to**: FR-004, AC2
- **Given** a changed routine with one precisely-resolved dependent and one
  dependent reachable only through a name-ambiguous edge
- **When** the blast-radius command runs with and without the fuzzy opt-in
- **Then** the default run lists only the precise dependent; the fuzzy run
  lists a superset that also contains the ambiguous dependent, with the
  ambiguity visible in the output.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_blast.py -q` # → all pass

## TC-013 — An empty radius exits successfully and says so
- **Story**: US2 · **Traces to**: FR-004, AC2
- **Given** a diff to a routine that nothing depends on
- **When** the blast-radius command runs
- **Then** it exits zero with an explicit statement that there are no
  dependents — not an error, not silent output.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_blast.py -q` # → all pass

## TC-014 — An unknown base ref fails with CI checkout guidance
- **Story**: US2 · **Traces to**: FR-004, AC2
- **Given** a shallow CI-style checkout in which the requested base
  reference is absent
- **When** the blast-radius command names that base
- **Then** it fails with a non-zero exit and an error that names the missing
  reference and tells the user to fetch full history (fetch-depth guidance),
  rather than reporting an empty radius.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_blast.py -q` # → all pass

## TC-015 — The map orients by directory cluster with counts and hubs
- **Story**: US3 · **Traces to**: FR-005, AC1
- **Given** a multi-repo workspace whose directories form distinct clusters
  with known file, symbol, and edge counts and known most-depended-on hubs
- **When** the map command runs
- **Then** each repo scope lists its directory clusters with file/symbol/edge
  counts, names each cluster's most-depended-on hubs, and the output names
  the workspace-wide hotspots.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_map.py -q` # → all pass

## TC-016 — The map is deterministic across repeats (standing guard)
- **Story**: US3 · **Traces to**: FR-005, AC1
- **Given** an unchanged indexed workspace
- **When** the map command runs twice
- **Then** both outputs are byte-identical, including ordering of clusters,
  hubs, and hotspots.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_map.py -q` # → all pass

## TC-017 — The map makes zero LLM or network calls (standing guard)
- **Story**: US3 · **Traces to**: FR-005, AC1
- **Given** a workspace with no language-model credentials and outbound
  network access unavailable
- **When** the map command runs
- **Then** it completes successfully with the full orientation payload —
  the map never requires an LLM or network.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_map.py -q` # → all pass

## TC-018 — Every cap reports what was dropped
- **Story**: US3 · **Traces to**: FR-005, AC1
- **Given** a bounded synthetic workspace deliberately exceeding the map's
  cluster, hub, and hotspot caps
- **When** the map command runs
- **Then** the output reports a dropped count at every cap that truncated —
  clusters, hubs, and hotspots each state how many entries were omitted
  rather than disappearing silently.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_map.py -q` # → all pass

## TC-019 — The MCP map tool returns the same orientation as the CLI
- **Story**: US3 · **Traces to**: FR-005, AC1
- **Given** the same indexed workspace
- **When** the CLI map command and the MCP repo-map tool both run
- **Then** both surfaces report the same clusters, counts, hubs, hotspots,
  and dropped counts — the MCP tool is a first-class map surface, not a
  reduced variant.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_map.py -q` # → all pass

## TC-020 — An empty workspace maps to an empty orientation, not an error
- **Story**: US3 · **Traces to**: FR-005
- **Given** an initialized repository containing no indexable source files
- **When** the map command runs
- **Then** it exits successfully with a valid empty orientation (zero
  clusters/hotspots), not a crash or missing-output error.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_map.py -q` # → all pass

## TC-021 — The file surface lists every symbol with signatures, no bodies
- **Story**: US3 · **Traces to**: FR-006, AC2
- **Given** an indexed file containing nested and top-level symbols of
  several kinds
- **When** the MCP file-surface tool is asked for that file
- **Then** it returns every symbol's kind, qualified name, signature text,
  and line span; no response field contains the symbol's body.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_file_api.py -q` # → all pass

## TC-022 — Missing signatures degrade to name and span
- **Story**: US3 · **Traces to**: FR-006, AC2
- **Given** an indexed file containing at least one symbol for which no
  signature text was parsed
- **When** the MCP file-surface tool is asked for that file
- **Then** that symbol still appears with its name and line span, the
  request succeeds, and the response marks the signature as unavailable
  rather than emitting an error or a fabricated signature.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_file_api.py -q` # → all pass

## TC-023 — A bodyless file yields an empty surface, not an error
- **Story**: US3 · **Traces to**: FR-006
- **Given** an indexed file that contains no symbols (for example, an empty
  or data-only file)
- **When** the MCP file-surface tool is asked for that file
- **Then** it returns an empty symbol list successfully.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_file_api.py -q` # → all pass

## TC-024 — Hits group under the innermost enclosing symbol
- **Story**: US4 · **Traces to**: FR-007, AC1
- **Given** an indexed file where a search pattern occurs inside a nested
  routine, inside its top-level parent, and at module level outside any
  symbol
- **When** the pattern-search command runs
- **Then** the nested hit groups under the nested routine, the top-level hit
  under the top-level routine, and the module-level hit under an explicit
  file-level group.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_grep.py -q` # → all pass

## TC-025 — Groups rank by incoming coupling
- **Story**: US4 · **Traces to**: FR-007, AC1
- **Given** two matching symbols where one is depended on by more routines
  than the other
- **When** the pattern-search command runs
- **Then** the more-depended-on symbol's group is ranked above the other.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_grep.py -q` # → all pass

## TC-026 — Case-insensitive, literal, and path filters behave as declared
- **Story**: US4 · **Traces to**: FR-007, AC1
- **Given** indexed files where a pattern occurs with mixed casing, where
  the pattern contains characters that regex would treat specially, and
  where matches exist both inside and outside one directory
- **When** the search runs with the case-insensitive flag, with the
  fixed-string flag, and with the path-prefix filter
- **Then** the case-insensitive run finds all casings; the fixed-string run
  treats the pattern literally; the filtered run returns hits only under the
  chosen prefix.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_grep.py -q` # → all pass

## TC-027 — Truncation is counted, never silent
- **Story**: US4 · **Traces to**: FR-007, AC1
- **Given** a synthetic corpus whose match count exceeds the search's result
  cap
- **When** the pattern-search command runs
- **Then** the output reports how many hits or groups were truncated beyond
  the cap, in every format that can truncate.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_grep.py -q` # → all pass

## TC-028 — Human notes survive regeneration byte-for-byte
- **Story**: US5 · **Traces to**: FR-008, AC1
- **Given** a generated wiki page and a generated compass page, each carrying
  a human-authored Notes section containing non-ASCII text, trailing
  whitespace, and an irregular blank-line run
- **When** both pages are regenerated
- **Then** each Notes section is preserved byte-identical — every character,
  including the trailing whitespace and blank-line run — while the generated
  sections update.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_notes.py -q` # → all pass

## TC-029 — Human notes survive enrichment
- **Story**: US5 · **Traces to**: FR-008, AC1
- **Given** a promoted page with a human-authored Notes section
- **When** the page is enriched with new generated content
- **Then** the Notes section remains byte-identical and the new content is
  added without rewriting or relocating the notes' bytes.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_notes.py -q` # → all pass

## TC-030 — The critic never judges Notes content against code
- **Story**: US5 · **Traces to**: FR-008, AC1
- **Given** a page whose Notes section references files and symbols that do
  not exist in the repository
- **When** the deterministic critic evaluates the regenerated page
- **Then** the page passes evaluation — Notes content is excluded from the
  code-reference check — while the same bogus references placed in a
  generated section still fail.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_notes.py -q` # → all pass

## TC-031 — HTML export is a single self-contained file
- **Story**: US5 · **Traces to**: FR-009, AC2
- **Given** an indexed workspace and a selected graph scope containing known
  nodes and edges
- **When** the visualization command exports an HTML file
- **Then** exactly one HTML file is produced; it references no network
  assets (no http(s) or protocol-relative source or link targets); and the
  file's inline data contains the selected scope's nodes and edges.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_viz_export.py -q` # → all pass

## TC-032 — The exported graph renders offline in a real browser
- **Story**: US5 · **Traces to**: FR-009, AC2
- **Given** the exported HTML file from TC-031
- **When** a human opens it in a browser with networking disabled
- **Then** the selected graph scope renders visibly (nodes and edges on
  screen) with no blank page and no missing-resource errors.
- **Pass condition**: Human opens the exported file in a browser with networking disabled and observes the selected scope's nodes and edges rendered, with no missing-resource error; genuinely visual, so manual.

## TC-033 — An available Python language server upgrades resolvable edges
- **Story**: US6 · **Traces to**: FR-010, AC1
- **Given** a built workspace containing a member call that the static
  resolver left ambiguous but a language server can resolve to one
  definition
- **When** a build runs with the language-server pass enabled and the
  Python server available on PATH
- **Then** that edge is upgraded to exact, pointing at the resolved
  definition, and the run reports the upgrade.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_lsp.py -q` # → all pass

## TC-034 — A missing language server is a deterministic no-op
- **Story**: US6 · **Traces to**: FR-010, AC1
- **Given** a built workspace with an ambiguous edge and no Python language
  server on PATH
- **When** a build runs with the language-server pass enabled
- **Then** the build succeeds deterministically, the edge keeps its previous
  resolution, and the output carries a notice that the server pass was
  skipped — never a crash or a partial graph.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_lsp.py -q` # → all pass

## TC-035 — A failing language server degrades to a no-op
- **Story**: US6 · **Traces to**: FR-010, AC1
- **Given** a built workspace where the language-server process starts but
  exits before answering lookups
- **When** a build runs with the language-server pass enabled
- **Then** the build succeeds, all affected edges keep their previous
  resolution, and the output reports the degraded pass.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_lsp.py -q` # → all pass

## TC-036 — The language-server pass never downgrades exact edges (standing guard)
- **Story**: US6 · **Traces to**: FR-010
- **Given** a built workspace that already contains exact edges, including
  edges the language server cannot resolve or would disagree with
- **When** a build runs with the language-server pass enabled — with the
  server present, absent, and failing
- **Then** no previously exact edge is downgraded or removed in any of the
  three runs.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_lsp.py -q` # → all pass

## TC-037 — Rust symbols index; Rust call edges are never exact (standing guard)
- **Story**: US6 · **Traces to**: FR-011, AC2
- **Given** a workspace containing Rust source files with definitions and
  function calls
- **When** the workspace is indexed
- **Then** the Rust definitions are searchable with spans, the calls appear
  as name-based edges, and every Rust-source edge is labeled unresolved —
  zero Rust edges are labeled exact.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_rust.py -q` # → all pass

## TC-038 — The Rust grammar dependency is recorded as a numbered decision
- **Story**: US6 · **Traces to**: FR-011
- **Given** the delivered feature set includes Rust indexing with a grammar
  dependency
- **When** the spec directory's decision records are searched
- **Then** a numbered `D-###` decision exists that names the Rust grammar
  dependency.
- **Pass condition**: `grep -RniE 'D-[0-9]+' specs/graft-parity | grep -iE 'rust.*grammar|grammar.*rust' && echo rust-grammar-decision-recorded` # → the marker prints (at least one numbered decision names the Rust grammar)

## TC-039 — Submodules and nested repos stay excluded by default (standing guard)
- **Story**: US6 · **Traces to**: FR-012, AC3
- **Given** a workspace containing an initialized git submodule and a nested
  child repository, with no inclusion setting persisted
- **When** repository discovery and indexing run
- **Then** neither the submodule nor the nested repository is discovered or
  indexed — the default is off.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_repos.py -q` # → all pass

## TC-040 — Opted-in submodules and nested repos index under a repo prefix
- **Story**: US6 · **Traces to**: FR-012, AC3
- **Given** the same workspace with inclusion enabled through persisted
  configuration
- **When** repository discovery and indexing run
- **Then** the initialized submodule and nested child repository are
  included, and their files are indexed under identifiers that prefix them
  by their owning repository so they cannot collide with parent-repo paths.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_repos.py -q` # → all pass

## TC-041 — Uninitialized submodules are skipped even when opted in
- **Story**: US6 · **Traces to**: FR-012, AC3
- **Given** a workspace whose configuration enables inclusion and whose
  submodule manifest declares a submodule that has not been initialized
- **When** repository discovery and indexing run
- **Then** the declared-but-empty submodule is skipped without failing the
  run.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_repos.py -q` # → all pass

## TC-042 — A linked worktree seeds from the main checkout and refreshes drift
- **Story**: US6 · **Traces to**: FR-012, AC3
- **Given** a linked git worktree with no graph store of its own, a main
  checkout with a store, and one file in the worktree edited after the main
  checkout was indexed
- **When** the worktree is indexed
- **Then** its graph is seeded from the main checkout's store, the drifted
  worktree file is refreshed so answers match the worktree, and the main
  checkout's store is left unmodified.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_repos.py -q` # → all pass

## Coverage matrix

| Requirement | Test cases | Type (auto/manual) |
-------------|------------|--------------------|
| FR-001 | TC-001, TC-002, TC-003 | auto |
| FR-002 | TC-004, TC-005, TC-006, TC-007 | auto |
| FR-003 | TC-008, TC-009, TC-010 | auto |
| FR-004 | TC-011, TC-012, TC-013, TC-014 | auto |
| FR-005 | TC-015, TC-016, TC-017, TC-018, TC-019, TC-020 | auto |
| FR-006 | TC-021, TC-022, TC-023 | auto |
| FR-007 | TC-024, TC-025, TC-026, TC-027 | auto |
| FR-008 | TC-028, TC-029, TC-030 | auto |
| FR-009 | TC-031, TC-032 | auto + manual |
| FR-010 | TC-033, TC-034, TC-035, TC-036 | auto |
| FR-011 | TC-037, TC-038 | auto |
| FR-012 | TC-039, TC-040, TC-041, TC-042 | auto |

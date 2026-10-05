# CLI Reference

← [Docs index](README.md)

Read this when MCP tools aren't available or you're scripting cairn. Every
command runs through the `cairn` entry point (`cairn.cli:main`, Click).
Pass `--db` / `--workspace` explicitly in scripts — `CAIRN_HOME` and friends
are read at process start, not per call.

## Build & lifecycle

| Command | Purpose |
|---|---|
| `cairn init [--with-hooks]` | interactive first-time setup (runs a build); `--with-hooks` also installs the cairn git hooks (see [hooks](#cairn-hooks)) |
| `cairn build [--lsp]` | full workspace rebuild (see [indexing.md](indexing.md)) |
| `cairn update [--file <path>] [--diff-ref A..B]` | incremental reindex (git-diff driven; see [below](#cairn-update---diff-ref)) |
| `cairn stats` | graph statistics |
| `cairn checkpoint` | snapshot the store |
| `cairn config` | show effective configuration (`--json` emits `cairn_home`/`workspace`/`db`/`knowledge` as one JSON document — read-only, registers nothing; the scripting/probe surface) |
| `cairn uninstall` | remove cairn integration artifacts |

### `cairn build --lsp`

- Runs `pyright --stdio` only when `--lsp` is present and `pyright` is on
  `PATH`.
- Candidates are Python call edges still marked `ambiguous` after normal
  resolution. Exactly one definition location mapping inside one stored Python
  symbol upgrades the edge to `exact`.
- Missing or failing pyright is a noticed no-op; failed passes roll back their
  changes.
- Existing `exact` edges are never selected or downgraded.

### `cairn update --diff-ref`

- `--diff-ref A..B` scopes changed-file detection to the git ref range: the
  changed set is exactly `git diff --name-only A..B` — the untracked-file
  pass and the size/mtime fallback that flag-less mode uses are skipped, so
  nothing outside the range is reindexed.
- Both range endpoints are pre-validated with `git rev-parse -q --verify`;
  an unresolvable ref exits with a clean error instead of a silent
  zero-file update.
- Flag-less `cairn update` is unchanged: worktree diff versus `HEAD` plus
  untracked source files.
- The cairn git hooks (see [below](#cairn-hooks)) call this form with the
  committed or checked-out range.

## Query

| Command | Purpose |
|---|---|
| `cairn def <symbol>` | find a definition |
| `cairn import-scip <file>` | import an external SCIP index as an edges-only overlay (see [scip.md](scip.md)) |
| `cairn callers <symbol>` | who calls this |
| `cairn callees <symbol>` | what this calls |
| `cairn search <query>` | symbol search (FTS5) |
| `cairn semantic <query>` | hybrid semantic search |
| `cairn impact <symbol>` | what breaks if changed (within-repo) |
| `cairn blast` | reverse-dependency radius of a git diff |
| `cairn map` | deterministic repository orientation map |
| `cairn communities` | Louvain subsystem clusters over the symbol graph (requires the `[graph-analytics]` extra) |
| `cairn grep <pattern>` | span-grouped search over indexed files |
| `cairn rationale` | `NOTE:`/`WHY:`/`HACK:` marker-comment records for a symbol or file (see [below](#cairn-rationale)) |
| `cairn deps <repo>` | cross-repo dependency map |
| `cairn tree <path>` | file/module symbol tree |
| `cairn ask "<question>"` | natural-language query across layers (`--all-repos` fans out across registered stores) |
| `cairn federated-search "QUERY"` | cross-store semantic search with per-repo attribution (`--limit`, `--json`, `--shared-embed`) |
| `cairn pack --task "<text>" --budget <N>` | one-shot token-budgeted context block (`--json`; drops lowest-centrality content first and reports counts) |
| `cairn path --from <pattern> --to <pattern>` | shortest structural path between symbols (`--fuzzy` adds ambiguous/unresolved hops; `--max-depth` caps the walk; `--limit` caps printed paths; patterns are case-insensitive substrings of a symbol or qualified name) |
| `cairn taint --from <pattern> --to <pattern>` | inter-procedural taint path trace (`--fuzzy` adds ambiguous/unresolved hops; `--max-depth` caps the walk) |
| `cairn review` | review loop: `--base <ref>` context pack, `--pre-submit [--gate]` memory guard, `--capture-event <file>` comment capture |
| `cairn context <file>` | compass + memory context for a file |

### `cairn blast`

- Default comparison: working tree versus `HEAD`.
- `--base <ref>` compares `HEAD` with the merge base of `<ref>` and `HEAD`.
- `--format text|markdown|mermaid|json` selects the renderer; text is the
  default. `--output <file>` writes the rendered result instead of stdout.
- Traversal is precise by default; `--fuzzy` also follows unresolved edges and
  labels their resolution.
- An empty radius exits 0 and states that there are no dependents.
- An unknown `--base` ref exits non-zero, names the ref, and recommends
  fetching full history with CI fetch-depth guidance.

### `cairn map`

- Groups directory clusters per repository with file, symbol, and edge counts;
  ranks hubs by incoming edges and reports workspace hotspots.
- `--json` emits the canonical machine shape. Text is the default.
- `--max-clusters`, `--max-hubs`, and `--max-hotspots` cap their arrays and
  every cap reports how many entries were dropped, including zero.

### `cairn communities`

- Computes deterministic Louvain communities over structural edge kinds only
  and persists the partition plus per-community hubs to derived tables;
  prints the community count, global hubs, and per-community hubs.
- Requires the `[graph-analytics]` extra (`pip install
  'cairn[graph-analytics]'`); without it the command exits 1 with that hint
  and the store is untouched.
- `--db` selects the store; `--top-k` caps the hub lists (default 10,
  minimum 1).
- A store with no structural edges prints "no communities found".

### `cairn grep <pattern>`

- Searches indexed files and groups hits by their innermost enclosing symbol;
  hits outside symbol spans form a file-level group.
- Groups rank by incoming edge count, then path, qualified name, and first hit.
- `-i` ignores case, `--fixed` treats the pattern literally, and `--in
  <path-prefix>` restricts repository-relative paths.
- `--max-hits` caps returned hits; dropped hit and group counts are always
  reported, and unreadable files are counted.

### `cairn rationale`

- Lists rationale records: comments whose text begins `NOTE:`, `WHY:`, or
  `HACK:` (after the comment opener and optional whitespace), extracted
  during `cairn build` in every language with a mapped comment node type.
  One record per comment; continuation lines fold into the text. Docstrings
  never produce records, and `TODO:`/`FIXME:` are out of scope.
- Target: `--symbol <name>` or `--file <path>`. Records print one per line,
  ordered by line, with kind tags (`note`/`why`/`hack`). `--db` selects the
  store; `--json` emits machine-readable rows.
- Attribution: a record belongs to the innermost enclosing callable or type
  whose span contains its line; otherwise it is file-level. A marker
  directly above a definition falls outside that symbol's span and is
  file-attributed.
- `cairn update` deletes and re-derives each reindexed file's rationale rows
  in the same transaction as its symbols, so removed markers never linger.
- MCP `explore` output includes a `=== Rationale (N) ===` section (after
  Tribal memory) when any matched symbol has rationale records, and omits
  it otherwise; the MCP tool count is unchanged.

## Embeddings & rerank

| Command | Purpose |
|---|---|
| `cairn embed` | compute/refresh symbol embeddings (`--multivector`, `--build-index`, `--install-deps`, `--download-model`, `--adopt-server-model [ID]` to make a parity-verified server fallback permanent) |
| `cairn download-reranker` | fetch the CrossEncoder reranker |

## Knowledge

Group: `cairn knowledge …`

| Subcommand | Purpose |
|---|---|
| `ingest` | staged doc ingestion; `--file`/`--dir`/`--repo` sources, `--ingest` to execute, `--include-drafts`, `--outbox` (see [knowledge-and-memory.md](knowledge-and-memory.md)) |
| `rebuild` | recompute the derived `knowledge_edges` / `knowledge_doc_refs` index tables from the bundle (runs automatically after `ingest --ingest`; idempotent); island detection then queues `doc-link` tasks for detached pair-units (deduped per member set) |
| `islands` | list doc-graph islands (detached pair-units) and their `doc-link` task state |
| `related <doc_id>` | stored relationship neighbors of one doc, one line per edge with relation (`relates-to`/`supersedes`/`superseded-by`/`references`), kind (`extracted`/`inferred`/`derived`), and direction; `--json` emits the full rows. Unknown ids exit non-zero with a named error |
| `chain <doc_id>` | the supersede chain containing a doc, printed oldest → newest (any chain member may be the argument; every member appears exactly once); `--json` emits ordered members. Unknown ids exit non-zero with a named error |
| `add` / `import` / `remove` | manual document management |
| `search` / `list` / `embed` / `export` | query and maintain the bundle |
| `impact <query>` | knowledge-to-graph bridge: matching docs with affected repos and their cross-repo dependencies |
| `status <doc_id> <new_status>` | lifecycle transitions |
| `workflow add|trace|sync` | workflow definitions and traces |

## Memory

Group: `cairn memory …`

| Subcommand | Purpose |
|---|---|
| `record <type> "<title>"` | capture a memory (decision/pattern/mistake/workaround); `--stance` declares a prior stance (see [Memory stance](#memory-stance)) |
| `reflect` | recompute stances across the store from supersession and citation evidence (see [Memory stance](#memory-stance)) |
| `search` / `list` / `digest` / `stats` | recall and inspect (`search --as-of <date>` for point-in-time recall) |
| `share --agent <id> --symbols a,b,c` | publish a memory to other agents' recall on the shared store |
| `check --agent <id> --symbols a,b,c` | warn on other agents' recent activity on the symbol set |
| `timeline <symbol>` | a symbol's memory history with validity intervals and successor links |
| `evolve` / `promote` / `demote` / `forget` | lifecycle |
| `decay` / `purge` / `consolidate` / `batch-critic` / `embed` / `capture` | maintenance |

### Memory stance

Memories carry an optional stance — `preferred`, `tentative`, or
`contested` — orthogonal to the lifecycle tiers (a promoted memory can be
contested).

- `cairn memory record --stance preferred|tentative|contested` declares a
  prior stance at record time.
- `cairn memory reflect` recomputes stances across the store from
  supersession and citation evidence: a memory superseded by a peer when
  the pair shares a symbol ref that still verifies is `contested` and
  names that peer; a
  memory whose refs-verified fraction dropped below its recorded baseline
  is `tentative`; a memory whose every cited ref verifies is `preferred`.
  The evidence verdict overrides a declared prior when they conflict, and
  the prior survives when evidence yields no verdict. Reflect touches
  stance metadata only — never bodies, titles, tiers, or scores — and is
  idempotent and deterministic on an unchanged store.
- Stance renders inline wherever memories are listed — `cairn memory search`
  / `list` output and MCP `recall_memory` / `explore` output — with the
  contradicting peer named on contested entries.

## Serving & surfaces

| Command | Purpose |
|---|---|
| `cairn serve run|start|stop|status|restart` | MCP server (stdio foreground / SSE daemon `:9876`). `start`/`stop`/`restart` manage the macOS launchd LaunchAgent; `start` embeds `CAIRN_HOME`/`CAIRN_WORKSPACE`/`CAIRN_DB`/`CAIRN_KNOWLEDGE` into the plist when a custom home is in effect. On Linux these exit 1 — run `cairn serve --port 9876` under a process supervisor, or prefer stdio. |
| `cairn dashboard [--db] [--port]` | loopback dashboard `:8765` (read-only views + Settings) |
| `cairn install-agents` / `uninstall-agents` | wire cairn into AI clients (claude, droid, zcode, cursor, opencode, kilo, omp, agy, claude-desktop). With a custom `CAIRN_HOME`, generated stdio registrations embed `env.CAIRN_HOME` (CLI-registered clients carry it too: claude global scope via `claude mcp add -e`, droid via `droid mcp add --env`), hook commands embed the assignment, and each written stdio registration is spawn-verified — the report shows per-client `verify: PASS/FAIL` naming both stores on mismatch. SSE and CLI-registered clients are skipped with a note; `--dry-run` never spawns. |

## Compass / wiki / tasks / dataflow

| Command | Purpose |
|---|---|
| `cairn compass generate|list|validate|gaps|flow|flow-gaps` | module navigation guides |
| `cairn skill generate <selectors>` | generate a packaged `SKILL.md` from modules, directory prefixes, or symbols (`--polish` queues a critic-gated task) |
| `cairn wiki generate --llm [--pages N] [--refine-catalog] [--diagrams] [--force] [--repo R]` | agent-decoupled wiki generation: plans a deterministic page outline (overview + top modules by incoming reference degree, capped by `--pages`, default 10) and queues one pending `wiki-page` task per page (keyed by the qualified `{repo}/{page_id}` resource) for any agent to claim and complete — a passing completion is critic-gated and promoted as a wiki article with a verified `## Sources` footer; a failing one runs the bounded revise cycle. Incremental via the `_wiki/manifest.json` manifest: unchanged, already-promoted pages are skipped unless `--force` re-queues every page; an empty/unindexed graph exits 1. `--refine-catalog` queues one `wiki-catalog` refinement task first — re-run the command after it completes to queue the page tasks from the validated refined outline. `--diagrams` instructs writers to include Mermaid fences. Without `--llm`, the deterministic single-summary generation is unchanged (its output is an `Architecture-Report` diagnostic at `reports/architecture/{repo}` — outside the critic-gated wiki page surface) |
| `cairn wiki status` | per-page generation state, derived at read time (planned / queued / in-progress / promoted / failed / dropped), with aggregate counts, joined from the manifest and live task state; each page also carries a staleness verdict — `fresh` when its recorded commit sha equals the repo's current HEAD, `stale` when both are present and differ, `unknown` when either is unavailable — with `fresh=… stale=… unknown=…` in the totals line |
| `cairn wiki retry` | re-queue exactly the failed pages — derived from the live task chain and promoted content, never a stored verdict (a done task with no passing critic verdict counts, so stuck chains are reachable) — as fresh task chains (cumulative queue-attempt count preserved); promoted pages untouched, and dropped tasks stay dropped — drop is terminal |
| `cairn wiki search <query>` | search the wiki (promoted articles + deterministic summaries) |
| `cairn wiki export --dir DIR [--force]` | write every promoted page as `DIR/{repo}/{page_id}.md` (OKF frontmatter preserved) and report the exported count; a non-empty target directory is refused unless `--force` is passed |
| `cairn wiki enrich [<page-id>] [--repo R] [--all]` | queue one `wiki-page-enrich` task per promoted page — either a single `page-id` or `--all` (never both), optionally scoped with `--repo`; the task's facts carry page identity and fresh seeds (never the body); the critic-passing completion reads the promoted body at completion time, appends the new sections, and merges the new `## Sources` entries into the frontmatter. Requires an already-promoted page |
| `cairn task list|show|claim|complete|drop` | LLM synthesis task queue; `list` filters by `--status`, `--kind`, or `--kind-prefix PREFIX` (e.g. `--kind-prefix wiki-page` lists every chain hop), `drop` abandons a pending or in-progress task — terminal: done tasks are refused and a dropped task is never claimable again (dropping an in-progress task releases its claim marker so the resource can be re-queued) |
| `cairn dataflow build\|lookup` | precomputed impact index; `build` caps public symbols per run — `--max-symbols`, else `CAIRN_DATAFLOW_MAX_SYMBOLS`, else 2000 — and warns on stderr when truncating (partial index) |

## Health & ops

| Command | Purpose |
|---|---|
| `cairn status` | build state, parse errors, resource health |
| `cairn doctor` | degradation check (exit 0 = PASS/WARN, 1 = FAIL). 11 checks: the 10 store-internal ones — including `memory_staleness` (WARN when tribal memories older than 30d have zero `memory_refs` in that window — write-only memory) — plus `environment` — store existence, client-registration consistency (FAIL on a provable wrong-store or unreachable SSE endpoint, WARN on stale missing-env registrations), platform/transport supportability (SSE on non-macOS ⇒ WARN), binary coherence. Emitted last, also on the db-unavailable degraded path. |
| `cairn metrics` / `report` | tool-metrics and health reports |
| `cairn validate` / `validate-paths` / `verify` | store integrity checks |
| `cairn bench` | performance suites |
| `cairn eval` | retrieval evaluation |
| `cairn viz [--export FILE]` | render graph diagrams; `--export` writes one self-contained HTML file |
| `cairn hooks install|uninstall` | manage the cairn git hooks — post-commit + post-checkout (see [below](#cairn-hooks)) |
| `cairn version` / `upgrade` | version and self-upgrade |
| `cairn sync` | sync pending watcher edits |

### `cairn hooks`

- `cairn hooks install` writes a post-commit and a post-checkout hook into
  every discovered git repo's `.git/hooks/`, idempotently: an existing
  cairn hook is rewritten in place, and a foreign hook (no cairn marker in
  its content) is never overwritten. `uninstall` removes only cairn-marked
  hooks.
- post-commit: after every commit, backgrounds
  `cairn update --diff-ref <prev>..HEAD` plus `cairn validate-paths --mark`.
  The previous commit resolves from the reflog (`HEAD@{1}`); a repo's first
  commit falls back to an empty-tree range. Output is discarded — the commit
  never waits on the update, and a failed update never fails the commit.
- post-checkout: on branch switches only, backgrounds
  `cairn update --diff-ref <old>..<new>`; file checkouts run nothing.
- With a non-default `CAIRN_HOME`, installed hooks embed an
  `export CAIRN_HOME` line (see [configuration.md](configuration.md)).
- `cairn init --with-hooks` installs both hooks with the same rules as
  `cairn hooks install` (opt-in, never interactive).
- In a workspace with no git repository, `hooks install` prints guidance
  and exits 1; `init --with-hooks` prints the guidance as a warning and init
  still succeeds. Neither path writes partial hooks.

Global: `-v/--verbose` for debug logging.

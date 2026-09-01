# Knowledge & Memory

Read this when you're ingesting documents, working with the OKF bundle,
recording/recalling memories, or driving the LLM task queue.

## The OKF bundle (`.knowledge/`)

Everything cairn remembers durable is an **OKF concept**: a markdown file
with YAML frontmatter (`type`, `title`, `description`, `tags`, `status`,
`concept_id`, …) managed by `src/cairn/okf/bundle.py` (`OKFBundle`) with
`fcntl.flock` cross-process locking. Concept files live under the workspace
store's `.knowledge/` directory; the same model backs knowledge docs, memory
tiers, compass guides, wiki entries, and task-queue items.

## Doc ingestion (`cairn knowledge ingest`)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="diagrams/doc-ingestion-pipeline-dark.png">
  <img src="diagrams/doc-ingestion-pipeline.png" alt="Doc ingestion pipeline diagram">
</picture>

Open [diagrams/doc-ingestion-pipeline.html](diagrams/doc-ingestion-pipeline.html)
for the full-size version. Pipeline: `src/cairn/knowledge/ingest/__init__.py:run_ingest`.

1. **Source adapters** (`adapters.py`) — `FedMarkdownAdapter` (explicit
   `.md` files/dirs), `FedBinaryAdapter` (`.pdf` via pymupdf4llm, `.docx`
   via mammoth→markdownify; `cairn[ingest]` extra), `RepoScanAdapter`
   (walks `docs`, `decisions`, `adr`, `adrs`). 10 MB per-file cap; a
   skip-list drops drafts/meetings/templates/changelogs.
2. **Parse** (`parser.py`) — YAML frontmatter → `title/status/tags/
   description`; graceful fallbacks to minimal frontmatter, inline status
   markers, first heading.
3. **Classify** (`classifier.py`) — `decision` / `spec` / `workflow` /
   `business-rule` (spec is the default). Layers: workspace title rules →
   built-in keyword rules → filename/directory conventions. Draft statuses
   (`draft`, `proposed`, `review`, `superseded`, `deprecated`) are blocked
   unless `--include-drafts` re-admits them tagged `draft`.
4. **Redact + identity** — `strip_private_data`
   (`src/cairn/memory/privacy.py`) scrubs private tags, credentials, API
   keys, JWTs from title/description/body. `build_identity`
   (`identity.py`) derives a stable id from `slugify(repo/relpath)` —
   a SHA-1 fragment when the 60-char slug cap bites, and `({repo})`,
   `({repo})-2`, … suffixes keep slugs collision-free.
5. **Stage outbox** (`staging.py`) — writes one OKF markdown file per
   accepted doc plus `manifest.json` (version 1: counts, per-row
   source/identity/type/tags/body/staged-path or skip reason) under
   `<workspace>/.cairn/ingest-outbox/`. **Dry run is the default** —
   nothing touches the knowledge store without `--ingest`.
6. **Execute** (only with `--ingest`, `executor.py`) — writes each accepted
   row via `add_document`, embeds (`embed_knowledge`, batch 32), then runs
   verify legs: store-count equality, OKF conformance, smoke search.

Docs land in `knowledge/<doc_type>/<slug>.md` ("doc families").

## Memory tiers

Memories (`src/cairn/memory/`) are OKF concepts under `.knowledge/memory/`:

| Tier | Directory | Score | Meaning |
|---|---|---|---|
| raw | `memory/raw/` | < 0.3 | ephemeral capture; expires after 7 days |
| drafts | `memory/drafts/` | 0.3–0.5 | awaiting quality review |
| tribal | `memory/tribal/` | ≥ 0.5 | decisions, patterns, mistakes, workarounds |
| archived | `memory/archived/` | — | decayed tribal (> 90 days stale) |

- **Recording** (`record_memory` → `capture_memory`): both title and body are
  redacted; a same-type near-duplicate (exact title or cosine ≥ 0.85) is
  superseded automatically (`memory_is_latest: false`, chained via
  `memory_superseded_by`).
- **Scoring** (`scoring.py`), 0–1:
  `0.25·graph_verification + 0.20·cross_session_refs + 0.15·agent_confidence
  + 0.20·critic_score + 0.05·freshness + 0.05·reinforcement + 0.10·authority`.
  `graph_verification` is the fraction of backtick-quoted file/symbol refs
  still present in the graph — memories rot when code changes.
- **Recall** (`recall_memory` → `search_memory`): hybrid — lexical match plus
  brute-force cosine over `memory_embeddings` (small corpus, no vec0), fused
  with RRF. Recall is symbol/title-keyed, not natural-language full text.
- **Lifecycle**: `memory decay` runs at MCP boot and after `cairn update`;
  `memory promote` moves decisions/patterns → compass, architecture → wiki;
  `evolve` creates a new version and supersedes the old.

## Compass, wiki, and the LLM task queue

Cairn never calls an LLM in-process. Synthesis work is queued as task
concepts (`src/cairn/llm/tasks.py`) in `.knowledge/_tasks/`:

- Kinds: `compass-synthesize`, `compass-revise`, `flow-synthesize`,
  `flow-revise`, `wiki`, `wiki-page`, `wiki-page-revise`, `wiki-page-enrich`,
  `wiki-page-enrich-revise`, `wiki-catalog`, `wiki-catalog-revise`,
  `memory-critic`, `memory-extract`.
- Lifecycle: `pending` → `in-progress` (atomic `O_EXCL` claim, 1h stale
  recovery) → `done` → promoted / revised (≤ 3 cycles) / dropped.
- Drive it with `cairn task list|show|claim|complete --result-file <path>`;
  `list` filters by `--status`, `--kind`, or `--kind-prefix` (e.g.
  `--kind-prefix wiki-page` lists every hop of the wiki chains), and
  `cairn task drop <id>` abandons a pending or in-progress task — terminal:
  done tasks are refused and a dropped task is never claimable again.
- The **deterministic critic** (`src/cairn/compass/critic.py`) fact-checks
  every result: backtick file paths must exist in the graph, symbol refs
  must resolve, prose-heavy low-ref bodies get warned. It is not a
  hallucination detector — only graph-verified references pass.

**Wiki generation** (`cairn wiki generate --llm`) rides the same queue.
Generate plans a deterministic page outline from the graph — modules whose
indexed files are a strict majority of test files
(`test`/`tests`/`spec`/`specs` path segments) are excluded from the plan
entirely, so page budgets are spent on product code — and queues one
`wiki-page` task per page; with `--refine-catalog` a
`wiki-catalog` task refines the outline first (re-run generate to queue page
tasks from the validated result). A manifest at `.knowledge/_wiki/manifest.json`
records each page's plan, input hash, task id, cumulative attempts, and the
commit sha current at queue time, so re-runs skip unchanged, already-promoted
pages (`--force` re-queues everything) and `cairn wiki status` /
`cairn wiki retry` derive per-page state from the manifest joined with live
task state. For wiki kinds the critic scores the `## Sources` footer instead
of compass sections — reporting each unresolved path once no matter how many
citation forms mention it — and a passing page is promoted to a
`Wiki-Article` concept under `wiki/pages/{repo}/{page_id}` with its verified
sources in frontmatter and the workspace HEAD sha recorded in its extensions.

**Consuming and extending a promoted wiki**: both `cairn wiki status` and
the dashboard wiki views compare the page's recorded sha with the repo's
current HEAD — `fresh` when equal, `stale` when they differ, `unknown` when
either is unavailable. `cairn wiki export --dir DIR [--force]` writes every
promoted page as `DIR/{repo}/{page_id}.md` with its frontmatter preserved.
`cairn wiki enrich [<page-id>|--all]` queues `wiki-page-enrich` tasks whose
facts carry the page's current body plus fresh seeds; a critic-passing
completion appends the new sections to the promoted body (the prior text
stays in the page) and merges the new `## Sources` entries into the
frontmatter, riding the same bounded revise cycle. The wiki output spec —
the `## Sources` footer requirement — serves any task kind whose name starts
with `wiki-page`, so revise hops of any depth keep it.

Compass files are 5-section module guides (`src/cairn/compass/generator.py`);
wiki entries (`src/cairn/wiki/generator.py`) are deterministic graph-derived
architecture summaries. Both work without any LLM; the queue only upgrades
prose quality.

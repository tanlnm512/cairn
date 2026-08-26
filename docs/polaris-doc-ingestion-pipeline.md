# Polaris workspace document-ingestion pipeline

← [Docs index](README.md)

> **Shipped in core.** This design was upstreamed into cairn itself as
> `cairn knowledge ingest` (see the knowledge section of
> [cli-reference.md](cli-reference.md)); this page is kept as the original
> design rationale. Deliverable 2's subprocess-per-row execution was
> superseded — an approved run (`--ingest`) writes each manifest row
> in-process via `add_document`. Rendered pipeline diagram:
> [diagrams/doc-ingestion-pipeline.html](diagrams/doc-ingestion-pipeline.html).
>
> Status: approved (workspace-side) · Date: 2026-08-26
> Target workspace: `/Users/lnmtan/Projects/polaris` (15 sibling repos, store key
> `435aeb8f7ab91a0d`, bundle `~/.cairn/435aeb8f7ab91a0d/.knowledge` — currently
> **zero** knowledge docs, task queue empty).
> Corpus assumptions: all 15 repos (~170 candidate md docs); drafts skipped;
> reference docs ingested as tagged `spec`; dry-run checkpoint before any write.

A staging pipeline that turns the Polaris workspace's documentation corpus into
cairn knowledge docs, classified per file with stable identities — working
around the current limits of `knowledge import` without changing cairn itself.

## Verified ingestion surface (as of this date)

| Fact | Source |
| --- | --- |
| `knowledge add` accepts per-doc `--title --type --tags --affects --affects-modules --epic --resource --file/--body` | `src/cairn/cli/knowledge.py` |
| `knowledge import DIR` rglobs `*.md`, applies **one uniform doc_type** to all files, derives titles from filename stems, does not parse frontmatter, no dedup (same slug overwrites atomically; different title duplicates) | `src/cairn/knowledge/store.py:265-311` |
| Embedding rows are written **only** by `knowledge embed` — `add`/`import` never embed | `store.py`, `graph/embeddings.py` |
| doc types: `business-rule \| spec \| decision \| workflow` (no enum enforcement) | `store.py:97` |
| doc_status lifecycle: `active → superseded → archived`, forward-only | `store.py:195-224` |
| Concept path: `knowledge/{doc_type}/{slugify(title)}.md` | `store.py:131-133` |

## The gap

The best corpus — `polaris/docs` (108 md files, full OKF frontmatter, stable IDs
like `ADR-0007`, `FEAT-0002`, `C-llm-gateway`) — is type-mixed per directory.
Raw `import` would misclassify ADRs as specs, munge titles from filenames, and
sweep in files the doc convention itself marks "not ground truth" (meeting
notes, drafts, generated mirrors). Per-repo ADR sets (`agent_runtime`,
`polaris-cli`, `polaris-ui`, `agent-kernel`) use inline `**Status:**` markdown
instead of YAML frontmatter, which import cannot read at all.

So the pipeline needs a staging step between corpus and `knowledge add`.

## Deliverable 1 — `scripts/ingest_docs.py` (in the polaris workspace)

1. **Allowlist walk** — per-repo doc dirs: `polaris/docs`, `agent-kernel/docs`,
   `agent_runtime/docs`, `polaris-cli/docs`, `polaris-ui/docs`,
   `polaris-codebase/docs`, `polaris-knowledge/docs` + `reports`,
   `polaris-memory/docs`, `polaris-app/docs`, root convention files
   (`AGENTS.md` / `CLAUDE.md` / `GEMINI.md`), and `polaris-directory`'s two
   root C4 diagram docs.
2. **Skip-list** — meeting-notes, journals, prototypes, generated HTML,
   `.tracking.yaml`, CHANGELOGs, `capability-catalog.md` (generated),
   `XX-fuji`, stale roadmaps (`agent_runtime/docs/project-roadmap.md`,
   `project-overview-pdr.md`), superpowers plans, docs-dir `README.md`
   navigation files, `*_template*`.
3. **Parse & classify** — YAML frontmatter (PyYAML when importable, minimal
   parser fallback; regex fallback for inline `**Status:**`/`## Status`
   ADRs) → doc_type map:
   - ADR / decisions / FINDING → `decision` (~43)
   - FEAT / UC / `C-*` component specs / proposals / designs → `spec` (~65)
   - GUIDE / runbook / setup guides → `workflow` (~20)
   - CONV conventions / AGENTS-CLAUDE-GEMINI convention files / code
     standards → `business-rule` (~10)
   - vision / architecture / prior-art reference docs → `spec` + tag
     `reference` (~55)
4. **Status gate** — ingest `accepted / approved / active / reference` (and
   no-status operational docs); skip `draft / proposed / review / superseded /
   deprecated`, logged with reason. `--include-drafts` overrides.
5. **Stable identity** — title `"{ID} — {title}"` → deterministic slug, so
   re-runs overwrite cleanly instead of duplicating. Slug collisions across
   repos (e.g. `ADR-0001` in three repos) resolved by appending `({repo})`.
   Tags = frontmatter tags + stable ID + repo name. `affects_repos` = origin
   repo; `affects_modules` = doc dir. Body = source body with frontmatter
   stripped and a `Source:` provenance line prepended.
6. **Flags** — `--dry-run` (default; writes manifest JSON + prints summary),
   `--ingest`, `--repo <name>` filter, `--include-drafts`,
   `--manifest-out <path>`.

## Deliverable 2 — execution (checkpointed)

1. **Dry-run** → manifest summary (counts by type/repo, skips with reasons) —
   explicit go-ahead required before anything is written.
2. **Ingest** — per manifest row: `cairn knowledge add --body … --title …
   --type … --tags … --affects <repo> --affects-modules <dir>`, run with the
   workspace as cwd so the right store resolves.
3. **Index** — `cairn knowledge embed --batch-size 32`.
4. **Verify** — `cairn knowledge list` count matches manifest; `cairn
   validate`; smoke searches ("llm gateway alias routing", "contribution flow
   gate", "memory recall contract") return the expected docs; the
   `affects_repos` graph bridge fires in search results.
5. **Record** — pipeline saved as a cairn pattern memory + session memory.

Task-queue (compass/wiki synthesis) is **out of scope** for v1 — the queue is
empty and synthesis is an optional follow-up once docs are in.

## Candidate cairn improvements this pipeline works around

Not required for v1, but each is a gap the staging script papered over:

- `knowledge import` could read OKF frontmatter for per-file doc_type / title /
  status, falling back to uniform flags.
- Import could report slug collisions and skip-list more surgically than
  "everything under DIR".
- `add`/`import` could enqueue embedding work (or warn that docs are
  unsearchable-by-vector until `knowledge embed` runs).
- A `--status` flag on `add` would let curated pipelines ingest at
  `superseded` (forward-only lifecycle currently starts everything at
  `active`).

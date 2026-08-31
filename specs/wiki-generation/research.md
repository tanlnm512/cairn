# Research: wiki-generation

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-31

**Researcher gate: skipped — no open questions at Stage 0.**
The approach is fully determined by (a) cairn's existing task-queue/critic
architecture (survey.md grounds it) and (b) the behavior of the feature being
ported, which was already reverse-engineered from the ZCode app bundle on
2026-08-31 (reference material below). No library, algorithm, or protocol
choice remains open. Per the skill: this file records the skip so a resumed
session can tell "skipped on purpose" from "forgotten".

## Questions
### (none — researcher spawn skipped at the Stage-0 gate)

## Options summary
### (none — no open technical choices; tech-spec decides from survey.md + the reference material below)

## Reference material: ZCode "Generate Project wiki" (as-shipped, 2026-08-31)

Extracted from `/Applications/ZCode.app/Contents/Resources/app.asar` and
`~/.zcode/v2/repo-wiki/` state files; recorded here because this spec ports
that feature's shape.

- **Pipeline**: two phases — `catalog` (outline) then per-page generation.
  Progress tracked per workspace in
  `~/.zcode/v2/repo-wiki/<workspace-hash>/task.json` with fields
  `taskId, phase (catalog|…), status, completedPages, failedPages,
  createdAt, updatedAt`; page content plus `wiki.json`/`manifest.json`
  keyed to a commit hash live alongside.
- **Catalog phase**: the model receives exactly two read-only tools —
  `get_dir_structure` (dir tree; caps: depth ≤ 4, ≤ 800 files, ~24 KB,
  .gitignore-aware, skips test/spec/stories files and lockfiles) and
  `view_file_in_detail` (line-numbered file snippets by workspace-relative
  path) — and produces page titles + short descriptions. Output budget
  16k tokens (32k when diagram generation is on).
- **Pages phase**: one model call per page; the model re-expands that
  page's relevant modules with the same two tools and writes markdown
  (Mermaid fences when diagrams are on, 8k/16k budgets). The app appends a
  `Sources:` footer of `[path](path#L1)` links for files actually read.
- **Lifecycle**: per-page retries (3–5 attempts), "Retry failed pages"
  (regenerate only missing), stop/cancel, regenerate, delete. Per-request
  timeout 15 min; running-task count broadcast over the `repo-wiki` RPC
  channel.
- **Storage**: entirely local under `~/.zcode/v2/repo-wiki/`; never written
  into the repo, never pushed.

### Porting translation (informational — tech-spec owns the decisions)
| ZCode mechanism | cairn translation |
|---|---|
| LLM explores with 2 tools to build catalog | deterministic planner over the existing symbol graph (module degrees, top symbols); optional refinement task |
| 1 model call per page | 1 queued `wiki-page` task per page (agent-decoupled, claim/complete) |
| Sources footer trust | footer verified by the deterministic critic against the graph; failures revise |
| task.json phase/counters | wiki manifest with per-page input hash + state |
| Retry failed pages | `cairn wiki retry` re-queueing failed/dropped pages |
| Model/lang/diagram options | model N/A (agent-side), lang out of scope (English), `--diagrams` as task facts |

ZCode state observed on this machine (2026-08-31): both prior runs
(cairn, polaris workspaces) stalled/cancelled in the catalog phase with 0
pages — no local content to migrate.

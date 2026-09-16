# Spec: context-pack

**Status**: draft
**Created**: 2026-09-16
**Branch**: `feat/context-pack`

## What
`cairn pack --task "<description>" --budget <tokens>` produces one
self-contained, token-budgeted context block: relevant source snippets,
blast-radius summary, compass excerpt, and top memories — selected by
semantic relevance, expanded through the call graph, and ranked by
structural centrality.

## Why
Aider's graph-ranked repo map remains the best compact-context mechanism;
cairn holds strictly richer inputs (resolution-labeled edges, precomputed
transitive impact, semantic search, memory, compass) but exposes them only
as separate queries. The pack turns cairn into the one-shot context
primitive an agent calls once before writing code.

## Business value
Agents get complete task context in one call within a stated token budget,
replacing multi-query exploration. Success: on the existing agent bench
corpus, a pack within budget contains the symbols needed for the task
(fit-rate target ≥0.85) at a fraction of grep-baseline tokens.

## User stories
### US1 — One-shot task context (P1)
As an agent starting a task, I want everything relevant in one call within a
token budget.

**Acceptance criteria** (each trace to an FR below):
- AC1: Given a task description and budget, When `cairn pack` runs, Then a
  single markdown block is emitted containing source snippets, blast-radius
  summary, compass excerpt, and top memories, with a reported token count
  within budget (FR-001, FR-002, FR-003).
- AC2: Given an impossible budget, When packing, Then the system degrades
  gracefully by centrality rank and reports what was dropped (FR-004).

## Requirements
- **FR-001**: The system shall provide `cairn pack --task <text> --budget <tokens>`
  that seeds candidate symbols via semantic search (falling back to lexical
  search when embeddings are absent), expands them through precise call
  edges, and emits a single markdown context block.
- **FR-002**: The pack shall rank the expanded symbol set by structural
  centrality computed from the precomputed transitive-impact tables.
- **FR-003**: The pack content shall include, per selected symbol: verbatim
  source trimmed to signature plus key body; plus a depth-2 precise
  blast-radius summary, the containing module's compass excerpt, and the
  top-3 relevant memories where present.
- **FR-004**: IF the requested budget cannot fit the selected content, THEN
  the system shall drop lowest-centrality items first and report dropped
  counts.
- **FR-005**: The pack generation shall be deterministic with no LLM in the
  path and no network access.

## Scope
**In**: pack command, seed/expand/rank/fit pipeline, token accounting,
markdown emitter, bench integration measuring fit rate, tests.
**Out (deferred)**: MCP `context_pack` tool (CLI proves the pipeline first);
streaming/incremental packs; multi-task batching; non-markdown outputs;
Aider-format compatibility mode.

## Assumptions & risks
- Assumption: the existing agent bench corpus can host a fit-rate arm
  measuring whether needed symbols appear in the pack.
- Risk: semantic seed quality is mid-band without embeddings — mitigation:
  lexical fallback plus graph expansion compensates; fit-rate arm measures
  honestly.
- Risk: token accounting drift across models — mitigation: report counts
  with the tokenizer used, budget conservative.

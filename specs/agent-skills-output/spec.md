# Spec: agent-skills-output

**Status**: draft
**Created**: 2026-09-16
**Branch**: `feat/agent-skills-output`

## What
`cairn skill generate <area>` emits a packaged skill (a `SKILL.md` plus
optional reference files) that AI agents load on demand — packaging the
compass body, the area's key symbols, and relevant tribal memory into the
agent-native format popularized by Claude Skills.

## Why
Compass, wiki, and memory exist as queryable stores; agents must learn to
query them across multiple round trips. The ecosystem has moved to
pre-packaged, lazily-loaded skills. Emitting cairn's existing knowledge in
that format is a format conversion over data cairn already holds — low
effort, high leverage.

The conversion surface is already built and proven. Cairn ships one static
skill package (`agent_integration/skill/`, a `SKILL.md` plus
`references/`/`scripts/`/`evals/`) that `cairn install-agents` merges into
each client's skill directory, with client-specific fallback paths already
mapped. Every content input a per-module skill needs is exposed by an
existing reader: module compass via `get_compass`, ranked symbols via
stored graph rows (`repo_map` already computes per-symbol in/out degree),
memory via the recall/search API, and verification via the deterministic
critic. What is missing is assembly: nothing generates a skill body from a
selector today.

## Business value
Any skill-compatible agent (Claude Code and others adopting the format) gets
cairn's module context in one load instead of many queries. Success: a
generated skill for a sample module contains only graph-verified symbols
(critic-checked) and loads in Claude Code without manual assembly.

## User stories
### US1 — Generate a skill (P1)
As a developer, I want `cairn skill generate <module>` so that my agent gets
pre-packaged module context it can load on demand.

**Acceptance criteria** (each traces to an FR below):
- AC1: Given an indexed workspace and a module selector, When
  `cairn skill generate` runs, Then a `SKILL.md` is written with frontmatter
  (name, description, load triggers) and a body containing the compass
  excerpt, top-K symbols by structural centrality, and top-N relevant
  memories (FR-001, FR-002).
- AC2: Given the deterministic default, When generation runs, Then no LLM is
  invoked (FR-003).

### US2 — Verified content only (P1)
As a user trusting cairn's verification contract, I want every symbol in a
generated skill to exist in the graph.

**Acceptance criteria**:
- AC1: Given a generated skill, When the deterministic critic runs, Then
  every referenced symbol is graph-verified or rejected before write (FR-004).

## Requirements
- **FR-001**: The system shall provide `cairn skill generate <selector>`
  accepting a module name, directory prefix, or explicit symbol list, writing
  a `SKILL.md` with frontmatter and a body assembled from compass, symbols,
  and memory.
- **FR-002**: The generated skill shall rank symbols by structural centrality
  (from precomputed transitive impact) and include only symbols whose
  referenced definitions exist in the graph.
- **FR-003**: The default generation path shall be deterministic with no LLM
  invocation; WHERE LLM polish is enabled, it shall run through the existing
  task queue behind the deterministic critic.
- **FR-004**: The deterministic critic shall verify every symbol reference in
  a generated skill against the graph before the skill is written.
- **FR-005**: The output format shall be [NEEDS CLARIFICATION: Anthropic's SKILL.md format as the primary target, a neutral structured-markdown format, or both (Anthropic primary + neutral export)? The proposal leaves standardization open.]

## Scope
**In**: generation command, selector resolution, centrality ranking, critic
verification, deterministic output, optional LLM polish via task queue,
tests; distribution rides the existing install-agents per-client wiring.
**Out (deferred)**: per-agent-client format variants beyond FR-005's ruling;
skill marketplace/publishing; skill versioning and diffing; non-markdown
skill bodies.

## Assumptions & risks
- Assumption: skill-format adoption continues across agent clients; the
  format is markdown-plus-frontmatter and stable enough to target.
- Risk: Anthropic's skill format is vendor-specific and may change —
  mitigation: the generator's content assembly is format-agnostic; the
  emitter is a thin final layer swappable per FR-005's ruling.

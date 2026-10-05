# Spec: corpus-breadth

**Status**: draft
**Effort**: large
**Created**: 2026-10-05
**Branch**: TBD

## What
Extend cairn's indexing beyond code and markdown: PDFs (with citation
extraction), images (vision extraction), SQL schemas, Terraform/HCL module
graphs, and package manifests (as canonical `depends_on` hub nodes) become
first-class graph citizens, following the graphify precedent of one graph
spanning the whole corpus.

## Why
A codebase's context is bigger than its source: the schema, the infra
definitions, the dependency manifests, and the papers/diagrams behind
design decisions all shape what the code means. Today cairn's graph covers
code symbols and a separate docs→knowledge layer only.

## Business value
Agents orient on the whole corpus; "what does this function read from the
schema" and "which module does this Terraform define" become graph
questions. This draft is parked pending the decisions below.

## User stories
### US1 — TBD
Parked: stories are authored only after the clarify pass below resolves.

## Requirements
- **FR-001**: TBD — artifact types and their graph representation are the
  first decision. [NEEDS CLARIFICATION: which artifact types are in v1
  scope — PDFs, images, SQL schemas, Terraform/HCL, package manifests — and
  in what priority order?]
- **FR-002**: TBD. [NEEDS CLARIFICATION: extraction model per type —
  deterministic parsers only, or LLM/vision passes for PDFs/images, and if
  LLM: which backend contract (the embedding-server precedent exists)?]
- **FR-003**: TBD. [NEEDS CLARIFICATION: how non-code artifacts relate to
  the symbol graph — separate node kinds joined by edges (the
  knowledge_edges precedent), or the concept-space-unification model?]

## Quality attributes
TBD pending clarify.

## Scope
**In**: TBD post-clarify. **Out (deferred)**: video/audio ingestion;
Google-Workspace-style remote sources.

## Assumptions & risks
- Risk: LLM-based extraction breaks cairn's deterministic-build guarantee —
  mitigation: gate behind extras/flags, keep the code graph build
  LLM-free.
- Risk: scope creep across five artifact types at once — mitigation: v1
  picks one or two types by the FR-001 ruling.

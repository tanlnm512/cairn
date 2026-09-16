# Spec: cross-repo-federation

**Status**: draft
**Created**: 2026-09-16
**Branch**: `feat/cross-repo-federation`

## What
Semantic search across every registered workspace store at once: federated
queries merge per-store results with per-repo attribution, and
`cairn ask --all-repos` routes natural-language questions across the
federation — the local-first answer to organization-scale code search.

## Why
Cross-repo context is a confirmed agent need. Cairn's structural reach
today is bounded by one store: `cross_repo_deps` maps dependencies across
repos indexed into the same database via namespaces, and every retrieval
tool queries the resolved workspace's store only — meaning-level search
stops at the store boundary even though the workspace registry
(`~/.cairn/workspaces.json`, written by `cairn init`) already
enumerates every store on the machine. Federation is a fan-out query over
that registry; per-store ranks merge with the same reciprocal-rank fusion
the hybrid retrieval stack already uses for BM25+vector merging, so
incomparable store scores need no new theory. Sourcegraph occupies
enterprise code search as SaaS; cairn's angle is local-first and free.

## Business value
Teams answer "which repos have authentication logic like ours?" across all
registered stores with attribution. Success: a federated semantic query over
N fixture stores returns merged, attributed results; structural tools'
per-repo behavior unchanged.

## User stories
### US1 — Federated search (P1)
As a developer, I want one semantic search across all my registered repos.

**Acceptance criteria** (each trace to an FR below):
- AC1: Given multiple registered stores with embeddings or lexical indexes,
  When a federated semantic search runs, Then merged results return with
  per-repo attribution and scores (FR-001).

### US2 — Cross-repo ask (P2)
As an agent, I want natural-language answers across all repos.

**Acceptance criteria**:
- AC1: Given registered stores, When `cairn ask --all-repos <question>` runs,
  Then the answer routes across stores with per-repo context attribution (FR-003).

## Requirements
- **FR-001**: The system shall provide federated semantic search — exposed
  as an MCP tool and a new CLI command — querying every registered
  workspace store and merging results with per-repo attribution, using
  lexical fallback where a store lacks embeddings.
- **FR-002**: WHERE stores share a compatible embedding backend, the
  federation shall optionally use one shared backend; otherwise each store
  serves its own vectors with results fused at rank level.
- **FR-003**: `cairn ask --all-repos <question>` shall route the question
  across registered stores and compose an answer with per-repo attribution.
- **FR-004**: Federated queries shall report dropped/unavailable stores
  explicitly (a store that is missing, locked, or unindexed is named, never
  silently skipped).
- **FR-005**: Existing single-store tool behavior shall remain unchanged.

## Scope
**In**: federation query path, per-repo attribution, ask routing, explicit
unavailable-store reporting, fixture multi-store tests.
**Out (deferred)**: federation-wide exact-edge resolution across repo
boundaries; a central multi-repo index (federation queries stores, it does
not merge them); permission boundaries between stores; remote federation
over the network.

## Assumptions & risks
- Assumption: registered workspaces are trusted (same user/team); no
  cross-store authorization in this spec.
- Risk: score comparability across stores with different backends —
  mitigation: rank-level fusion (RRF) rather than raw-score merging, the
  same approach the hybrid retrieval stack already uses.

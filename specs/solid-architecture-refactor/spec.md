# Spec: solid architecture refactor

**Status**: draft
**Created**: 2026-09-15
**Branch**: `codex/solid-architecture-refactor`

## What
Refactor the major cairn subsystems behind explicit internal seams so that parser, embedding, dashboard, retrieval, LLM, CLI health, and graph-persistence behavior remains unchanged while each concern gains a smaller, replaceable implementation boundary.

## Why
Several load-bearing modules currently combine orchestration, transport, persistence, telemetry, presentation, and dispatch. That makes new languages, embedding providers, routes, retrieval stages, and health checks harder to add without editing broad central modules, and it increases regression risk during ordinary maintenance.

## Business value
- Maintainers can add supported capabilities through focused adapters instead of editing god modules.
- Reviewers can reason about a transport, route, retrieval stage, or persistence concern in isolation.
- Contributors preserve current user-facing behavior while reducing architectural coupling.
- Success is measured by stable black-box behavior, no new import cycles, no new complexity findings, and reduced dispatch/presentation responsibility in the identified central modules.

## User stories
### US1 — Parser extension (P1)
As a maintainer, I want language parsing resolved through a parser factory, so that adding or changing a parser does not require editing the graph builder.

**Acceptance criteria** (each traces to an FR below):
- AC1: Given the supported language set, When a file is indexed, Then the same parser and parsed result are selected as before.
- AC2: Given a repeated language lookup, When the factory resolves it, Then it returns the cached parser instance.
- AC3: Given an unsupported language, When indexing requests a parser, Then indexing receives no parser and handles that condition as before.

### US2 — Embedding provider isolation (P1)
As a maintainer, I want embedding backends selected through one backend contract and registry, so that provider transport and fallback policy do not require changes to the embedding entry point.

**Acceptance criteria** (each traces to an FR below):
- AC1: Given each configured backend, When text is embedded, Then vectors, dimensions, model identity, and degradation behavior match the existing contract.
- AC2: Given a backend becomes unavailable, When fallback resolution runs, Then the existing fallback order and cache-reset behavior are preserved.

### US3 — Dashboard modularity (P2)
As a maintainer, I want dashboard routes grouped by focused controllers, so that application composition remains separate from request handling and presentation.

**Acceptance criteria** (each traces to an FR below):
- AC1: Given the existing dashboard, When any route is requested, Then its URL, method, route name, response shape, and status semantics remain unchanged.
- AC2: Given a new dashboard section, When its routes are added, Then the app factory registers a controller route table without implementing request behavior inline.

### US4 — Retrieval pipeline isolation (P2)
As a maintainer, I want semantic search composed from explicit retrieval stages, so that query enrichment, retrieval, fusion, reranking, telemetry, and result assembly can be maintained independently.

**Acceptance criteria** (each traces to an FR below):
- AC1: Given an existing query and configuration, When semantic search executes, Then result fields, ranking behavior, degradation markers, and telemetry event semantics remain unchanged.
- AC2: Given a retrieval stage fails, When the pipeline handles the failure, Then the existing degradation path and observable markers are preserved.

### US5 — LLM backend substitution (P2)
As a maintainer, I want LLM backends to honor one timeout and malformed-result contract, so that switching between queue and subprocess execution does not silently change client behavior.

**Acceptance criteria** (each traces to an FR below):
- AC1: Given either backend and a timeout, When synthesis or extraction runs, Then the backend stops within the declared bounded wait.
- AC2: Given malformed extraction output, When either backend parses it, Then malformed records are skipped rather than raising inconsistent exceptions.

### US6 — Operational command modularity (P2)
As a maintainer, I want operational CLI behavior grouped by command family and reusable health checks, so that metrics, status, synchronization, doctor, and reporting evolve independently.

**Acceptance criteria** (each traces to an FR below):
- AC1: Given existing system commands, When a command runs in human or JSON mode, Then command names and output contracts remain unchanged.
- AC2: Given a new doctor check, When health validation is extended, Then the check is registered without enlarging one central conditional implementation.

### US7 — Graph persistence separation (P3)
As a maintainer, I want file, symbol, import, and edge writes isolated behind repository seams, so that indexing orchestration and SQL row mapping remain independently maintainable.

**Acceptance criteria** (each traces to an FR below):
- AC1: Given a full or incremental index build, When parsed files are persisted, Then stored rows, IDs, transaction boundaries, and recovery behavior remain unchanged.

## Requirements
- **FR-001**: The system shall resolve built-in language parsers through a single parser factory.
- **FR-002**: WHEN the same language is resolved repeatedly, the parser factory SHALL return the same cached parser instance.
- **FR-003**: WHEN graph indexing requests a parser, the graph builder SHALL delegate parser selection to the parser factory instead of constructing concrete parsers.
- **FR-004**: The system shall dispatch embedding requests through an embedding backend contract.
- **FR-005**: WHEN an embedding backend is selected, the system SHALL resolve it through a registry without hardcoding backend branches in the embedding entry point.
- **FR-006**: WHEN embedding configuration or backend availability changes, the system SHALL preserve the existing resolution order, fallback behavior, cache invalidation, vector format, dimensions, and model identity.
- **FR-007**: The dashboard app factory SHALL assemble route controllers without implementing request behavior inline.
- **FR-008**: WHEN any existing dashboard route is requested, the system SHALL preserve its URL, method, route name, response shape, rendering behavior, and status semantics.
- **FR-009**: The system shall compose semantic search from explicit query, retrieval, fusion, rerank, enrichment, telemetry, and result-assembly stages.
- **FR-010**: WHEN semantic search encounters a failed or unavailable stage, the system SHALL preserve the existing degradation path, result fields, provenance, ranking semantics, and telemetry event semantics.
- **FR-011**: The system shall expose LLM synthesis and extraction through contracts with bounded timeout and malformed-output behavior.
- **FR-012**: WHEN either LLM backend handles extraction output, the system SHALL skip malformed JSON records consistently.
- **FR-013**: The system shall organize operational CLI implementations by command family and registered health checks.
- **FR-014**: WHEN an existing system command runs, the system SHALL preserve command names, human output semantics, JSON output contracts, exit behavior, and redaction rules.
- **FR-015**: The system shall isolate file, symbol, import, and edge persistence behind repository seams used by graph indexing.
- **FR-016**: WHEN parsed files are inserted or reindexed, the system SHALL preserve stored values, generated IDs, transaction boundaries, batch behavior, and crash-recovery semantics.
- **FR-017**: The refactoring SHALL introduce no runtime dependency.
- **FR-018**: The completed refactoring SHALL introduce no module import cycle and no new static complexity finding.

## Scope
**In**:
- Internal parser factory and built-in parser registration.
- Embedding backend contract, registry, configuration/fallback separation, model lifecycle separation, and corpus service separation.
- Dashboard controller extraction and application-factory simplification.
- Semantic search pipeline extraction while preserving the public search API.
- LLM protocol normalization for synthesis and extraction.
- Operational CLI split and health-check registration.
- Repository seams for graph indexing persistence.
- Regression, architecture, and static-quality guardrails.

**Out (deferred)**:
- A new public third-party parser-plugin API.
- Embedding-provider feature changes, new retrieval algorithms, dashboard features, and LLM provider features.
- SQLite schema migrations or data migrations.
- Performance rewrites beyond preserving existing timing and caching behavior.
- Changing public CLI JSON, MCP result, telemetry event-name, or dashboard route contracts.

## Assumptions & risks
- Assumption: this spec covers the full roadmap rather than only the parser factory; parser extensibility is internal-first, and the complete implementation lands on one umbrella branch and one final implementation PR.
- Assumption: “no new complexity finding” compares the same Ruff rule set against the recorded baseline rather than requiring every legacy finding to disappear.
- Risk: broad refactors can alter hidden behavioral contracts — mitigation: characterization tests, compatibility wrappers, staged milestones, and a full closing regression gate.
- Risk: moving SQLite writes can change transaction timing — mitigation: repository tasks preserve explicit transaction ownership and are covered by indexing regression tests.
- Risk: dashboard extraction can break route names or template context — mitigation: route-name and response contract tests must run before and after each extraction phase.

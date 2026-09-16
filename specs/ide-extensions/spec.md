# Spec: ide-extensions

**Status**: draft
**Created**: 2026-09-16
**Branch**: `feat/ide-extensions`

## What
Native IDE extensions surfacing cairn inline: hover blast radius on symbols,
caller/callee code-lens counts, a graph tree-view panel, and
compass/memory display when opening files — talking to a local cairn server
over HTTP, no CLI or MCP configuration required from the user.

## Why
The dashboard is a good start but not inline; developers who never touch a
CLI or MCP config need a zero-config visual surface. The server side of an
extension already exists in two proven shapes: the dashboard (a
starlette/jinja2/uvicorn app on loopback with routes for graph, memory,
knowledge, and wiki data) demonstrates local HTTP + JSON rendering over the
store, and the SSE daemon demonstrates an auto-managed background server.
An extension is a new render surface over that infrastructure, not a new
backend. VS Code has the largest install base; JetBrains covers enterprise
Java/Kotlin teams where cairn's graph is strongest.

## Business value
Cairn reaches IDE-native developers with zero agent setup. Success: the
first extension installs from its marketplace, shows hover blast radius and
compass on a real repo, and needs no manual server config beyond the
extension's bundled start command.

## User stories
### US1 — Inline blast radius (P1)
As an IDE user, I want caller/callee counts and blast radius on symbol
hover.

**Acceptance criteria** (each trace to an FR below):
- AC1: Given an indexed workspace and the extension running, When hovering a
  known symbol, Then caller/callee counts and a depth-limited precise blast
  radius render in the hover (FR-001).
- AC2: Given an unindexed or stopped-server state, When hovering, Then the
  extension degrades to a non-intrusive status indicator (FR-004).

### US2 — Compass/memory surface (P2)
As an IDE user, I want module compass and relevant memory shown when I open
a file.

**Acceptance criteria**:
- AC1: Given a file with compass/memory content, When it opens, Then a panel
  renders the compass excerpt and relevant memories (FR-002).

## Requirements
- **FR-001**: The first-priority extension shall render, on symbol hover and
  via code-lens, precise caller/callee counts and a depth-limited blast
  radius from the local cairn server.
- **FR-002**: The extension shall render the containing module's compass
  excerpt and relevant memories for the active file.
- **FR-003**: The extension shall communicate with a local cairn HTTP server
  (started by or alongside the extension) with zero manual MCP/CLI
  configuration.
- **FR-004**: WHERE the workspace is unindexed or the server is stopped, the
  extension shall degrade gracefully to a status indicator, never blocking
  the editor.
- **FR-005**: The first-priority IDE shall be [NEEDS CLARIFICATION: VS Code first (largest install base) or JetBrains first (enterprise Java/Kotlin strength)? The proposal leaves ordering open; the second follows.]
- **FR-006**: The second-priority extension (per FR-005's ruling) shall reach
  feature parity for FR-001–FR-004 on its platform.

## Scope
**In**: one extension at full feature depth (FR-005 ruling), local HTTP
communication, graceful degradation, packaging for marketplace/manual
install, tests.
**Out (deferred)**: the second IDE (scoped, not forgotten); inline taint
decorations (depends on taint-tracking); remote-server connection from the
extension; editor-embedded graph editing.

## Assumptions & risks
- Assumption: a lightweight local HTTP endpoint on the existing serve
  infrastructure suffices (no bespoke protocol).
- Risk: extension packaging and marketplace processes differ per IDE —
  mitigation: one IDE first per FR-005, packaging decision recorded as a
  D-###; manual-install path always available.

# Spec: rationale-nodes

**Status**: done
**Effort**: standard
**Created**: 2026-10-05
**Branch**: `feat/rationale-nodes`

## What
Index the "why" that already lives in code: comments marked `NOTE:`, `WHY:`,
or `HACK:` become rationale records linked to the innermost enclosing
symbol (or the file when top-level), persisted in an additive `rationale`
table during the normal build, queryable via `cairn rationale`, and
surfaced in explore output. No new MCP tool — the tool count stays fixed.

## Why
Design intent concentrates in inline comments, and the graph never sees
them: an agent navigating to a symbol gets its signature and body but not
the constraint comment three lines up explaining why the code is shaped
that way. Docstrings are already indexed; marker comments — the ones
engineers actually write constraints in — are not. This indexes what
already exists; it does not encourage writing new rationale in code.

## Business value
Agents gain constraint awareness at navigation time without grepping; the
knowledge graph covers the why-layer of a codebase for the cost of one
table and some CST-walk work. Success: a build of the cairn repo itself
produces rationale records for existing marker comments, attributed to
their enclosing symbols, visible in `cairn rationale` and explore output.

## User stories
### US1 — Extraction at build (P1)
As a user, I want marker comments captured during the normal build, so that
no extra step is needed.

**Acceptance criteria** (each traces to an FR below):
- AC1: Given a file containing `# NOTE:`/`# WHY:`/`# HACK:` comments (or the
  language's comment syntax), When `cairn build` runs, Then the `rationale`
  table holds one record per marker comment with kind and text.
- AC2: Given a marker comment inside a function's span, When the build
  runs, Then the record is attributed to that symbol; given one at
  top level, Then it is attributed to the file (symbol NULL).

### US2 — Query (P2)
As an agent, I want to list the rationale for a symbol or file, so that
constraints are visible before editing.

**Acceptance criteria**:
- AC1: Given rationale records for a symbol, When `cairn rationale
  --symbol SYMBOL` runs, Then the records print ordered by line with kind
  tags.

### US3 — Explore surfacing (P2)
As an agent using explore, I want rationale attached to the symbols I
query, so that the constraint is in context without a second call.

**Acceptance criteria**:
- AC1: Given symbols with rationale records, When explore runs, Then a
  "Rationale" section lists them; given none, Then no section appears.

### US4 — Incremental honesty (P2)
As a user running `cairn update`, I want rationale kept fresh, so that
removed comments don't linger.

**Acceptance criteria**:
- AC1: Given a reindexed file, When the incremental update completes, Then
  that file's rationale rows are exactly what its current comments imply.

## Requirements
- **FR-001**: The build shall extract comments whose text begins with
  `NOTE:`, `WHY:`, or `HACK:` (after the comment opener and optional
  whitespace) in every language whose parser the registry supports with a
  mapped comment node type, and shall persist them in an additive
  `rationale` table (id, file_id, symbol_id nullable, line, kind, text).
- **FR-002**: Attribution shall attach a record to the innermost enclosing
  callable or type whose span contains the comment line; otherwise
  symbol_id is NULL (file-level).
- **FR-003**: The system shall provide `cairn rationale` with
  `--symbol`/`--file` filters, printing records ordered by line with kind
  tags.
- **FR-004**: Explore output shall include a "Rationale" section when any
  queried symbol has rationale records, and omit it otherwise; the MCP tool
  count shall not change.
- **FR-005**: Docstrings shall not produce rationale records (they are
  already captured on the symbol).
- **FR-006**: WHERE a language has no mapped comment node type, the build
  shall produce no rationale records for it and SHALL NOT fail.
- **FR-007**: Incremental reindex and `cairn update` shall delete and
  re-derive the affected files' rationale rows together with their
  symbols/edges.
- **FR-008**: `TODO:`/`FIXME:` markers are explicitly out of scope and
  shall not be captured.

## Quality attributes
- **NFR-001**: Security — not applicable: local index data only, no new
  surface.
- **NFR-002**: Privacy — not applicable: rationale text is repo content
  already indexed elsewhere; nothing leaves the store.
- **NFR-003**: Performance — applicable: WHEN the build runs with
  rationale extraction, the per-file overhead shall be negligible (comment
  scan rides the existing CST walk; no second pass over the tree).
- **NFR-004**: Reliability — not applicable: additive table, rebuildable
  from source, no new failure contract.
- **NFR-005**: Observability — not applicable: build summary may report
  rationale counts; no new signal required.
- **NFR-006**: Accessibility — not applicable: no UI surface is touched.

## Scope
**In**: comment-node-type map per supported language; extraction in the
shared parser walk; additive `rationale` table; `cairn rationale` CLI;
explore "Rationale" section; incremental delete/re-derive; docs.
**Out (deferred)**: `TODO:`/`FIXME:` capture; ADR/design-doc citation
linking (concept-space-unification spec's territory); compass/wiki
integration beyond optional counts; rationale for languages outside the
parser registry; any MCP tool surface change.

## Assumptions & risks
- Assumption: every registry language has a stable comment node type
  nameable per grammar (e.g. `comment`, `line_comment`); verified per
  language with a tiny parse in tech-spec, with the map table as the
  single source.
- Risk: marker comments above a definition (e.g. directly preceding a
  function) fall outside its span and would be file-attributed —
  mitigation: attribution rule is span-containment only, pinned with
  fixture tests; a follow-up can add look-ahead if it proves useful.
- Risk: multi-line marker comments could duplicate records per line —
  mitigation: one record per comment node; continuation lines fold into
  the text.
- Risk: per-language parser variance (the generic tier vs dedicated
  parsers) — mitigation: extraction lives in the shared walk with the map
  consulted per language, not per parser copy.

# Research: ui-dashboard-cross-links

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-20
External grounding for tech decisions: every claim below carries a source
URL — no unsourced "it is known that". The tech agent consumes this file
when choosing options in tech-spec.md.

## Questions

### RQ1: URL parameters as cross-view state — shareability and limits?
- **source**: [MDN — URLSearchParams](https://developer.mozilla.org/en-US/docs/Web/API/URLSearchParams) · **claim**:
  query parameters are a first-class, serializable representation of view
  state that survives copying, bookmarking, and programmatic construction. ·
  **relevance**: FR-001, FR-002 (links as plain URLs) · **confidence**: high
- **source**: [whatwg URL LS](https://url.spec.whatwg.org/) · **claim**:
  percent-encoding rules are defined per component, so arbitrary ids (uuid
  hex, repo ids) travel safely in query strings. · **relevance**: FR-005
  (window carry-over composed with other params) · **confidence**: high
- no credible source found — decide from first principles <!-- fallback only -->

### RQ2: How do multi-view dashboards structure drill-down navigation?
- **source**: [Datasette — linked tables](https://docs.datasette.io/en/stable/custom_templates.html) · **claim**:
  Datasette's exploration model makes every displayed entity (table, row,
  foreign key) a hyperlink into the matching filtered view, which is the
  navigation pattern this spec mirrors. · **relevance**: all FRs (prior art) · **confidence**: medium
- **source**: [Grafana — dashboard links and data links](https://grafana.com/docs/grafana/latest/dashboards/build-dashboards/manage-dashboard-links/) · **claim**:
  Grafana distinguishes per-row data links from nav-level links and supports
  carrying time-range context through them — the same split as FR-005
  (row links) vs FR-006 (shared nav). · **relevance**: FR-005, FR-006 · **confidence**: high

### RQ3: Graph-node context actions in vis-network?
- **source**: [vis-network docs — Network events](https://visjs.github.io/vis-network/docs/) · **claim**:
  vis-network emits selectNode/click events carrying node ids, and nodes can
  carry arbitrary custom fields — enough to drive an inspect action without
  extra libraries. · **relevance**: FR-004 · **confidence**: high
- **source**: [Cytoscape.js vs vis comparison](https://doc.linkurious.com/ogma/latest/compare/visjs.html) · **claim**:
  interactive node actions are a baseline capability across mainstream graph
  canvases; the differentiator is what the action navigates to, not the
  event wiring. · **relevance**: FR-004 (shared path with graph-nav) · **confidence**: medium

## Options summary

### Link embodiment (FR-001, FR-002)
- **plain anchor with query params** — server round-trip, filter state in the
  URL, works with the existing snapshot architecture
- **client-side router (history.pushState)** — no round-trip but duplicates
  rendering state in JS; overkill for server-rendered views

### Session focus on chains (FR-002)
- **session URL param + data-layer filter** — deep-linkable, consistent with
  /history's existing params
- **template-side highlight/anchor only** — no server change but unbounded
  render cost on the legacy 'unknown' session (survey note)

### Inspect action surface (FR-004)
- **node click → navigate to scope=symbol+focus** — one interaction, reuses
  the existing symbol scope (survey Q5)
- **in-page popup with neighborhood** — no navigation but duplicates the
  graph view's job; overlaps graph-nav's expand more than it helps

# Research: ui-dashboard-graph-nav

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-20
External grounding for tech decisions: every claim below carries a source
URL — no unsourced "it is known that". The tech agent consumes this file
when choosing options in tech-spec.md.

## Questions

### RQ1: Disambiguation UX for name search in graph tools?
- **source**: [Grafana — dashboard search](https://grafana.com/docs/grafana/latest/dashboards/build-dashboards/manage-dashboard-links/) · **claim**:
  operational tools resolve ambiguous matches with an inline candidate
  list (label + distinguishing context) rather than picking silently or
  erroring. · **relevance**: FR-002 (candidates shown, not arbitrary) · **confidence**: medium
- **source**: [MDN — datalist element](https://developer.mozilla.org/en-US/docs/Web/HTML/Element/datalist) · **claim**:
  the platform's native combobox pattern pairs a text input with a
  candidate dropdown, implementable with zero JS libraries. ·
  **relevance**: FR-001, FR-002 (search box + disambiguation) · **confidence**: high

### RQ2: Layout options in vis-network?
- **source**: [vis-network docs — layout hierarchy option](https://visjs.github.io/vis-network/docs/) · **claim**:
  vis-network's layout engine supports hierarchical (top-down) arrangement
  toggled by options on the same DataSet, without remounting the network. ·
  **relevance**: FR-004 (toggle without losing focus) · **confidence**: high
- **source**: [Cytoscape.js vs vis-network comparison](https://doc.linkurious.com/ogma/latest/compare/visjs.html) · **claim**:
  force-directed vs hierarchical is a standard two-mode offering across
  graph canvases; re-running layout on the same data is the norm. ·
  **relevance**: FR-004 · **confidence**: medium

### RQ3: Incremental node expansion — server or client?
- **source**: [MDN — fetch](https://developer.mozilla.org/en-US/docs/Web/API/Window/fetch) · **claim**:
  fetching subgraph deltas as JSON and merging into the rendered DataSet
  is the standard client-side pattern for grow-as-you-explore graphs. ·
  **relevance**: FR-003 (expansion without form resubmit) · **confidence**: high
- no credible source found — decide from first principles <!-- fallback only -->

## Options summary

### Search surface (FR-001, FR-002)
- **input + server candidates endpoint + confirm-to-focus** — exact or
  disambiguated selection, one interaction (SC-1); works without JS
  frameworks
- **datalist-backed autocomplete** — native UX, but candidate semantics
  (file/kind context) render poorly in some browsers
- **plain form submit on exact name** — no disambiguation; fails FR-002

### Expansion mechanics (FR-003)
- **server endpoint returning a node's neighbors as JSON; client merges** —
  precise, countable, keeps FR-005 honest
- **client re-queries scope with wider focus list** — reuses /graph but
  re-renders everything and loses the incremental feel
- **precompute full graph, expand client-side** — impossible at scope
  caps; defeats the query layer's LIMITs

### Multi-hop traversal (FR-003 backing)
- **wire the dead depth param via a recursive CTE / walk shared with the
  impact scope** — one traversal, two consumers
- **cap expansion at 1-hop per action** — no new traversal; users chain
  expansions manually (may suffice; cheaper)

### Layout toggle (FR-004)
- **vis-network built-in hierarchical option toggled on the live network** —
  zero new dependency, same DataSets
- **re-instantiate the network per layout** — simpler code; loses camera
  state (focus) on toggle — violates FR-004's "without losing the current
  focus" unless camera is restored

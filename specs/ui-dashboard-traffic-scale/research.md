# Research: ui-dashboard-traffic-scale

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-20
External grounding for tech decisions: every claim below carries a source
URL — no unsourced "it is known that". The tech agent consumes this file
when choosing options in tech-spec.md.

## Questions

### RQ1: OFFSET vs keyset pagination on growing tables?
- **source**: [Use The Index, Luke — Pagination: OFFSET](https://use-the-index-luke.com/sql/paging/offset) · **claim**:
  OFFSET pagination degrades linearly with page depth and skips/duplicates
  rows when rows are inserted between page fetches; a seek (keyset) method
  on an ordered unique key stays O(log n) and stable under concurrent
  inserts. · **relevance**: FR-001, FR-006, US1-AC2 (no row repeats) · **confidence**: high
- **source**: [PostgreSQL wiki — Don't Do This (OFFSET pagination)](https://wiki.postgresql.org/wiki/Don%27t_Do_This) · **claim**:
  the database community's standing guidance is to prefer keyset
  pagination for user-facing page-through of large result sets. ·
  **relevance**: FR-001 · **confidence**: medium
- no credible source found — decide from first principles <!-- fallback only -->

### RQ2: SQLite index behavior for range predicates on timestamps?
- **source**: [SQLite query planner docs](https://sqlite.org/queryplanner.html) · **claim**:
  a two-column index on (invoked_at, id) can serve both the range scan
  `invoked_at >= ?` and the seek `(invoked_at, id) < (?, ?)` as a single
  index descent, avoiding full-table scans. · **relevance**: FR-002, FR-005 (spec's unindexed-timestamp risk) · **confidence**: high
- **source**: [SQLite partial indexes](https://sqlite.org/partialindex.html) · **claim**:
  partial indexes can serve queries whose WHERE matches the index's
  predicate, trading coverage for size. · **relevance**: FR-005 (store size
  discipline; likely unnecessary here but documents the option) · **confidence**: high

### RQ3: Capping grouped/chain views without losing counts?
- **source**: [Grafana dashboard best practices](https://grafana.com/docs/grafana/latest/dashboards/build-dashboards/best-practices/) · **claim**:
  operational dashboards bound rendered series and show an explicit
  "truncated / showing N of M" affordance rather than rendering
  everything, keeping interaction responsive. · **relevance**: FR-04's
  expand mechanism and count display · **confidence**: medium
- **source**: [MDN — Intersection Observer](https://developer.mozilla.org/en-US/docs/Web/API/Intersection_Observer_API) · **claim**:
  client-side lazy loading of long lists can defer offscreen work without
  pagination state in the URL. · **relevance**: FR-001 alternative
  (infinite scroll) · **confidence**: high

## Options summary

### Pagination strategy (FR-001)
- **keyset on (invoked_at DESC, id DESC)** — stable under inserts (live
  data!), no repeats, needs composite index
- **OFFSET/LIMIT page numbers** — familiar page controls; unstable + slow
  at depth on a growing table
- **infinite scroll via IntersectionObserver** — no pagination UI; loses
  shareable page URLs and composes worse with cross-links

### Window filtering (FR-002, FR-003)
- **SQL WHERE invoked_at >= computed epoch** — computed per preset; one
  index serves all presets
- **materialized per-window aggregates** — faster reads; new write-side
  complexity the recording pipeline doesn't need yet

### Chain bounding (FR-004)
- **top-N chains by recency + per-chain head/tail window with explicit
  expand** — bounded render, visible truncation, server-side
- **full render + client virtualization** — moves the cost, keeps the
  whole-table Python grouping (survey Q3) as the bottleneck

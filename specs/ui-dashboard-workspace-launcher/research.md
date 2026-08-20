# Research: ui-dashboard-workspace-launcher

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-20
External grounding for tech decisions: every claim below carries a source
URL — no unsourced "it is known that". The tech agent consumes this file
when choosing options in tech-spec.md.

## Questions

### RQ1: Multi-database switching in single-process local web tools?
- **source**: [Datasette — serve multiple database files](https://docs.datasette.io/en/stable/custom_templates.html) · **claim**:
  Datasette serves many SQLite files from one process, addressing each by
  URL path segment, with per-database inspection pages — prior art for
  restart-free multi-store browsing. · **relevance**: FR-003 (switching
  without restart) · **confidence**: medium
- **source**: [SQLite URI filenames](https://sqlite.org/uri.html) · **claim**:
  `file:path?mode=ro` opens are cheap and lock-free for readers, so
  per-request store opens scale without connection pooling. ·
  **relevance**: FR-003, FR-005 (probe strategy) · **confidence**: high

### RQ2: Cheap per-file stats without opening databases?
- **source**: [Python docs — os.stat](https://docs.python.org/3/library/os.html#os.stat) · **claim**:
  file size and mtime are available via a single syscall without opening
  the file, making size/freshness columns free relative to SQL probes. ·
  **relevance**: FR-001, FR-005 (200-store budget) · **confidence**: high
- **source**: [SQLite file format docs](https://sqlite.org/fileformat.html) · **claim**:
  the DB header is page 1; row counts are not stored, so count queries
  require opening the DB (or maintaining a sidecar). · **relevance**: FR-001
  (which stats need an open vs a stat) · **confidence**: high

### RQ3: Session vs URL-path state for "current selection" in a dashboard?
- **source**: [MDN — URL query params](https://developer.mozilla.org/en-US/docs/Web/API/URLSearchParams) · **claim**:
  query-param state is shareable, bookmarkable, and server-visible on
  every request — unlike cookie/session state which hides from the user. ·
  **relevance**: FR-003's switching seam · **confidence**: high
- **source**: [whatwg URL LS](https://url.spec.whatwg.org/) · **claim**:
  path segments and query params are both first-class routing state;
  path-segment per store is the Datasette pattern (RQ1). · **relevance**: FR-003 · **confidence**: high

## Options summary

### Switching seam (FR-003)
- **per-request store param (query/header) resolved inside handlers** —
  restart-free, shareable URLs, composes with existing per-request
  read-only opens (survey Q5)
- **app-state reconnect (rebuild app per selection)** — simplest mental
  model but restart-equivalent churn; loses shareable state
- **path-segment routing per store (Datasette style)** — clean URLs but
  rewrites every route; heavier than the spec needs

### Probe strategy (FR-001, FR-005)
- **stat-only columns + SQL open for counts, batched with a budget** —
  size/mtime free; counts pay one open each, capped and cacheable
- **stat-only v1 (no opens), counts added behind the budget** — fastest;
  leaves FR-001's call-count column unimplemented
- **background probe cache with explicit refresh** — bounded renders;
  staleness complexity the spec's risk note already flags as fallback

### Enumeration source (FR-002)
- **registry ∪ store-dir listing, reconciled with states** — covers stale
  registrations and orphan dirs (both proven shapes on this machine)
- **registry only** — misses orphan stores; fails FR-002's missing-state case

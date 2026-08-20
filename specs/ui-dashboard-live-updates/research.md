# Research: ui-dashboard-live-updates

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-20
External grounding for tech decisions: every claim below carries a source
URL — no unsourced "it is known that". The tech agent consumes this file
when choosing options in tech-spec.md.

## Questions

### RQ1: How do no-build server-rendered dashboards implement polling refresh?
- **source**: [htmx docs — polling](https://htmx.org/docs/#polling) · **claim**:
  htmx implements auto-refresh as a single attribute that re-issues a GET on
  an interval and swaps the returned HTML fragment into the page, requiring
  no client build step. · **relevance**: FR-001 (poll mechanism shape) · **confidence**: high
- **source**: [MDN — Window.setInterval](https://developer.mozilla.org/en-US/docs/Web/API/Window/setInterval) · **claim**:
  setInterval scheduling can drift and stack callbacks when the tab is
  throttled, so visible-cadence work should re-arm per tick or use the
  visibility state. · **relevance**: FR-001, SC-2 (hour-long open page) · **confidence**: high
- **source**: [MDN — Document.visibilityState](https://developer.mozilla.org/en-US/docs/Web/API/document/visibilityState) · **claim**:
  background tabs can throttle timers to ~1/minute, and pages can observe
  visibility changes to pause work that is pointless while hidden. ·
  **relevance**: FR-004 (pause semantics), SC-2 (responsiveness) · **confidence**: high

### RQ2: Full-content swap vs incremental append for refresh idempotency?
- **source**: [MDN — Element.innerHTML](https://developer.mozilla.org/en-US/docs/Web/API/Element/innerHTML) · **claim**:
  replacing innerHTML resets descendant DOM state (scroll anchoring, text
  selection, focus) in one operation, which is exactly where flicker and
  lost position come from. · **relevance**: FR-003, FR-006 · **confidence**: high
- **source**: [whatwg HTML LS — session history and fragment navigation](https://html.spec.whatwg.org/multipage/browsing-the-web.html) · **claim**:
  the platform preserves scroll position on same-URL fragment navigation,
  so refresh strategies that avoid a full navigation inherit that
  preservation for free. · **relevance**: FR-003 (input/scroll state) · **confidence**: medium
- no credible source found — decide from first principles <!-- fallback only -->

### RQ3: Connection-state detection for a local HTTP UI?
- **source**: [MDN — NavigatorOnLine.online/offline events](https://developer.mozilla.org/en-US/docs/Web/API/NavigatorOnLine) · **claim**:
  browser online/offline events signal network connectivity, not whether a
  specific origin is reachable — fetch failure is the reliable local signal. ·
  **relevance**: FR-005 (disconnected state + auto-recovery) · **confidence**: high
- **source**: [whatwg Fetch LS](https://fetch.spec.whatwg.org/) · **claim**:
  a refused connection rejects the fetch promise with a TypeError, giving a
  deterministic client-side signal to drive a disconnected indicator. ·
  **relevance**: FR-005 · **confidence**: high

## Options summary

### Refresh transport (FR-001)
- **fetch + HTML fragment swap** — reuses existing Jinja handlers for the body
  region; one JS file, no new dependency
- **htmx vendored** — declarative but adds a vendored dependency for one behavior
- **meta-refresh / location.reload** — zero JS but loses filter/scroll state (violates FR-003)

### Refresh scheduling (FR-001, FR-004)
- **re-arming setTimeout loop** — avoids callback stacking; interval changeable per state
- **setInterval** — simpler; drifts and stacks under tab throttling
- **visibility-gated pause** — free win aligned with FR-004's pause control

### Row idempotency (FR-006)
- **keyed merge on monotonic row id** — append rows with id greater than the
  last seen; no duplicates by construction
- **full re-render of the fetched page** — simplest correct swap; relies on
  server-side ORDER BY for ordering, idempotent if swap is atomic

# Tech Spec: ui-dashboard-polish

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-20
Every file/symbol citation below comes verbatim from [survey.md](survey.md)
or a grep run in this session — never from memory.

## Architecture

```mermaid
flowchart TD
  boot["app startup"] -- "background prewarm thread" --> probes["reranker/ann probes cached"]
  req["GET /health"] --> probes
  trunc["instrument truncation branch"] -- "magnitude columns" --> db2[(tool_metrics)]
  sink["sink flush transaction"] -- "extended _prune" --> db2
  tokensview["tokens view"] -- "mode-aware estimates + truncation counts" --> db2
  tok["tokenizer helper (optional import)"] --> tokensview
  export["/history.csv|.json /tokens.csv|.json"] --> datafns["existing data functions with current filters"]
  theme["app.css variables + localStorage toggle"] --> pages["all views"]
```

Six small refinements share one theme: extend existing seams (probes,
truncation chokepoint, prune transaction, data functions, CSS variables)
rather than adding subsystems. FR-007's split is structural: aging runs in
the sink, the dashboard only displays.

## Solution
### Chosen approach
- **Health prewarm** (FR-001): at app startup, a daemon thread runs the
  probe set once (reranker import, ann backend check, embedding counts)
  and caches results; `get_health` serves the cache and refreshes it on a
  slow cadence. First /health render stops paying the import (survey Q1).
- **Truncation magnitude** (FR-003, record side): the truncation branch in
  `src/cairn/mcp_server/metric_buffering.py` (survey Q3) additionally
  records original-vs-delivered chars per call via additive tool_metrics
  columns (migration seam, survey's schema precedent); the existing
  `truncate_result` event keeps firing unchanged (occurrence analytics),
  but magnitude now survives the events cap (survey supporting evidence).
- **Retention** (FR-004, FR-007): `_prune` in `src/cairn/telemetry/sink.py`
  (survey Q4) gains a tool_metrics deletion with env-configurable caps
  (rows, default generous; optional age bound), inside the same flush
  transaction; `get_health` reports the policy + current row count
  (survey Q7's dict shape).
- **Tokenizer mode** (FR-002): a helper probing for an available exact
  tokenizer (transformers behind the existing semantic extra — research
  RQ1; tiktoken declined as too provider-specific) with graceful
  chars÷4 fallback; the active mode is computed once per process and
  rendered wherever estimates appear; bench comparability is preserved by
  the visible label (survey Q2's shared-constant note).
- **Export** (FR-005): routes wrapping the existing data functions with
  the current filter params; CSV via Python's csv module (RFC 4180 —
  research RQ2), JSON via stdlib; Content-Disposition attachment.
- **Theme** (FR-006): dark palette as CSS variable overrides on a
  `[data-theme=dark]` selector; default follows prefers-color-scheme; a
  toggle persists the override in localStorage (research RQ3).

### Alternatives rejected
| Alternative | Why rejected |
|-------------|--------------|
| tiktoken as the exact mode | OpenAI-encoding exactness only; wrong audience default (research RQ1) |
| Truncation magnitude widened into the events payload | Still pruned at the 5000-row events cap; not per-call (survey Q3 gap) |
| Time-only retention | Spikes can still blow the store; row cap composes with age optionally |
| Separate aging worker/thread | New lifecycle to own; the flush transaction already exists (survey Q4) |
| Hand-rolled CSV join | Corrupts quoted fields (RFC 4180, research RQ2) |
| Cookie-based theme persistence | Server state for a per-browser preference; localStorage is the fit |

## Impact analysis
- `metric_buffering.instrument`'s truncation branch gains a column write —
  guarded best-effort like its event emission; `tests/test_metrics.py` /
  `tests/test_metrics_extensions.py` baselines must stay green.
- `sink._prune` extension: runs inside the existing flush transaction;
  failure isolation per-table already established (its try/except shape);
  `tests/test_telemetry.py` gains cap tests.
- `get_health` gains keys — additive; `health.html` renders them.
- Export routes read through the same data functions the views use, so
  parity is by construction (same function, same params).
- CSS: variables already exist (survey Q6) — no selector rewrites, only
  palette values + one attribute selector + toggle script.
- Cross-spec: traffic-scale's filters flow into export via the shared
  param-forwarding pattern; cli-usage-recording's source column surfaces
  in exports automatically (same SELECT).

## Code guide
### Health prewarm
- Touches: `src/cairn/dashboard/app.py` (startup hook),
  `src/cairn/dashboard/data.py` (probe cache)
- Approach: cache probe results with a timestamp; serve stale-while-
  revalidate on /health; prewarm thread at create_app time.
- Verify before implementing: `grep -n "reranker_available" src/cairn/dashboard/data.py`
- Pitfalls: never block create_app on the probes (background thread);
  cache must be per-process and cheap to invalidate on env change in tests.

### Truncation columns + prune extension
- Touches: `src/cairn/mcp_server/metric_buffering.py` (survey Q3),
  `src/cairn/graph/schema.py` (additive columns), `src/cairn/telemetry/sink.py`
  (survey Q4)
- Approach: record original/delivered chars on the truncation branch;
  _prune gains `DELETE FROM tool_metrics ... Beyond cap` with
  env-configurable limits; health reports policy + count.
- Verify before implementing: `grep -n "_MAX_EVENTS_ROWS\|_prune" src/cairn/telemetry/sink.py`
- Pitfalls: keep the MCP INSERT backward-compatible (columns nullable —
  pre-migration rows and non-truncated calls read as no-evidence, not
  zero); pruning key is id (monotonic) — matches the events pattern.

### Tokenizer helper + tokens view
- Touches: new helper module under `src/cairn/dashboard/` or `src/cairn/bench/`,
  `src/cairn/dashboard/data.py`, `src/cairn/dashboard/templates/tokens.html`
- Approach: `active_tokenizer_mode()` → exact-name or "heuristic(chars/4)";
  mode-aware estimate function wraps the existing constant path; label
  rendered near totals.
- Verify before implementing: `grep -n "CHARS_PER_TOKEN" src/cairn/dashboard/data.py`
- Pitfalls: tokenizer import failure must fall back silently to heuristic
  with the label honest; never make the semantic extra a required import.

### Export + theme
- Touches: `src/cairn/dashboard/app.py` (routes), `history.html`/
  `tokens.html` (buttons), `src/cairn/dashboard/static/app.css`,
  `base.html`
- Approach: export routes call the view's data function with the request's
  params; csv.writer to a StringIO response; theme toggle sets
  data-theme + localStorage, default from prefers-color-scheme.
- Verify before implementing: `grep -n "Route(" src/cairn/dashboard/app.py | tail -3`
- Pitfalls: export must not become a pagination-aware duplicate of list
  logic — reuse the data function and export the filtered slice it
  returns; filename should carry the view + filter hint.

### Tests
- Touches: `tests/test_dashboard_app.py`, `tests/test_dashboard_data.py`,
  `tests/test_metrics_extensions.py`, `tests/test_telemetry.py`
- Approach: timing test for first-health (cache warm); truncation column
  assertions on a truncating call; prune cap tests; mode-selection tests
  with the import mocked present/absent; export parity byte-comparison;
  theme persistence is a manual/browser check plus the toggle script's
  unit-testable apply function.
- Verify before implementing: `uv run pytest tests/test_dashboard_app.py tests/test_telemetry.py -q`
- Pitfalls: the hermetic suite must pin env for retention caps and mock
  the tokenizer import explicitly (hermetic-suite lesson).

## References
- tiktoken (exact-mode candidate evaluated): https://github.com/openai/tiktoken
- transformers tokenizers (optional-extra rider): https://huggingface.co/docs/transformers
- RFC 4180 (CSV correctness): https://datatracker.ietf.org/doc/html/rfc4180
- prefers-color-scheme + localStorage (theme): https://developer.mozilla.org/en-US/docs/Web/CSS/@media/prefers-color-scheme
- Related specs: ui-dashboard (substrate), ui-dashboard-traffic-scale
  (filters export honors), cli-usage-recording (source column surfaces).

## Decisions
### D-001: Exact tokenizer rides the existing semantic extra
- **Context**: FR-002's optional-dependency assumption; research RQ1.
- **Decision**: transformers-based counting when the semantic extra's
  tokenizer is importable; else the labeled chars÷4 heuristic.
- **Consequences**: no new required dependency; mode label keeps bench
  comparability interpretable; tiktoken rejected as provider-specific.

### D-002: Truncation magnitude lives in tool_metrics, not the event
- **Context**: FR-003's "durably"; events cap prunes occurrences (survey).
- **Decision**: per-call columns recorded on the truncation branch; the
  event keeps firing for occurrence analytics.
- **Consequences**: magnitude survives pruning and joins per-tool
  aggregates directly; two recordings exist but answer different
  questions (occurrence rate vs magnitude).

### D-003: Retention extends the flush-transaction prune, dashboard never ages
- **Context**: FR-004 + FR-007's split.
- **Decision**: configurable row cap (+ optional age) in sink._prune;
  health displays policy and size.
- **Consequences**: one transactional seam; the read-only guard holds by
  construction; generous default documented beside CAIRN_TELEMETRY.

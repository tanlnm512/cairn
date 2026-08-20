# Research: ui-dashboard-polish

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-20
External grounding for tech decisions: every claim below carries a source
URL — no unsourced "it is known that". The tech agent consumes this file
when choosing options in tech-spec.md.

## Questions

### RQ1: Exact token counting without a hard dependency?
- **source**: [tiktoken on PyPI](https://github.com/openai/tiktoken) · **claim**:
  tiktoken gives exact counts for OpenAI BPE encodings (cl100k/o200k) but
  token ids diverge for other providers' vocabularies. · **relevance**:
  FR-002 (mode choice + honesty of "exact") · **confidence**: high
- **source**: [OpenAI cookbook — how to count tokens](https://cookbook.openai.com/examples/how_to_count_tokens_with_tiktoken) · **claim**:
  chars-per-token heuristics (~4 for English/code) are the accepted
  approximation when the real tokenizer is unavailable. · **relevance**:
  FR-002's documented heuristic baseline · **confidence**: high
- **source**: [transformers tokenizers](https://huggingface.co/docs/transformers) · **claim**:
  any HF-hosted model's tokenizer can count tokens offline via transformers
  — already cairn's optional semantic dependency, so an exact mode can ride
  an existing extra. · **relevance**: FR-002 (optional-dependency doctrine) · **confidence**: medium

### RQ2: CSV/JSON export fidelity?
- **source**: [RFC 4180 — CSV](https://datatracker.ietf.org/doc/html/rfc4180) · **claim**:
  CSV fields containing commas/quotes/newlines require quoting and escaped
  quotes — a hand-rolled writer will corrupt rows with redacted summaries. ·
  **relevance**: FR-005 (use a real writer, e.g. Python csv) · **confidence**: high
- **source**: [MDN — Content-Disposition](https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Content-Disposition) · **claim**:
  attachment disposition with a filename drives the browser save-as flow —
  the standard export delivery mechanism. · **relevance**: FR-005 · **confidence**: high

### RQ3: Dark theme with persistence?
- **source**: [MDN — prefers-color-scheme](https://developer.mozilla.org/en-US/docs/Web/CSS/@media/prefers-color-scheme) · **claim**:
  the OS-level dark preference is observable in CSS media queries; a manual
  override needs author state on top of it. · **relevance**: FR-006 · **confidence**: high
- **source**: [MDN — Window.localStorage](https://developer.mozilla.org/en-US/docs/Web/API/Window/localStorage) · **claim**:
  localStorage persists per-origin key/value state in the browser without
  cookies or server state. · **relevance**: FR-006 ("persisted per browser") · **confidence**: high

### RQ4: Retention policy shapes for append-only telemetry tables?
- **source**: [SQLite DELETE query planner](https://sqlite.org/queryplanner.html) · **claim**:
  id-order DELETE-by-subquery keeps pruning bounded when the retention key
  is indexed — tool_metrics' PK serves that role. · **relevance**: FR-004 · **confidence**: medium
- **source**: [OpenTelemetry spans retention practices](https://opentelemetry.io/docs/specs/otel/configuration/sdk-environment-variables/) · **claim**:
  bounded retention with explicit, visible limits is the norm for local
  telemetry stores; unbounded growth is treated as a defect. ·
  **relevance**: FR-004 (visible policy in the health panel) · **confidence**: medium

## Options summary

### Tokenizer mode (FR-002)
- **transformers tokenizer behind the existing semantic extra** — zero new
  required deps; exact for the tokenizer chosen, labeled by name
- **tiktoken optional extra** — exact for OpenAI encodings only; adds a dep
  for a cairn audience that is often not OpenAI-tokenized
- **heuristic only (status quo + label)** — honest but leaves FR-002's
  exact mode unimplemented

### Truncation durability (FR-003)
- **per-row tool_metrics columns (original vs delivered chars)** — durable,
  per-call, survives events-cap pruning; additive migration
- **widen the event payload (exact chars, keep events table)** — smaller
  change but still pruned at 5000 rows and not per-call joinable

### Retention shape (FR-004)
- **row-count cap + optional age bound, env-configurable, applied in the
  existing flush prune** — one seam, generous default, visible in health
- **time-bound only (e.g. 90d)** — simpler mental model; spikes can still
  blow the store
- **background vacuum job** — over-engineered for a local store; the flush
  transaction already exists

### Export + theme (FR-005, FR-006)
- **CSV via Python csv module + JSON via stdlib, Content-Disposition
  attachment** — RFC-4180-correct, no deps
- **theme via CSS variables + prefers-color-scheme default + localStorage
  override** — OS-follows-free, manual choice persists per browser

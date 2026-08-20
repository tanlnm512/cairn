# Plan: ui-dashboard-polish

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-20
Source of truth for code state: [survey.md](survey.md). Statuses below cite
survey items (Q#) or greps run in this planning session (file:line).

## Milestones
| Phase | Milestone | Delivers (demoable) | FRs | Depends on |
|-------|-----------|---------------------|-----|------------|
| 1 | Recording-side: warm health, truncation magnitude, retention | First health render after server start is fast; truncation magnitude lands durably per call; usage rows age out under a visible configurable policy applied by the recording pipeline | FR-001, FR-003 (record side), FR-004, FR-007 | — |
| 2 | View-side: tokenizer mode + truncation surfacing | The tokens view uses an exact tokenizer when locally available (labeled) and shows per-tool truncation counts; the heuristic fallback stays labeled | FR-002, FR-003 (view side) | Phase 1 (truncation columns) |
| 3 | Export + dark theme | Filtered history/tokens export as CSV/JSON matching what is shown; light/dark themes with per-browser persistence | FR-005, FR-006 | — |

## Dependencies

- **Phase 1 ∥ Phase 3** — recording-side work (sink, schema, health
  prewarm) is disjoint from view/asset work (export routes, CSS).
- **Phase 2 after Phase 1's truncation columns** — the tokens view reads
  what Phase 1 records; the tokenizer-mode half of Phase 2 could start
  early but rides the same view edits, so one phase keeps it coherent.
- **FR-007 is Phase 1's design constraint**, not a task: aging lives in
  the sink's prune; the dashboard only displays policy (survey supporting
  evidence).

## Parallelization map

**Area A — recording side** (Phase 1: FR-001, FR-003-rec, FR-004, FR-007)
Files: `src/cairn/dashboard/app.py` (startup prewarm hook),
`src/cairn/mcp_server/metric_buffering.py` (record original-vs-delivered
chars — survey Q3's chokepoint), `src/cairn/graph/schema.py` (additive
columns), `src/cairn/telemetry/sink.py` (extend `_prune` — survey Q4).

**Area B — tokens view** (Phase 2: FR-002, FR-003-view)
Files: `src/cairn/dashboard/data.py` (mode-aware estimates, truncation
aggregates), a tokenizer helper module, `src/cairn/dashboard/templates/tokens.html`.

**Area C — export + theme** (Phase 3: FR-005, FR-006)
Files: `src/cairn/dashboard/app.py` (export routes over existing data
functions), `src/cairn/dashboard/templates/history.html`/`tokens.html`
(export buttons), `src/cairn/dashboard/static/app.css` (dark palette —
survey Q6), `base.html` (theme toggle + persistence script).

- Independent: **A ∥ C** — disjoint files (A: sink/schema/metric_buffering;
  C: routes/templates/css), no shared state.
- Strictly ordered: **A's columns → B's truncation surfacing** — B reads
  them; B's tokenizer half is independent but co-located in the view.
- Cross-spec: traffic-scale's filtered views are what export must honor —
  if that spec has landed, export composes its params; if not, export
  composes whatever filters exist (tool/session) and gains windows for
  free later (same param-forwarding pattern).

## Checkpoints

- **After Phase 1** (covers SC-2's mechanics, FR-007): a fresh server's
  first /health responds under 200ms server-side; truncating a tool result
  records the magnitude per call durably; a store over the retention cap
  ages oldest rows out on the next flush, and /health shows the policy.
  Verify: `uv run pytest tests/test_metrics_extensions.py
  tests/test_telemetry.py tests/test_dashboard_app.py -q` + the timing
  test's output.
- **After Phase 2**: with the semantic extra installed, tokens counts use
  the exact tokenizer and the mode label shows it; without it, the
  heuristic label shows; per-tool truncation counts render next to usage.
  Verify: mode-selection unit tests with/without the import + view tests.
- **After Phase 3**: exporting a filtered view yields CSV/JSON whose rows
  match the page exactly; dark choice persists across visits. Verify:
  export-parity tests (filtered row sets byte-compared) + manual theme
  toggle across restarts.

## Risks & mitigations
- Risk: prewarming imports sentence-transformers at server start even for
  users who never open /health → mitigation: prewarm lazily-but-early
  (background thread at startup) or cache probe results; measure boot
  impact in the timing test; keep the import optional either way.
- Risk: exact tokenizer availability differs across installs → mitigation:
  mode detection is import-check + capability probe with the label always
  rendered; heuristic remains the zero-dependency default (spec
  assumption); bench comparability preserved by labeling (survey Q2 note).
- Risk: pre-existing rows have no truncation magnitude → mitigation:
  views render unknown cleanly (spec's own risk); aggregates treat NULL
  as no-evidence, not zero-truncation.
- Risk: retention deletes data a user wanted → mitigation: generous
  default cap, policy + current size visible in health (FR-004), opt-out
  documented beside CAIRN_TELEMETRY.

## Delivery
Branch `feat/ui-dashboard-polish` (or rides the dashboard-v2 train); one
PR, one commit per task. Post-merge: `cairn update` + `record_memory`;
`cairn doctor` (touches the recording/flush path).

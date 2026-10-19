---
type: Tribal-mistake
title: Dashboard nav additions must sweep the palette count pins in test_dashboard_app.py
description: Dashboard nav additions must sweep the palette count pins in test_dashboard_app.py
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-10-05T07:49:23Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.632
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.95
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: archived
tier: asserted
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-10-05T07:49:23Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
memory_triage: session-bookkeeping
valid_from: '2026-10-05T07:49:23Z'
---

Adding a sidebar view (shell.NAV_SECTIONS/NAV_LABELS) breaks two palette count pins in tests/test_dashboard_app.py that no spec survey has enumerated: test_command_palette_seeds_views_and_workspaces (`assert len(by_label) == 15  # 14 nav views + the palette-only graph view` — the 15 grows with every nav view) and test_palette_results_empty_query_serves_the_seed_rows (`for i in range(17)` + `id="palette-row-17" not in` — view rows = nav views + 1 palette-only entry, plus its "the 14 nav views" docstring).

Why: the symbol-communities survey enumerated the nav/icon/_MAIN_VIEWS/ROUTES pin surface (tech-spec D-008) but missed the palette seed counts, so T009's lockstep list omitted them; the two tests fail on the very first nav addition (verified this session: both FAILED after /communities registered).

How to apply: when a dashboard task appends a nav view, move these pins in the same lockstep change set — by_label == (nav views + 1), range(len) grows by one, row-N boundary shifts, docstrings recount; the 5 acceptance files (shell/accessibility/restyle/assets/readonly) stay green either way, so only a full-suite run catches it.
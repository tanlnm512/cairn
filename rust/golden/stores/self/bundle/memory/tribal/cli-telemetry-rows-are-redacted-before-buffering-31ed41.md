---
type: Tribal-decision
title: CLI telemetry rows are redacted before buffering
description: CLI telemetry rows are redacted before buffering
resource: src/cairn/telemetry/cli_metrics.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:23:57Z'
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
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T14:23:57Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:23:57Z'
---

CLI telemetry rows are scrubbed and bounded in build_row before entering any buffer: argv/error text passes strip_private_data, summaries cap at 200 chars, errors at 500, and failed JSON serialization degrades summary and size to NULL. The insert names every column explicitly, CLI rows set source=cli, and session identity prefers TERM_SESSION_ID, then TMUX_PANE, then a fresh non-unknown CLI id.
Why: argv and exception text can echo paths, flags, tokens, and payloads, while additive schema migrations must not silently shift a positional insert.
How to apply: extend build_row and _INSERT_SQL together, scrub and truncate every new payload field before buffering, and keep the session precedence covered by CLI telemetry tests.
---
type: Tribal-pattern
title: MCP staleness banners read pending_sync written only by the live watcher
description: MCP staleness banners read pending_sync written only by the live watcher
resource: src/cairn/mcp_server/_server_core.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:35:24Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.621
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.9
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T15:35:24Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:35:24Z'
---

Result staleness banners query pending_sync with a single indexed SELECT ... WHERE path IN (...), capped at 200 paths, and treat a missing table or empty result as no banner.
Why: pending_sync rows are written by the watcher on debounced file events and deleted by reindex_paths per file, so the banner fires only inside the debounce/reindex window; without watchdog or with CAIRN_WATCH=0 no rows are ever written and the banner stays inert by design.
How to apply: keep the banner best-effort — never fatal, near-zero latency when guarded.
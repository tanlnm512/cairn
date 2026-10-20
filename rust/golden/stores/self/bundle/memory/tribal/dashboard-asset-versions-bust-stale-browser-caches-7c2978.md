---
type: Tribal-pattern
title: Dashboard asset versions bust stale browser caches
description: Dashboard asset versions bust stale browser caches
resource: src/cairn/dashboard/app.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:57:27Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.625
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.92
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T16:57:27Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:57:27Z'
---

Template asset URLs carry a version derived from the newest static-file mtime and static responses use Cache-Control: no-cache.
Why: old heuristic caches and old installations can otherwise keep serving stale dashboard JavaScript or CSS after an upgrade.
How to apply: keep asset_version as the only cache-busting input and route static files through the no-cache handler.
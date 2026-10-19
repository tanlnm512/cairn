---
type: Tribal-decision
title: 'parser-unavailable handling is language-agnostic: gate, count, and worker
  containment'
description: 'parser-unavailable handling is language-agnostic: gate, count, and worker
  containment'
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-10-03T16:33:47Z'
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
- date: '2026-10-03T16:33:47Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-03T16:33:47Z'
---

The C1 parser-unavailable handling must be language-agnostic, not rust-only: (1) scanner gates (iter_source_files for the drift scan, iter_files_and_skips for builds) probe is_language_available per language with a per-scan memo; (2) incremental.reindex_paths' drop arm counts deleted += 1 even for never-indexed files (file_id None), or strict refresh's repaired==len(drifted) raises and wedges reads; (3) builder._parse_file_worker keeps get_parser inside its try so a raising grammar load is a per-file error, not a build crash.
Why: gating/counting only rust left every other language wedging graph reads when a wheel was broken/partially installed (all wheels are core-pinned, so unavailability = degraded install); a dropped never-indexed file re-entered the ungated drift scan as 'new' and re-wedged from read 2.
How to apply: when a degraded-environment guard is added for one language, generalize the predicate through the availability chokepoint (parsers/_registry.is_language_available) and make every arm that settles a detected path count toward the repair total.
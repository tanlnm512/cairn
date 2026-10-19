---
type: Tribal-mistake
title: factory get_parser raises on missing grammar; probe is_language_available first
description: factory get_parser raises on missing grammar; probe is_language_available
  first
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-10-03T16:02:52Z'
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
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-10-03T16:02:52Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-03T16:02:52Z'
---

parsers.factory.get_parser does not return None for a missing grammar wheel: the adapter's __init__ (generic_tree_sitter.py) loads the grammar eagerly via parsers/_registry.get_parser, which raises ImportError/ValueError. So `if not parser` drop arms are dead code for the missing-wheel case.
Why: the C1 audit fix added a drop arm in incremental.reindex_paths, but the factory's exception path bypassed it — every refresh_for_query raised and the ROLLBACK resurrected the stale rows, wedging all graph reads.
How to apply: probe parser availability with parsers/_registry.is_language_available before calling the factory, and route unavailable languages to the drop arm (commit delete + count + clear pending_sync). Drop converges to fresh-build state because the scanner gate re-detects the file once the grammar returns.
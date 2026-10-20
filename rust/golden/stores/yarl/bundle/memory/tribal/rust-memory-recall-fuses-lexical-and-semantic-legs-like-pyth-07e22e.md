---
type: Tribal-decision
title: rust memory recall fuses lexical and semantic legs like python
description: rust memory recall fuses lexical and semantic legs like python
resource: rust/crates/cairn-query/src/tools_km.rs
tags:
- decision
generated:
  by: cairn/0.22.0
  at: '2026-10-19T11:02:15Z'
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
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-19T11:02:15Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-19T11:02:15Z'
---

search_memory must run semantic_memory_search (cosine over memory_embeddings at 0.1 threshold, semantic_env model) and RRF-fuse with the lexical ranking at k=60, upgrading dual hits to a fused provenance stamp; a lexical-only port reorders the top-3 picks and breaks the pack --json golden. Why: python promotion.search_memory always fuses when memory embeddings exist, and pack renders the top 3. How to apply: keep the Rust memory read path aligned with src/cairn/memory/promotion.py leg-for-leg.
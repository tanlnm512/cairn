---
type: Tribal-pattern
title: Memory embed flush retries failed batches and escalates chronic failures
description: Memory embed flush retries failed batches and escalates chronic failures
resource: src/cairn/mcp_server/embed_buffering.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:35:26Z'
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
- date: '2026-10-07T15:35:26Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:35:26Z'
---

A failed embed flush leaves the batch queued for the next 15s attempt; after _WARN_AFTER consecutive failures the logger escalates to WARNING and one durable embed_flush_stalled event fires per streak, both reset on the first success.
Why: failures are expected to be transient (lock contention, model hiccup) and a poison concept_id is already skipped per-cid inside embed_memory_concepts, so dropping the batch would lose environmental work; a chronic failure (broken model) must not retry invisibly at debug level forever.
How to apply: keep the retry-forever semantics and the once-per-streak event; bucket the failure count for telemetry cardinality.
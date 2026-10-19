---
type: Tribal-decision
title: Bench env snapshot/restore lives in cairn/bench/_env.py
description: Bench env snapshot/restore lives in cairn/bench/_env.py
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-10-02T16:42:57Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.275
memory_signals:
  graph_verification: 0.0
  cross_session_refs: 0
  agent_confidence: 0.95
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: archived
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-02T16:42:57Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
memory_refs_baseline: 1.0
memory_stance: preferred
valid_from: '2026-10-02T16:42:57Z'
---

The four bench suites (perf/scaling/agent/swe_bench) and the `cairn bench` CLI share snapshot_env/restore_env from `src/cairn/bench/_env.py`; restore_env also calls embeddings.reset_backend_cache() because the embed backend is resolved once per process and restoring CAIRN_EMBED_BACKEND alone would leave the bench backend active.

Why: Q5 (audit 2026-10-02) — the snapshot/restore block was copy-pasted verbatim in swe_bench_suite and agent_suite with a boolean-tracked variant in perf_suite, and scaling_suite had no restore at all (Q1), the perf restore only covered the success return (Q2), and the CLI leaked its pinned CAIRN_DB (Q3). graph/model_warmup._restore_env was NOT reused: it is module-private, belongs to the graph layer, and does not reset the embed backend cache, so it does not satisfy the bench contract.

How to apply: any new bench suite that pins CAIRN_DB/CAIRN_EMBED_BACKEND/CAIRN_RERANK must snapshot before pinning and restore in a finally that wraps the whole run via _env.restore_env; never re-inline the snapshot/restore block.
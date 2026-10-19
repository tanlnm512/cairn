---
type: Tribal-pattern
title: 'Identity-based drain for maxlen-bounded telemetry buffers: after snapshotting
  a deque for a slow flush (DB write or network export), remove batch entries by identity
  (for row in batch: buf.remove(row), ignoring ValueError) -- a count-based popleft
  drops never-flushed rows enqueued while maxlen evicted batch entries. Enforced in
  telemetry sink, cli_metrics, and otel flushers by tests/test_cli_metrics.py, tests/test_otlp_export.py'
description: 'Identity-based drain for maxlen-bounded telemetry buffers: after snapshotting
  a deque for a slow flush (DB write or network export), remove batch entries by identity
  (for row in batch: buf.remove(row), ignoring ValueError) -- a count-based popleft
  drops never-flushed rows enqueued while maxlen evicted batch entries. Enforced in
  telemetry sink, cli_metrics, and otel flushers by tests/test_cli_metrics.py, tests/test_otlp_export.py'
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-10-16T16:34:35Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.578
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.7
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-16T16:34:35Z'
  action: captured
  score: 0.7
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-16T16:34:35Z'
---

Identity-based drain for maxlen-bounded telemetry buffers: after snapshotting a deque for a slow flush (DB write or network export), remove batch entries by identity (for row in batch: buf.remove(row), ignoring ValueError) -- a count-based popleft drops never-flushed rows enqueued while maxlen evicted batch entries. Enforced in telemetry sink, cli_metrics, and otel flushers by tests/test_cli_metrics.py, tests/test_otlp_export.py
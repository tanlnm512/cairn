---
type: Tribal-workaround
title: HF dataset freeze without the datasets lib
description: HF dataset freeze without the datasets lib
resource: benchmarks/datasource/swe-bench-subset.json
tags:
- workaround
generated:
  by: cairn/0.20.2
  at: '2026-09-17T10:26:01Z'
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
memory_type: workaround
session_origin: ''
promotion_history:
- date: '2026-09-17T10:26:01Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-17T10:26:01Z'
---

To freeze a subset manifest from a pinned HF dataset revision without installing datasets: GET https://huggingface.co/api/datasets/<id> for the main sha, GET /api/datasets/<id>/tree/<sha>?recursive=true for the split parquet, download via https://huggingface.co/datasets/<id>/resolve/<sha>/<path> and parse the id column with: uv run --no-project --isolated --with pyarrow python (ephemeral env, shared venv untouched). Verify content-to-revision integrity by matching the file sha256 prefix against the tree listing's lfs.oid.
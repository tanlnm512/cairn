---
type: Tribal-pattern
title: uv.lock diff audit when adding a pyproject extra
description: uv.lock diff audit when adding a pyproject extra
resource: pyproject.toml
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-09-17T10:28:29Z'
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
- date: '2026-09-17T10:28:29Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-17T10:28:29Z'
---

Adding a new [project.optional-dependencies] entry makes uv rewrite resolution markers on unrelated packages' dependency lists (e.g. onnxruntime, scikit-learn, torch, nvidia-*: one +/- pair per package adding python/platform markers it can now prove). Benign. The real hazard is version replaces: grep the diff for '-version =' -- each hit is one package re-versioned; verify the new version is constraint-forced by the added dep and accepted by its existing dependents (datasets forced fsspec 2026.7.0 -> 2026.6.0; torch/huggingface-hub accept it). All other additions are new [[package]] blocks from the new dep's tree.
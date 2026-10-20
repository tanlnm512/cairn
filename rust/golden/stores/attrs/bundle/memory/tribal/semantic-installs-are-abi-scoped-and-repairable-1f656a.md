---
type: Tribal-decision
title: Semantic installs are ABI-scoped and repairable
description: Semantic installs are ABI-scoped and repairable
resource: src/cairn/graph/embeddings.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:52:40Z'
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
- date: '2026-10-07T14:52:40Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:52:40Z'
---

Sentence-transformers installs target the ABI-specific shared library and running interpreter, child PYTHONPATH mirrors in-process shared-library order, verification imports in a subprocess, and a failed verification wipes and reinstalls the directory once.
Why: Why: pip target installs skip seemingly satisfied broken packages, and uv can otherwise choose a different interpreter ABI.
How to apply: How to apply: never patch a failed shared-library install in place and never resolve child imports from an unrelated environment.
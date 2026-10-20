---
type: Tribal-workaround
title: Spec pass conditions pin store env and venv binary under bash -c
description: Spec pass conditions pin store env and venv binary under bash -c
resource: test.md
tags:
- workaround
generated:
  by: cairn/0.20.2
  at: '2026-09-17T05:36:46Z'
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
memory_type: workaround
session_origin: ''
promotion_history:
- date: '2026-09-17T05:36:46Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-17T05:36:46Z'
---

Auto pass conditions in specs/*/test.md that build a fixture via prepare.sh must wrap the chain in PATH-prefixed bash -c, export CAIRN_BIN (repo venv binary), CAIRN_HOME, CAIRN_DB, CAIRN_KNOWLEDGE, CAIRN_WORKSPACE, CAIRN_EMBED_BACKEND=hash, and cd into the fixture ws; the audit proofs runner executes the first backticked span with sh from the repo root, so a leading export or bare cairn breaks the auto classification or targets the stale pipx binary.
---
type: Tribal-decision
title: Vendored SCIP gencode carries its protobuf floor
description: Vendored SCIP gencode carries its protobuf floor
resource: src/cairn/parsers/_scip_pb2.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:15:07Z'
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
- date: '2026-10-07T17:15:07Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:15:07Z'
---

The checked-in SCIP protobuf stub records its upstream provenance and requires the protobuf runtime validated by its generated version check.

Why: Sourcegraph ships no Python SCIP binding, so the stub avoids a runtime protoc dependency while preserving reproducible gencode provenance.
How to apply: regenerate the stub with scripts/regen_scip_pb2.sh and raise the pyproject protobuf floor together whenever the generated runtime requirement rises.
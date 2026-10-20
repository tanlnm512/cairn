---
type: Tribal-decision
title: CLI golden gate pins tokenizer probe and log level for pack parity
description: CLI golden gate pins tokenizer probe and log level for pack parity
resource: scripts/rust_migration/cli_golden.py
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-10-19T06:19:30Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.61
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.85
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-19T06:19:30Z'
  action: captured
  score: 0.85
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
---

scripts/rust_migration/cli_golden.py cli_env pins CAIRN_EMBED_LOCAL_MODEL to a never-cached name and CAIRN_LOG_LEVEL=ERROR for the differential python-vs-rust runs. Why: python pack resolves the exact HF tokenizer when locally cached while the Rust CLI is heuristic-only (migration plan section 9), and python logging noise (rich ann-index warning with millisecond timestamps) is not part of the C-4 stdout purity / exit-code contract. How to apply: when a differential run fails only on token costs or logging noise, extend these pins instead of porting the tokenizer or the rich handler; exact-mode parity would need a D-### tokenizers-crate decision first.
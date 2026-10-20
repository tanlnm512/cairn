---
type: Tribal-decision
title: 2a parser ports validate against tests/fixtures/golden expected.json via normalise()
description: 2a parser ports validate against tests/fixtures/golden expected.json
  via normalise()
resource: rust/crates/cairn-parse/tests/parser_golden.rs
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-10-19T00:11:06Z'
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
- date: '2026-10-19T00:11:06Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
---

Rust cairn-parse per-language ports (2a workstreams) are validated by parsing tests/fixtures/golden/<lang>/sample.<ext> and comparing a Rust re-implementation of tests/fixtures/golden/regenerate.py::normalise against the checked-in expected.json (order-insensitive, same sort keys). parser_script.rs (python, php, ruby, dart) landed this harness in rust/crates/cairn-parse/tests/parser_golden.rs. Why: plan 2a pins each language port to its existing golden fixtures, which are generated from the Python parsers, so they arbitrate parity without porting the pytest suite. How to apply: remaining family files (jvm, apple, web, sys) should add a <family>_golden.rs test target reusing the same normalise() port and differential-check tricky paths against the .venv Python parsers when the golden sample does not exercise them.
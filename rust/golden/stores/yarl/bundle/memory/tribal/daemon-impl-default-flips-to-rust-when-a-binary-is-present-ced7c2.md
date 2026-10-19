---
type: Tribal-decision
title: Daemon impl default flips to rust when a binary is present
description: Daemon impl default flips to rust when a binary is present
resource: src/cairn/mcp_server/lifecycle.py
tags:
- decision
generated:
  by: cairn/0.22.0
  at: '2026-10-19T09:53:17Z'
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
- date: '2026-10-19T09:53:17Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-19T09:53:17Z'
---

CAIRN_DAEMON_IMPL when set still wins (rust; anything else is python). With the env unset, daemon_impl() defaults to rust when rust_bin() finds a native cairn binary (CAIRN_RUST_BIN, then PATH/~/.local/bin filtered by a not-a-#!-script check on the Python side; CAIRN_RUST_BIN/current_exe on the Rust side), else python.
Why: the flip makes the Rust binary the preferred cairn while the deprecated Python package keeps working everywhere; a #! check is what keeps the pipx/uv Python console script (also named cairn) from being mistaken for the binary.
How to apply: explicit --impl rust with no binary still errors loudly; the default path never errors. Owner tests: tests/test_connect.py::test_daemon_impl_selects_start_binary and auth_lifecycle.rs::daemon_impl_selection_matrix.
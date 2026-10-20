---
type: Tribal-pattern
title: Concurrent CLI agent tests run subprocesses, not CliRunner threads
description: Concurrent CLI agent tests run subprocesses, not CliRunner threads
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-09-17T01:10:49Z'
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
- date: '2026-09-17T01:10:49Z'
  action: captured
  score: 0.7
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-17T01:10:49Z'
---

click.testing.CliRunner.invoke swaps the process-global sys.stdout/stderr inside click's isolation contextmanager; concurrent invokes from multiple threads restore stale streams and flake. Simulate concurrent agents with real processes: subprocess.Popen([sys.executable, '-c', 'from cairn.cli import main; main()', <argv>]) per agent, then assert on store state. Precedent: tests/test_memory_overlap_check.py::test_concurrent_share_and_check_writers_stay_lossless.
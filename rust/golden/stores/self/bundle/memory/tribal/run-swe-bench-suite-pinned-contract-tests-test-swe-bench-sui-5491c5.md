---
type: Tribal-decision
title: run_swe_bench_suite pinned contract (tests/test_swe_bench_suite.py)
description: run_swe_bench_suite pinned contract (tests/test_swe_bench_suite.py)
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-09-17T10:50:13Z'
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
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-09-17T10:50:13Z'
  action: captured
  score: 0.7
  tier: tribal
memory_is_latest: true
memory_supersedes:
- memory/tribal/run-swe-bench-suite-pinned-contract-tests-test-swe-bench-sui-a06c6f
memory_superseded_by: null
valid_from: '2026-09-17T10:50:13Z'
---

Contract pinned by tests/test_swe_bench_suite.py for src/cairn/bench/swe_bench_suite.py: module-level run_swe_bench_suite(tasks, workspaces, db_path, *, runs=3) -> payload dict. tasks = iterable of the loader's five-field dicts (injected; no fetch branch, no datasets import). workspaces = mapping instance_id -> repo tree both arms probe. Returns dict with: tasks list in input order (rows: instance_id, cairn/control arms with exactly {tool_calls, chars, est_tokens, wall_ms}, reduction with {calls_pct, tokens_pct, time_ratio}), medians {cairn, control} (same arm shape), runs, chars_per_token == CHARS_PER_TOKEN. Both arms non-zero effort per task; two full runs give identical calls/est_tokens (fresh builds); duplicate instance ids and unmapped workspaces raise ValueError; pinned env vars (CAIRN_DB/CAIRN_EMBED_BACKEND/CAIRN_RERANK) restored after the run. Why: T005 is the red-by-design contract T006 greens; the tests are the interface spec. How to apply: implement swe_bench_suite.py to exactly this signature and payload; reuse _CairnArm/_ControlAgent/ArmEffort shapes from agent_suite.py; keep the report at its own payload level.
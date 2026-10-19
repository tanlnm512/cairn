---
type: Tribal-pattern
title: Hermetic git-clone fixture via GIT_CONFIG_GLOBAL insteadOf rewrite
description: Hermetic git-clone fixture via GIT_CONFIG_GLOBAL insteadOf rewrite
resource: tests/test_swe_bench_cli.py
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-09-17T11:45:21Z'
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
- date: '2026-09-17T11:45:21Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes:
- memory/tribal/hermetic-git-clone-fixture-via-git-config-global-insteadof-r-fa30ee
memory_superseded_by: null
valid_from: '2026-09-17T11:45:21Z'
---

Test code that must exercise a real 'git clone' of a GitHub URL without network: build a local bare repo, set uploadpack.allowFilter=true on it, point GIT_CONFIG_GLOBAL at a config file with 'url "file://<bare-without-.git>" / insteadOf = https://github.com/<owner>/<name>', then clone the normal https URL -- git rewrites it to the file:// path and the suffix (e.g. .git) is appended to the rewrite base, so the bare repo must live exactly at <rewrite-base>.git. Works with --filter=blob:none and sha checkout. Pair with CAIRN_HOME redirected into tmp_path so cache writes stay hermetic (C-04).
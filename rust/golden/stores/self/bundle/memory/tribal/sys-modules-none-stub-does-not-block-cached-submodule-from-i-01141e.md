---
type: Tribal-mistake
title: sys-modules-none-stub-does-not-block-cached-submodule-from-imports
description: sys-modules-none-stub-does-not-block-cached-submodule-from-imports
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-10-15T15:46:11Z'
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
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-10-15T15:46:11Z'
  action: captured
  score: 0.7
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-15T15:46:11Z'
---

Setting sys.modules['pkg']=None to simulate a missing optional dependency does not fail 'from pkg.sub import name' when pkg.sub is already cached in sys.modules: the from-import short-circuits on the cached full name and never imports the halted parent. Stub every cached name in the chain (pkg, pkg.algorithms, pkg.algorithms.community) or the probe passes while 'import pkg' inside the function fails, as in the scaling-suite communities test.
---
type: Tribal-pattern
title: swe-bench loader offline seam
description: swe-bench loader offline seam
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-09-17T10:36:45Z'
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
- date: '2026-09-17T10:36:45Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
stale: true
valid_from: '2026-09-17T10:36:45Z'
valid_until: '2026-09-18T16:34:19Z'
memory_refs_baseline: 0.222
---

`cairn.bench.swe_bench.load_tasks(manifest, *, fetch=None)` is the offline seam: the manifest is validated before any fetch, the default fetch is the lazy `datasets` import (the only network touchpoint), and injecting `fetch` keeps task assembly a pure function of (manifest, fetched rows) — identical across reruns. Gold-patch fields (`patch`, `test_patch`, `FAIL_TO_PASS`, `PASS_TO_PASS`) never cross the five-field `TASK_FIELDS` projection. How to apply: suite runners and CI smokes inject `fetch` over fixture rows; only the real reproduction path uses the default Hub fetch at the manifest's `dataset_revision_sha`.
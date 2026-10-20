---
type: Tribal-pattern
title: Silence ruff on golden fixture corpora with a scoped per-dir .ruff.toml
description: Silence ruff on golden fixture corpora with a scoped per-dir .ruff.toml
resource: rust/golden/flow/.ruff.toml
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-10-19T14:38:12Z'
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
- date: '2026-10-19T14:38:12Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
---

A directory-scoped .ruff.toml (e.g. rust/golden/flow/.ruff.toml) with per-file-ignores = { "**/corpus/**" = ["ALL"] } silences lint on fixture corpora without editing shared pre-commit config or fixture bytes. extend-exclude alone is insufficient: pre-commit passes staged files explicitly, and exclusion resolves at traversal time from the invocation-level config (repo-root pyproject), not from the per-file config governing lint settings, so explicit paths get linted anyway. The scoped config shadows the root select for its subtree (ruff does not merge configs); re-declare select = ["F"] to keep the root gate, and note ruff defaults are broader than the root F-only selection (PLR0402 fires under --isolated). Why: golden fixture source is deliberately non-idiomatic (duplicate defs create the ambiguous-dispatch capture); editing fixture code to satisfy lint drifts recorded fixtures, including ones owned by other agents. How to apply: when the pre-commit ruff gate fails on a fixture corpus, add the scoped .ruff.toml next to the fixtures instead of mutating fixtures or shared lint config; verify with the exact hook invocation (ruff check <explicit staged paths>).
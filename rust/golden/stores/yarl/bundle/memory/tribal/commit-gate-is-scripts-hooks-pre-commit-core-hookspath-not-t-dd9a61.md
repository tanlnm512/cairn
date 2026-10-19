---
type: Tribal-workaround
title: Commit gate is scripts/hooks/pre-commit (core.hooksPath), not the pre-commit
  framework; staged-files ruff needs --force-exclude for excluded data trees
description: Commit gate is scripts/hooks/pre-commit (core.hooksPath), not the pre-commit
  framework; staged-files ruff needs --force-exclude for excluded data trees
tags:
- workaround
generated:
  by: cairn/0.20.2
  at: '2026-09-20T19:54:36Z'
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
memory_type: workaround
session_origin: ''
promotion_history:
- date: '2026-09-20T19:54:36Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-20T19:54:36Z'
---

Local commits are NOT gated by .pre-commit-config.yaml (the framework): core.hooksPath points at scripts/hooks/pre-commit, a custom bash script running `ruff check --output-format=concise <staged .py files>`. Explicit paths bypass pyproject's extend-exclude, so staged test-data trees (tests/fixtures) lint-fail at commit while `uv run --extra dev pre-commit run --all-files` stays green — two different gates.

How to apply: when a commit reds on files CI's bare `ruff check src scripts tests` passes, check core.hooksPath first; the fix is --force-exclude on the staged-files invocation (landed in fbcbed9 for tests/fixtures/scip-indexing). Same trap class: committing fixture workspaces whose .git dirs are provision-time artifacts — git records gitlinks; add sources with nested .git temporarily mv'd aside (provision.sh recreates them idempotently).
---
type: Tribal-decision
title: Shared share-recall merge lives in memory.store.shared_recall_entries
description: Shared share-recall merge lives in memory.store.shared_recall_entries
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-09-17T01:08:02Z'
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
- date: '2026-09-17T01:08:02Z'
  action: captured
  score: 0.7
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-17T01:08:02Z'
---

The agent read-through merge for kind='share' rows exists once, in cairn.memory.store.shared_recall_entries beside share_memory/check_overlap; cairn.mcp_server.tools_memory.recall_memory and cairn.cli.memory.memory_search both consume it and carry no merge logic of their own.

Why: the merge logic was duplicated line-faithfully as _shared_recall_entries (MCP) and _shared_search_entries (CLI); the reuse rule requires one shared helper per duplicated behavior.

How to apply: MCP's include_superseded knob is the helper's documented parameter (default False = CLI contract: drop non-latest versions); never re-fork a per-surface copy. The helper raises ValueError on a malformed as_of, unreachable through both callers because search_memory pre-validates as_of -- keep that ordering when adding new callers.
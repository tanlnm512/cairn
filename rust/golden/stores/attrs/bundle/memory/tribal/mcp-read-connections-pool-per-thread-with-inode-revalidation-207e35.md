---
type: Tribal-pattern
title: MCP read connections pool per thread with inode revalidation
description: MCP read connections pool per thread with inode revalidation
resource: src/cairn/mcp_server/_server_core.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:35:22Z'
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
- date: '2026-10-07T15:35:22Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:35:22Z'
---

_conn() caches one connection per (thread, db path) keyed by (st_dev, st_ino); the returned wrapper close() is a no-op release; CAIRN_CONN_POOL=0 restores unpooled behavior.
Why: full builds swap the DB file with os.replace, so a pooled connection can point at a dead inode; one entry per thread means a server repointed at another workspace drops the old store connection instead of holding it open.
How to apply: revalidate (st_dev, st_ino) before reusing a pooled connection and drop-plus-reopen on mismatch.
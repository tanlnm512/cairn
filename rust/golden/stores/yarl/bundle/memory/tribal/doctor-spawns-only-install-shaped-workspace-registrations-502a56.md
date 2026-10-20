---
type: Tribal-decision
title: Doctor spawns only install-shaped workspace registrations
description: Doctor spawns only install-shaped workspace registrations
resource: src/cairn/cli/system/doctor.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:29:03Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.625
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.92
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T16:29:03Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:29:03Z'
---

Workspace-owned MCP registration files are repo content whose author is untrusted in a cloned checkout, and doctor has no user-approval step, so the spawn probe runs only when an entry is exactly the shape `cairn install-agents` writes: the PATH-resolved cairn binary with args ["serve"] and an env block limited to CAIRN_HOME; anything else from such a file degrades to a WARN naming the file.
Why: the probe executes the entry argv verbatim, and the `python -m` fallback would put the untrusted cwd on sys.path.
How to apply: never widen the spawnable shape beyond the installer-written form; treat other workspace-owned entries as findings, not commands.
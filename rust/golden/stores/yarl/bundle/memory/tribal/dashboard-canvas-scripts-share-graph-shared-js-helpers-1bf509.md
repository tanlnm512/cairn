---
type: Tribal-decision
title: Dashboard canvas scripts share graph-shared.js helpers
description: Dashboard canvas scripts share graph-shared.js helpers
resource: src/cairn/dashboard/static/graph-shared.js
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-10-02T17:12:32Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.61
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.85
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-02T17:12:32Z'
  action: captured
  score: 0.85
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-02T17:12:32Z'
---

The three vis-network canvas scripts (app.js, db-graph.js, knowledge-graph.js) consume window.CairnGraphShared from static/graph-shared.js: cssVar, prefersReducedMotion, makeSpiralPosition (factory — one spiral cursor per view script), interactionOptions, physicsOptions(barnesHut, iterations), wireOverlayControls(canvas, network, actionAttr, zoomStep, animate), el, wireInspectPanel(panel, pathPrefix, buildUrl).

Why: the audit found ~100 duplicated lines in knowledge-graph.js already drifted from app.js/db-graph.js (C65). The shared file carries only view-agnostic machinery; per-view tuning (KIND_TOKENS, themeOptions, physics constants, zoom step/action attribute) stays in each view script, and physicsOptions parameterizes the barnesHut block so the schema graph keeps its lighter tuning.

How to apply: any new canvas view includes graph-shared.js before its view script (templates: graph.html, database.html, knowledge_graph.html) and must not re-inline cssVar/spiral/overlay/inspect machinery. View scripts guard on `typeof window.CairnGraphShared === "undefined"` so pages that load app.js without the shared file (traffic views) still bail out safely. drift points resolved toward the shared version: db-graph zoom-out gained the shared 0.05 scale floor, and its physics block spells out vis defaults (enabled/fit true) without behavior change.
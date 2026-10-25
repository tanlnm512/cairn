---
type: Task
title: 'doc-link: cairn'
resource: cairn
tags:
- doc-link
generated:
  by: cairn/0.23.0
  at: '2026-01-01T00:00:00Z'
status: pending
okf_version: '0.2'
task_kind: doc-link
assigned_to: ''
attempt: 1
result_path: ''
completed_at: ''
claimed_at: ''
facts:
  members:
  - knowledge/a
  - knowledge/b
---

# Task: doc-link

**Resource:** `cairn`  
**Status:** pending  
**Attempt:** 1/4

## Facts (graph-grounded — do not invent beyond these)
**members:** ['knowledge/a', 'knowledge/b']

## Output spec
Propose relationships between the isolated documents in facts.members. Read each member document first. Output ONLY proposed-edge lines, one per line, in this exact format:
<concept_id> <relation> <related_id>
where <relation> is one of: relates-to | supersedes | superseded-by | references. <concept_id> and <related_id> must be existing knowledge document ids (facts.members lists them). No prose, no code fences, no extra formatting.

Write your result, then run: `cairn task complete golden-drop`

---
type: Task
title: 'compass-synthesize: src/cairn/llm'
resource: src/cairn/llm
tags:
- compass-synthesize
generated:
  by: cairn/0.23.0
  at: '2026-01-01T00:00:00Z'
status: done
okf_version: '0.2'
task_kind: compass-synthesize
assigned_to: ''
attempt: 1
result_path: ''
completed_at: ''
claimed_at: ''
facts:
  key_files:
  - src/graph/queries.py
---

# Task: compass-synthesize

**Resource:** `src/cairn/llm`  
**Status:** done  
**Attempt:** 1/4

## Facts (graph-grounded — do not invent beyond these)
**key_files:** ['src/graph/queries.py']

## Output spec
Write a 25-35 line compass file with exactly these 5 sections:
# What Does This Module Do? / # Common Modification Patterns / # Build-Failure Patterns / # Cross-Module Dependencies / # Tribal Knowledge
When facts.subsystems is non-empty, also emit `# Subsystems` right after the first section: one line per community (label, size, hub symbol).
Only reference files/symbols listed in the facts. Use backticks for code.

Write your result, then run: `cairn task complete golden-done`

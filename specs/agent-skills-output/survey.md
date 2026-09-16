# Survey: agent-skills-output

**Created**: 2026-09-16 | **Baseline**: main @ 137fa5f (refreshed from 1af3b35 — delta was specs/-only, no code citations affected)
The survey node's output — the single source of truth for code state. Every citation
in the other four docs must trace to a line here. Evidence is pasted
verbatim from grep/read output in the session that wrote it.

## Items

```
item S1: "A static skill package ships today and is installed per client"
  evidence:   src/cairn/agent_integration/skill/SKILL.md exists (frontmatter name/description + references/tools.md, references/golden-rules.md, scripts/impact_guard.py, evals/); src/cairn/agent_install/merge.py:97 `skill packages (SKILL.md + references/ + scripts/ + evals/)`; src/cairn/agent_install/detect.py:165 `(.claude / "skills" / "cairn" / "SKILL.md").exists()`
  status:     DONE
  verify:     find src/cairn/agent_integration/skill -type f | head
  gap:        skill body is hand-authored and static; no generation from a selector

item S2: "Per-client skill distribution paths (incl. fallbacks) already mapped"
  evidence:   src/cairn/agent_install/__init__.py:153 `Skill = full skill package (SKILL.md + references/ + scripts/ + evals/)`; :156-171 per-client matrix (claude `.claude/skills/`, droid `.factory/skills/`, zcode `.zcode/skills/`, cursor/opencode skill-via-fallback)
  status:     DONE
  verify:     sed -n '150,175p' src/cairn/agent_install/__init__.py
  gap:        nothing writes generated skills into these paths

item S3: "Every content input has an existing reader"
  evidence:   src/cairn/mcp_server/tools_compass.py:34 `def get_compass(module: str) -> str:`; src/cairn/graph/repo_map.py:21-22 `_SymbolRow(... incoming: int, outgoing: int)` (per-symbol in/out degree); src/cairn/memory/promotion.py:242 `def search_memory(`
  status:     DONE
  verify:     grep -n "def get_compass" src/cairn/mcp_server/tools_compass.py; grep -n "incoming\|outgoing" src/cairn/graph/repo_map.py | head -3
  gap:        no assembly step (selector → ranked symbols → compass excerpt → memory → SKILL.md)

item S4: "Deterministic critic exists for graph-verifying references"
  evidence:   src/cairn/compass/critic.py (validate_paths is consumed by cli/validate.py:35); compass/wiki promotion is critic-gated through the task queue
  status:     DONE
  verify:     grep -n "def " src/cairn/compass/critic.py | head -5
  gap:        critic is not wired to any skill emitter (new output surface)
```

## Supporting evidence
`cairn skill generate` has no existing command surface (src/cairn/cli/ has no skill
module); nearest precedents for generated markdown output are wiki pages
(src/cairn/wiki/) and compass generation (src/cairn/compass/generator.py).

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.

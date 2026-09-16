# Survey: swe-bench-integration

**Created**: 2026-09-16 | **Baseline**: codex/docs-level-up-specs @ 1af3b35
The survey node's output — the single source of truth for code state. Every citation
in the other four docs must trace to a line here. Evidence is pasted
verbatim from grep/read output in the session that wrote it.

## Items

```
item S1: "Two-arm deterministic agent bench already exists"
  evidence:   src/cairn/bench/agent_suite.py:94 `class _CairnArm:` / :111 `class _ControlAgent:` (grep+read control); :3-5 `"cairn's query tools vs a plain grep+read loop?" ... this suite measures *agent effort*`; :58 `CHARS_PER_TOKEN = 4`
  status:     DONE
  verify:     grep -n "class _CairnArm\|class _ControlAgent" src/cairn/bench/agent_suite.py
  gap:        corpus is cairn's own; no SWE-bench task loader, no resolve-rate arm

item S2: "CI-rot-prevention pattern is proven by the self-demo test"
  evidence:   tests/test_self_demo.py:1 `"""The 'cairn on cairn' self-demo (Phase 2.3) — cairn indexes itself, verbatim.`; :57 `def test_self_demo_build_and_query(built_db):`; :97 `def test_self_demo_resolution_invariant_holds(built_db):`
  status:     DONE
  verify:     head -60 tests/test_self_demo.py
  gap:        no swe-bench smoke job

item S3: "Bench corpus is datasource-driven (loader seam exists)"
  evidence:   src/cairn/bench/ has corpus.py + datasource.py (pinned-dataset stamping: datasource.py:410 `the stamp answers "which pinned dataset does this artifact belong to"`)
  status:     DONE
  verify:     ls src/cairn/bench/
  gap:        no SWE-bench datasource; no `swebench` dev dependency in pyproject.toml
```

## Supporting evidence
`grep -rn "swe" src/cairn/bench/` matches nothing task-related — the arm is fully
new work on top of the existing harness shape. Bench CLI lives at src/cairn/cli/bench.py
(`cairn bench --suite agent|perf|scaling` selection pattern to extend).

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.

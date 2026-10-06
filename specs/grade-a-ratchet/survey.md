# Survey: grade-a-ratchet

**Created**: 2026-10-05 | **Baseline**: HEAD @ 2e7a7e7725e00f382e5192082de040f496ee0dd5
Evidence from this session's grep/read output only.

## Items

```
item FR-001: "git mv architecture.html → docs/diagrams/architecture.html and audit-findings-2026-10-02.md → docs/audits/2026-10-02.md with inbound links updated"
  evidence:   `git ls-files` (session): `architecture.html`, `audit-findings-2026-10-02.md` are tracked at repo root. `ls docs/diagrams` (session) contains `cairn-architecture.html` and other diagram HTML but no `architecture.html`; `ls docs` (session) has no `audits/` entry. `git grep -n 'architecture\.html' -- ':!CHANGELOG.md' ':!docs/diagrams/*' ':!audit-findings-2026-10-02.md'` → only `docs/README.md:29` and `docs/architecture.md:13`, both pointing at `diagrams/system-architecture.html` (different file). `git grep -n 'audit-findings-2026-10-02' -- ':!specs'` → no output. CHANGELOG.md:2550 `architecture.html` "26 tools" → "27 tools"` and CHANGELOG.md:2726 `- `docs/architecture-overview.md` + `architecture.html` — big-picture system` are historical changelog mentions, not live links.
  status:     TODO
  verify:     git ls-files | grep -E '^(architecture\.html|audit-findings[^/]*)$' && ls docs/audits
  gap:        both files still at root; docs/diagrams/architecture.html and docs/audits/2026-10-02.md do not exist; no live (non-historical) inbound links were found to update beyond CHANGELOG history mentions.

item FR-002: "make audit-status reading docs/audits/*.md + <audit>.status.json sidecars, remaining-vs-total per P0–P3, --json, clean failure naming offending file"
  evidence:   Makefile grep of target lines (session): only `dist:`, `evals:`, `ci-local:`, `ci-local-all:`, `verify-no-code-change:`, `release:`, `help:` (Makefile:8,18,30,35,45,51,81). `make audit-status` (session): `make: *** No rule to make target `audit-status'.  Stop.` (exit 2). `find . -name '*.status.json' -not -path './.git/*'` → no output. `ls scripts` (session) has no audit-status script; `ls scripts/audit_status.py` → `No such file or directory`. Source audit exists at root: audit-findings-2026-10-02.md:3 `141 discrete defects — 2 high (P0), 51 medium (P1), 88 low (P2) — plus 3 systemic clusters (P3)`; priority sections at audit-findings-2026-10-02.md:25 `### P0 — High (2)`, :32 `### P1 — Medium (51)`, :88 `### P2 — Low (88)`, :181 `### P3 — Systemic clusters (3)`; finding tables use `| ID | Location | Issue |` (audit-findings-2026-10-02.md:27) with IDs like `C77`, `Q41`, `S1`, `SY1`. No fixed-status marker exists: `grep -n 'FIXED\|Fixed\|fixed' audit-findings-2026-10-02.md` matches only unrelated prose (e.g. :49 `fromisoformat`, :213 `cheapest way to keep them fixed`).
  status:     TODO
  verify:     make audit-status
  gap:        no Makefile target, no script, no docs/audits/ directory, no sidecar schema or sidecars.

item FR-003: "comment/docstring length checker (script + pre-commit hook + CI job) with shrink-only baseline allowlist, src/ scope, remaining-count output"
  evidence:   `.pre-commit-config.yaml` hook inventory (session, grep `id:`): `ruff` (:24), `gitleaks` (:39), `check-yaml` (:46), `check-toml` (:47), `check-merge-conflict` (:48), `check-added-large-files` (:49), `debug-statements` (:54) — no comment/docstring-length hook. `.github/workflows/ci.yml` job grep (session): `security`, `typecheck`, `pr-title`, `pre-commit`, `dependency-review`, `test`, `closure-gate`, DS-v2 seal (`285`), `build`, `container`, `bench` — no checker job. `ls scripts` (session) lists 14 entries (check_doc_links.py, ci-local.sh, fetch_t3_corpus.py, hooks/, install-dev-hooks.sh, install-hooks.sh, install.sh, measure_memory_health.py, measure_warm_time.py, mint_baselines.py, regen_scip_pb2.sh, run_skill_evals.py, uninstall.sh, verify_datasource.py, verify_ground_truth.py, verify_no_code_change.py) — `ls scripts/check_comment_lengths.py` → `No such file or directory`. `git grep -n 'comment_length\|docstring.*checker\|check_comments\|comment_policy' -- ':!specs'` → no output. Adjacent-but-different tooling exists: Makefile:40 `# Verify a "comments/docstrings-only" change didn't alter executable code.` (scripts/verify_no_code_change.py).
  status:     TODO
  verify:     ls scripts/check_comment_lengths.py && grep -n 'comment' .pre-commit-config.yaml .github/workflows/ci.yml
  gap:        no checker script, no baseline allowlist, no pre-commit hook, no CI job.

item FR-004: "--json machine-readable outputs from the checker and audit-status consumable by CI and cairn system report"
  evidence:   Neither producer exists (see FR-002/FR-003 evidence). Consumer side partially exists: src/cairn/cli/system/report.py:287 `def report(db, as_json, out_path):` with `@click.option("--json", "as_json", is_flag=True, help="Emit the bundle as JSON.")` and `_build_report` (src/cairn/cli/system/report.py:200) returning `{generated_at, versions, doctor, recent_errors, config}` — no audit/comment-violation fields. `git grep -n 'audit-status\|comment_length'` → no output.
  status:     TODO
  verify:     rg -n 'audit|comment_violation' src/cairn/cli/system/report.py
  gap:        no --json emitters for the two new tools and no report/CI consumption of their counts.

item NFR-001: "Security — n/a"
  evidence:   Scope is file moves, local scripts, hook/CI wiring over repo-owned markdown/JSON; no secrets/auth/untrusted-input surface found in the proposed artifact paths (root files + scripts/ + Makefile + .pre-commit-config.yaml + ci.yml, all session-read).
  status:     DONE
  verify:     git grep -n 'secret\|password\|token' Makefile scripts .pre-commit-config.yaml .github/workflows/ci.yml | head
  gap:        none

item NFR-002: "Privacy — n/a"
  evidence:   No data leaves the repo: scripts operate on tracked files only; existing precedent scripts are local (scripts/verify_no_code_change.py, Makefile:45).
  status:     DONE
  verify:     grep -rn 'http\|upload' Makefile scripts/check_doc_links.py | head
  gap:        none

item NFR-003: "checker and audit-status complete in under 5 s"
  evidence:   Neither tool exists yet (FR-002/FR-003); no timing to measure. Repo scale reference from session: `src/cairn/` Python files and 304-entry tests/ directory exist.
  status:     TODO
  verify:     /usr/bin/time make audit-status
  gap:        tools do not exist; performance unmeasurable.

item NFR-004: "deterministic checker; idempotent baseline regeneration"
  evidence:   No checker or baseline exists (FR-003 evidence); determinism/idempotence cannot be observed.
  status:     TODO
  verify:     make audit-status && make audit-status
  gap:        tools do not exist.

item NFR-005: "observability via --json per FR-004"
  evidence:   No --json outputs for these tools exist; the only related --json is `cairn system report` (src/cairn/cli/system/report.py:287 `def report(db, as_json, out_path):`), which is a different bundle.
  status:     TODO
  verify:     make audit-status --json
  gap:        no emitters.

item NFR-006: "Accessibility — n/a"
  evidence:   No UI surface in scope; artifacts are CLI scripts, Make targets, hooks, CI YAML (session-read paths).
  status:     DONE
  verify:     grep -rn '<html\|<div' scripts Makefile | head
  gap:        none
```

## Supporting evidence

- Makefile target inventory (session grep `^[a-z][a-z-]*:`): Makefile:8 `dist:`, :18 `evals:`, :30 `ci-local:`, :35 `ci-local-all:`, :45 `verify-no-code-change:`, :51 `release:`, :81 `help:` — no audit/comment targets.
- Root artifacts tracked (session `git ls-files | grep ...`): `architecture.html`, `audit-findings-2026-10-02.md`. Root also carries non-standard working-tree clutter not addressed by this spec: `.DS_Store`, `.coverage`, `coverage.xml`, `test-results.xml` (session `ls -la`).
- Audit totals (re-counted from headings): P0=2 (audit-findings-2026-10-02.md:25), P1=51 (:32), P2=88 (:88), P3=3 (:181); validation roll repeats P0 2/2 (:281), P1 49+2 partial (:288), P2 86+2 partial (:344), P3 3 (:436).
- Finding ID namespaces present in the file: `C*`, `Q*`, `S*`, `SY*` (session `grep -oE '\*\*[A-Z]+[0-9]+\*\*' | sort -u`).
- Existing JSON-mode precedent for diagnostics: tests assert CLI `--json` output (specs/context/tech.md "JSON-mode assertions against CLI `--json` output (test_doctor.py:96-124)") and `report` exposes `--json` (src/cairn/cli/system/report.py:287).
- Baseline commit: `git rev-parse HEAD` → `2e7a7e7725e00f382e5192082de040f496ee0dd5`. Working tree at survey time: `M specs/INDEX.md`, `?? specs/grade-a-guardrails/`, `?? specs/grade-a-ratchet/` (spec docs only; no source changes).

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
- Status derives from evidence, not intent. Verify commands were run this session (make/ls/grep/find/git grep as cited).
- Numbers re-counted this session from the cited files.

# Plan: comment-sweep

**Spec**: [spec.md](spec.md) | **Created**: 2026-10-07

## Milestones
<!-- Each milestone = a phase in task.md. -->
| Phase | Milestone | Delivers (demoable) | Requirements | Depends on |
|-------|-----------|---------------------|--------------|------------|
| 1 | Quiet gate spine | `make comment-style` / `make audit-status` stderr carry no runpy RuntimeWarning; migration ledger scaffolded under `docs/audits/` | FR-004 | — |
| 2 | Pilot sweep protocol | telemetry + memory areas trimmed with migrations recorded BEFORE deletion; per-commit shrink protocol proven; count ≤1130 | NFR-004 | Phase 1 |
| 3 | Bulk sweep | graph, interface (cli/mcp_server/dashboard), and long-tail areas trimmed in per-area suite-gated commits; count ≤550; every durable rationale migrated and ledgered | FR-001, FR-002, FR-003 | Phase 2 |
| 4 | Closeout & ratchet handoff | final shrink-only re-derivation lands ≤606 (target ≤550) via `make comment-style-shrink`; delivery record complete with every migration listed; CHANGELOG entry; full CI green | FR-005, NFR-005 | Phase 3 |

Baseline facts driving the sizing (re-counted this session from
`docs/audits/comment-style-baseline.json`): 1213 fingerprints (897 docstring +
316 comment) across 188 `src/` files; by area — graph 334, cli 126, parsers
107, mcp_server 96, dashboard 83, knowledge 70, agent_install 66, telemetry
54, memory 51, bench 42, compass 31, wiki 26, all remaining ≤17 each.

## Dependencies
- Phase 1 is the spine: the D-012 import fix must land first so every later
  gate invocation (pre-commit hook, make targets, CI) runs warning-free while
  the sweep multiplies those invocations.
- Phase 2 proves the full per-commit protocol (migrate → trim → shrink → gate)
  on two small areas (105 fingerprints, 10 files) before Phase 3 scales it.
- Phase 3 consumes the ledger scaffold from Phase 1/2 and is internally
  ordered per-area (graph → interfaces → long tail), each area a
  suite-green commit, because the baseline file is shared mutable state.
- Phase 4 consumes Phase 3's final tree state; only after all trims does the
  definitive `make comment-style-shrink` re-derivation run (FR-005).

## Parallelization map
- Independent: Phase 1's two work areas — the D-012 fix
  (`src/cairn/cli/system/report.py`) and the ledger scaffold
  (`docs/audits/comment-sweep.md`) touch disjoint files and are parallel.
- Independent in principle, serialized by shared state in practice: per-area
  trim EDITS are disjoint file sets (e.g. `src/cairn/telemetry/` vs
  `src/cairn/memory/`), but every trim commit must also re-derive
  `docs/audits/comment-style-baseline.json` (the gate exits 1 on stale
  entries and the pre-commit hook runs on every commit) and append to the
  append-only ledger `docs/audits/comment-sweep.md`. Two shared files per
  commit force one-writer-at-a-time; with a solo dev the phases run serial
  regardless. Task-level chains carry this reason explicitly.
- Strictly ordered: migrations → trims (FR-002 forbids deleting rationale
  before its memory/docs landing exists); trims → per-commit shrink (stale
  gate); Phase 3 trims → Phase 4 final re-derivation (FR-005's count is only
  final once no further trims are planned).

## Checkpoints
- **After Phase 1**: `UV_CACHE_DIR=/tmp/cairn-uv-survey make comment-style 2>&1 | grep RuntimeWarning`
  → empty output (same for `make audit-status`); targeted suites green:
  `CAIRN_LIB=/tmp/__no_such_lib__ UV_CACHE_DIR=/tmp/cairn-uv-survey uv run --extra test pytest tests/test_check_comment_lengths.py tests/test_report.py -q`;
  ledger file exists with empty migration table; full suite green at the phase
  boundary — verified feasible this session (`4309 passed, 5 skipped` in
  6m50s on the baseline tree).
- **After Phase 2**: `UV_CACHE_DIR=/tmp/cairn-uv-survey make comment-style ARGS=--json`
  → `{"ok": true, ..., "remaining": ≤1130, "stale": []}`; ledger lists every
  migration made; `make verify-no-code-change` exit 0 over the phase's commits;
  full suite green at the phase boundary (locally where the host allows, else
  the phase PR's CI).
- **After Phase 3**: same gate → `remaining ≤550`, `new: []`, `stale: []`;
  every mid-phase commit passed pre-commit (comment-style hook) and
  `make verify-no-code-change REF=HEAD~1`; full suite green at the phase
  boundary — locally where the host allows (baseline proven green this
  session: `4309 passed, 5 skipped`), and authoritatively in the phase PR's
  CI (`.github/workflows/ci.yml` test job) for hosts whose sandbox cannot
  bind loopback (survey FR-003).
- **After Phase 4**: `UV_CACHE_DIR=/tmp/cairn-uv-survey make comment-style-shrink ARGS=--json && jq '.violations | length' docs/audits/comment-style-baseline.json`
  → ≤606 (target ≤550); delivery record lists every migration with its
  memory-record type/title or docs target; CHANGELOG entry present; CI green.

## Risks & mitigations
- Risk: wide mechanical diff obscures review → mitigation: per-area commits
  (10–30 files max), `--stat`-reviewable shape, suite-green gate between
  areas, AST-equivalence proof per commit.
- Risk: prose-pinned tests churn → mitigation: same-step pin update rule
  (NFR-004); known pins inventoried in tech-spec (doctor help text, MCP
  non-empty docstrings); per-trim `rg` check over `tests/` before deleting
  distinctive prose.
- Risk: yield underestimates leave the count above 606 → mitigation: Phase 3
  includes 448 long-tail fingerprints of headroom beyond the ~440 needed;
  Phase 4 trims more only if the margin demands.
- Risk: deleting rationale that only looked obvious → mitigation: FR-002
  ordering (memory record BEFORE deletion) plus ledger review in each PR.
- Risk: some sandboxes cannot run the full suite (loopback EPERM, survey
  FR-003) → mitigation: targeted local suites per commit + full suite at each
  phase boundary where the host allows (baseline proven green this session:
  4309 passed, 5 skipped) + CI on every phase PR (C-01 workflow makes CI the
  authority).

## Delivery
Branch `feat/comment-sweep`; one PR per phase (4 PRs), per-area commits inside
Phase 3. Every commit passes pre-commit (ruff, gitleaks, comment-style hook);
every PR carries the audit checklist and watches CI to green before merge.
The sweep changes no executable semantics except the D-012 import-chain fix in
Phase 1 — proven per commit by `make verify-no-code-change` (Phase 1's
report.py commit is the single documented exception).

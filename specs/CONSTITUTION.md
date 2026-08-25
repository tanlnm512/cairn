# Constitution: cairn

<!-- Non-negotiable principles every spec and task in this repo obeys.
     Created by scaffold.sh on the repo's first spec; filled with the user
     (clarify pass 2026-08-25 — drafted from AGENTS.md hard rules; the
     Stage-4 gate is the veto point). Amending is deliberate and appending
     — never silently weaken an article. Checked at the before-audit;
     carried in every implementer payload. One principle per article,
     MUST-strength. -->

## Articles
- **C-01**: Every change lands on a feature branch via a conventional
  commit (`type(optional-scope): subject`) and a PR carrying the audit
  checklist — never pushed directly to `main`.
- **C-02**: `pre-commit run --all-files` must pass before every commit;
  `git commit --no-verify` past a pre-commit failure is a human-only
  decision, never an agent's.
- **C-03**: Every behavior change ships with tests — bugfixes
  failing-test-first; features with test cases traced to their FRs.
- **C-04**: No new runtime dependency (and no dependency replacement) without
  a tech-spec D-### decision recording why and what it costs.
- **C-05**: After completing a task, `cairn update` runs to refresh the
  graph, and learnings are recorded via `record_memory` (plus `cairn doctor`
  when performance/fallback paths were touched).

## Rationale
- C-01: direct pushes to `main` skip the PR-title/dependency-review gates
  and the review layer — the repo's audit chain exists to be used.
- C-02: `--no-verify` defeats Layer 0 (pre-commit) — only a human may
  accept that risk.
- C-03: untested behavior is unverifiable behavior — the self-demo and CI
  guarantees rot the first time a change lands untested.
- C-04: dependencies are the attack surface and the install-weight surface;
  swapping them silently launders a decision nobody examined.
- C-05: the graph and tribal memory are cairn's product — not refreshing
  them after a change lets them drift from the code they describe.

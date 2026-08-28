# Constitution: cairn

<!-- Non-negotiable principles every spec and task in this repo obeys.
     Created by scaffold.sh on the repo's first spec; filled 2026-08-28
     (clarify pass returned no answers; articles adopted from established
     repo conventions — AGENTS.md and verified test gotchas — and presented
     again at the approval gate). Amending is deliberate and appending —
     never silently weaken an article. Checked at the before-audit; carried
     in every implementer payload. One principle per article, MUST-strength. -->

## Articles
- **C-01 — Shipping workflow**: every change lands via `branch → pre-commit run --all-files → conventional commit → push feature branch → PR (audit checklist filled) → watch CI`; never push directly to `main`; never `git commit --no-verify` past a pre-commit failure.
- **C-02 — Test-first**: every new behavior ships with a failing-test-first task; no implementation task is done without its test case passing.
- **C-03 — Dependency gate**: no new runtime dependency without a tech-spec `D-###` decision recording why and what it costs.
- **C-04 — Test isolation**: no eager `cairn.cli`/`cairn.mcp_server` imports in test modules; never patch the global `subprocess.Popen`; tests using `tmp_path` must not leak workspaces into the real `~/.cairn`.

## Rationale
- C-01: the PR gates (pre-commit, PR-title/dependency-review, audit checklist) are the repo's Layers 0-3 review pipeline; bypassing them skips review entirely.
- C-02: cairn's regression surface (multi-client config merging, daemon lifecycle, doctor checks) is too wide for untested landings; the failing-test-first task is what proves a TC actually covers the behavior.
- C-03: every runtime dep widens the wheel/platform matrix (see the musllinux/Intel-macOS drops in 0.14.0); additions must be a recorded decision, not an import-time accident.
- C-04: these three gotchas have each broken CI or leaked state onto dev machines empirically; they are cheap to state and expensive to rediscover.

# Contribution Workflow (binding for agents)

← [Docs index](README.md)

> The mandatory procedure for making and shipping any change in this repo —
> feature, improvement, bugfix, or docs. **Agents: follow this exactly; do not
> improvise the order or skip steps.** CI enforces the automatable parts; this
> workflow covers the rest (branching, commit shape, PR, post-merge).
>
> This is referenced from `AGENTS.md` under "Shipping a change — MANDATORY
> workflow", which is the trigger that routes every agent here.

**The flow:** branch → local gates → conventional commit → push → PR → CI → merge → post-task.
Each step has an exact command below.

## Contents

| Section | What it covers |
|---------|----------------|
| [`## Prerequisites (one-time)`](#prerequisites-one-time) | The two one-time setups: the pre-commit hook and branch protection. |
| [`## 1. Branch off main`](#1-branch-off-main) | Creating the feature branch; why `main` is never committed to directly. |
| [`## 2. Make the change (the *how* — see AGENTS.md)`](#2-make-the-change-the-how--see-agentsmd) | The explore-first editing rules and the comments-only verification escape hatch. |
| [`## 3. Run local gates (Layer 0)`](#3-run-local-gates-layer-0) | The pre-commit gate and the optional containerized CI replication. |
| [`` `## 4. Commit (conventional — required by CI and by `cz bump`)` ``](#4-commit-conventional--required-by-ci-and-by-cz-bump) | The conventional commit-title shape CI and `cz bump` depend on. |
| [`## 5. Push the feature branch`](#5-push-the-feature-branch) | Pushing the branch, and why a push alone runs no CI. |
| [`## 6. Open the PR`](#6-open-the-pr) | PR creation and the audit checklist the template carries. |
| [`## 7. Watch CI; fix on the SAME branch`](#7-watch-ci-fix-on-the-same-branch) | When the checks run and the fix-on-the-same-branch rule. |
| [`## 8. After merge — post-task (binding, see AGENTS.md §"After completing a task")`](#8-after-merge--post-task-binding-see-agentsmd-after-completing-a-task) | The mandatory post-merge graph update and memory capture. |
| [`## Gate-failure decision table`](#gate-failure-decision-table) | The failed-check → fix mapping, and which checks are advisory-only. |
| [`` `## Branch protection (one-time, via `gh`)` ``](#branch-protection-one-time-via-gh) | The `gh api` setup that makes the CI gates actually block merges. |
| [`## Do NOT`](#do-not) | The hard prohibitions: direct pushes to main, `--no-verify`, PR close/reopen, skipped checklists, non-conventional titles. |

---

## Prerequisites (one-time)

```bash
# 1. Layer 0 hook — runs ruff/gitleaks/yaml-checks on every commit.
uv run pre-commit install

# 2. Branch protection — makes the CI gates actually block merges.
#    See "Branch protection" at the bottom of this doc (the `gh` one-liner).
```

## 1. Branch off main

```bash
git checkout main && git pull
git checkout -b <type>-<short-slug>     # e.g. feat/parser-edge-case, fix/mcp-leak
```

Never commit directly to `main`.

## 2. Make the change (the *how* — see AGENTS.md)

- `explore(query)` first, then drill down with the specific tools.
- **Before editing a file:** `ask_compass(file_path=...)`, `find_definition`, `get_callers`.
- Comment/docstring-only edit? Run `make verify-no-code-change` before staging.

## 3. Run local gates (Layer 0)

```bash
uv run pre-commit run --all-files     # ruff F-only, gitleaks, yaml/toml, debug-statements
```

If a hook fails → fix the file → re-run. Do NOT `--no-verify` past a failure.

### Optional: replicate CI exactly before pushing (apple container)

`make ci-local` re-runs the CI **test job** in a bare Linux container via
Apple's [`container`](https://github.com/apple/container) CLI (Virtualization
framework — no Docker needed). No host PATH/HOME/agent CLIs leak in, so it
catches non-hermetic tests (green locally only because of the dev machine)
before you push. Other CI jobs are available too:

```bash
make ci-local                          # test job (Python 3.12): skill evals + core + full suite
make ci-local PYTHON_VERSION=3.11      # same, on another version
make ci-local-all                      # the full 3.10–3.14 matrix, sequentially
scripts/ci-local.sh security           # pip-audit (hard gate) + bandit (advisory)
scripts/ci-local.sh typecheck          # mypy (advisory)
scripts/ci-local.sh precommit          # pre-commit run --all-files
scripts/ci-local.sh build              # wheel + sdist + import check
scripts/ci-local.sh bench              # bench + advisory baseline comparison
CI_LOCAL_ARCH=linux/amd64 make ci-local   # GitHub-runner parity via Rosetta
```

Venv/pip/pre-commit caches persist under `.cache/ci-local/` (gitignored), so
only the first run per Python version pays the install. The first-ever run
also boots the container VM and pulls the `python:<ver>-bookworm` image.
GitHub-only jobs (PR-title gate, dependency review, artifact/PR-comment
uploads) have no local equivalent.

## 4. Commit (conventional — required by CI and by `cz bump`)

```bash
git commit -m "feat(parser): handle C++ template edge case"
```

Title shape: `type(optional-scope): subject`.
Types: `feat fix chore docs ci refactor perf test build style revert`.

## 5. Push the feature branch

```bash
git push -u origin HEAD
```

Note: pushing a feature branch does **not** run CI — CI runs when the PR opens (step 6)
and again on every later push to the branch.

## 6. Open the PR

```bash
gh pr create --fill      # the PR template (.github/PULL_REQUEST_TEMPLATE.md) auto-loads
```

Fill the template's audit checklist (procedure: `docs/review-checklist.md`):
blast radius (`explore` + `impact_analysis`), layering (`ask_compass`), graph
updated (`cairn update`), memory recorded, tests, CHANGELOG.

## 7. Watch CI; fix on the SAME branch

Seven checks run on PR open (and on each later push). Use the gate-failure table
below. Push fixes to the branch — CI re-runs automatically; do **not**
close/reopen the PR.

## 8. After merge — post-task (binding, see AGENTS.md §"After completing a task")

```bash
git checkout main && git pull
cairn update            # refresh the graph with the merged change
# record_memory(...)    # any decision / pattern / mistake worth keeping
```

---

## Gate-failure decision table

Map the failed check to its fix. Check names match the CI job `name:` fields.

| Failed check | Meaning | Fix |
|--------------|---------|-----|
| `pre-commit (all local gates)` — ruff | unused import / undefined name (F-rule) | fix the code, re-commit |
| `pre-commit …` — gitleaks | a secret / API key in the diff | remove it (rotate if real); **never** `--no-verify` a real secret |
| `pre-commit …` — yaml/toml/large-file | malformed config or big binary | fix the file / drop the binary |
| `Security (pip-audit + bandit)` — pip-audit | a dependency has a known CVE | bump the dep to a fixed version |
| `PR title (conventional commits)` | title isn't `type: subject` | rename the PR title (no code change) |
| `Dependency review (PR)` | a NEW dep in the PR is vulnerable/unlicensed | pick a different version/package |
| `Test (Python X)` | a test failed | fix code or update the test; run `pytest -m core` locally first |
| `Build wheel + sdist` | wheel won't build or import | fix the packaging/import error |

`Security / bandit` and `Type check (mypy, advisory)` are **advisory**: they show
green even with findings (`continue-on-error`). Open their job logs and review any
*new* finding — don't ignore them just because the check is green.

---

## Branch protection (one-time, via `gh`)

Run once (requires `gh auth`; replace the owner/repo if different):

```bash
gh api -X PUT repos/tanlnm512/cairn/branches/main/protection --input - <<'EOF'
{
  "required_status_checks": {
    "strict": true,
    "contexts": [
      "pre-commit (all local gates)",
      "Security (pip-audit + bandit)",
      "Type check (mypy, advisory)",
      "PR title (conventional commits)",
      "Dependency review (PR)",
      "Test (Python 3.12)",
      "Build wheel + sdist"
    ]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "allow_force_pushes": false
}
EOF
```

Notes:
- `strict: true` forces the branch up-to-date with main before merge. Set `false` if that friction annoys you.
- `enforce_admins: false` lets an admin override in an emergency. Set `true` to make it absolute.
- `Test (Python 3.12)` is the canonical version required; add the other matrix versions (`Test (Python 3.10)` … `3.14`) if you want all enforced.
- **After the first PR runs CI once**, verify the exact context names under Settings → Branches — matrix jobs expand and names must match byte-for-byte.

---

## Do NOT

- Push directly to `main` — skips `pr-title`/`dependency-review` and the review layer.
- `git commit --no-verify` to bypass pre-commit — defeats Layer 0.
- Close/reopen the PR to re-run CI — just push to the branch.
- Skip the PR template's audit checklist — it's the Layer 2-3 review gate.
- Write a non-conventional commit/PR title — `cz bump` and the `pr-title` gate both depend on it.

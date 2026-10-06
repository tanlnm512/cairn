.PHONY: dist evals ci-local ci-local-all verify-no-code-change audit-status comment-style comment-style-shrink verify-protection release help

# Build the wheel + sdist into dist/ for the HOST platform only (the
# release workflow's cibuildwheel matrix builds the full platform set).
# Produces:
#   dist/cairn_intel-<version>-cp310-abi3-<platform>.whl
#   dist/cairn_intel-<version>.tar.gz
dist:
	rm -rf dist build *.egg-info
	uv build
	@echo ""
	@echo "Built:"
	@ls -lh dist/
	@echo ""
	@echo "Install with:"
	@echo "  uv tool install ./dist/$$(ls dist/*.whl | head -1)"

evals:
	uv run python scripts/run_skill_evals.py

# Clean-room CI replication: run the CI jobs in a bare Linux container via
# Apple's `container` CLI (Virtualization framework -- no Docker needed).
# No host PATH/HOME/agent CLIs/state -- the same environment a GitHub runner
# has. Catches non-hermetic tests (green locally only because of the dev
# machine) BEFORE pushing. Mirrors .github/workflows/ci.yml job-by-job;
# venv/pip/pre-commit caches persist under .cache/ci-local/ (gitignored).
# Jobs: scripts/ci-local.sh [test|test-all|security|typecheck|precommit|build|bench]
# CI_LOCAL_ARCH=linux/amd64 runs the x86-64 image via Rosetta for runner parity.
PYTHON_VERSION ?= 3.12
ci-local:
	scripts/ci-local.sh test $(PYTHON_VERSION)
	@echo ""
	@echo "clean-room suite green"

ci-local-all:
	scripts/ci-local.sh test-all
	@echo ""
	@echo "clean-room matrix green (3.10-3.14)"

# Verify a "comments/docstrings-only" change didn't alter executable code.
# Compares AST (docstrings blanked) of changed .py files. Run before staging
# uncommitted edits, or pass REF=HEAD~1 to verify a just-made commit.
# Catches the failure mode where sub-agents/bulk edits silently touch code
# while self-reporting "comments only." See docs/release-checklist.md.
verify-no-code-change:
	uv run python scripts/verify_no_code_change.py $(REF)

# Quality ratchets; module flags travel through ARGS because make consumes a
# literal --json itself, so the JSON spelling is ARGS=--json.
audit-status:
	@uv run --no-sync python -m cairn.cli.system.audit_status $(ARGS)

comment-style:
	@uv run --no-sync python -m cairn.cli.system.comment_style $(ARGS)

# Rewrites the comment baseline after cleanup; refuses growth.
comment-style-shrink:
	@uv run --no-sync python -m cairn.cli.system.comment_style --update $(ARGS)

# Maintainer-only main protection check; gh needs admin-scoped auth.
# Deliberately not wired into CI.
PROTECTION_REPO ?= tanlnm512/cairn
verify-protection:
	@uv run --no-sync python scripts/verify_protection.py \
	  --repo $(PROTECTION_REPO) $(ARGS)

# Release walkthrough -- prints the steps AND previews the next bump.
# Does NOT modify anything; the dry-run just shows what cz would do.
# Full details: docs/release-checklist.md "Cutting a release".
release:
	@echo "┌─ Release checklist ───────────────────────────────────────────"
	@echo "│"
	@echo "│  0. You are on main, pulled, working tree clean, CI green."
	@echo "│     Run the pre-release checks (tests, build, etc.) in"
	@echo "│     docs/release-checklist.md first."
	@echo "│"
	@echo "│  1. Finalize CHANGELOG.md: move [Unreleased] -> [X.Y.Z] - YYYY-MM-DD"
	@echo "│     (use the draft printed below as a starting point)."
	@echo "│"
	@echo "│  2. cz bump --yes"
	@echo "│     Bumps version in pyproject.toml + __init__.py, commits,"
	@echo "│     tags vX.Y.Z. CHANGELOG stays hand-maintained."
	@echo "│"
	@echo "│  3. Land the release commits on main, then push the tag."
	@echo "│     main is branch-protected (PRs required), so either:"
	@echo "│       - PR: branch off, gh pr create, merge, then tag-push, OR"
	@echo "│       - tag-first: push the tag now, sync main via PR after."
	@echo "│     git push origin vX.Y.Z"
	@echo "│     The tag push triggers .github/workflows/release.yml:"
	@echo "│     build -> publish to PyPI -> cut GitHub Release."
	@echo "│"
	@echo "│  Watch: gh run watch \$$(gh run list --workflow=release.yml -L1 -q '.[0].databaseId')"
	@echo "└──────────────────────────────────────────────────────────────"
	@echo ""
	@echo "Preview of the next bump (dry-run, nothing is written):"
	@echo ""
	@uv run cz bump --dry-run --changelog-to-stdout || \
		echo "(cz not available -- run 'uv sync --extra dev' first)"

help:
	@echo "Targets: dist evals ci-local ci-local-all verify-no-code-change audit-status comment-style comment-style-shrink verify-protection release help"
	@echo ""
	@echo "  dist                   build wheel + sdist into dist/ (for distribution)"
	@echo "  evals                  validate skill eval specs"
	@echo "  ci-local               clean-room CI replication in a Linux container"
	@echo "                         (PYTHON_VERSION=3.11 to pick; apple container, not docker)"
	@echo "  ci-local-all           the full 3.10-3.14 test matrix, sequentially"
	@echo "  verify-no-code-change  AST-check that changed .py files are comment-only"
	@echo "                         (REF=HEAD~1 to verify a commit; default: uncommitted)"
	@echo "  audit-status           remaining-vs-total audit findings by priority"
	@echo "                         (ARGS=--json for machine-readable output)"
	@echo "  comment-style          gate long comment/docstring blocks against the baseline"
	@echo "                         (ARGS=--json for machine-readable output)"
	@echo "  comment-style-shrink   rewrite the baseline after cleanup; refuses growth"
	@echo "  verify-protection      maintainer-only main branch protection check"
	@echo "                         (ARGS=--json for machine-readable output)"
	@echo "  release                print the release walkthrough + preview the next bump"

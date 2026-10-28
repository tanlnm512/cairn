.PHONY: dist evals ci-local ci-local-rust ci-local-parity audit-status comment-style comment-style-shrink verify-protection release help

# Build the bin wheel + sdist into dist/ for the HOST platform only (the
# release workflow's maturin matrix builds the full platform set).
# Produces:
#   dist/cairn_intel-<version>-py3-none-<platform>.whl
#   dist/cairn_intel-<version>.tar.gz
dist:
	rm -rf dist
	uv build
	@echo ""
	@echo "Built:"
	@ls -lh dist/
	@echo ""
	@echo "Install with:"
	@echo "  uv tool install ./dist/$$(ls dist/*.whl | head -1)"

evals:
	cargo xtask skill-evals

# Clean-room CI replication: run the CI jobs in a bare Linux container via
# Apple's `container` CLI (Virtualization framework -- no Docker needed).
# No host PATH/HOME/agent CLIs/state -- the same environment a GitHub runner
# has. Catches non-hermetic tests (green locally only because of the dev
# machine) BEFORE pushing. Mirrors .github/workflows/ci.yml job-by-job;
# venv/pip/pre-commit caches persist under .cache/ci-local/ (gitignored).
# Jobs: scripts/ci-local.sh [precommit|build|rust|parity|smoke]
# CI_LOCAL_ARCH=linux/amd64 runs the x86-64 image via Rosetta for runner parity.
ci-local:
	scripts/ci-local.sh precommit
	@echo ""
	@echo "clean-room pre-commit gates green"

ci-local-rust:
	scripts/ci-local.sh rust
	@echo ""
	@echo "clean-room rust gates green (fmt/clippy/test/deny)"

ci-local-parity:
	scripts/ci-local.sh parity
	@echo ""
	@echo "clean-room parity gates green (self replay + golden + G2 base)"

# Quality ratchets; module flags travel through ARGS because make consumes a
# literal --json itself, so the JSON spelling is ARGS=--json.
audit-status:
	@cargo xtask audit-status $(ARGS)

comment-style:
	@cargo xtask comment-style $(ARGS)

# Rewrites the comment baseline after cleanup; refuses growth.
comment-style-shrink:
	@cargo xtask comment-style --update $(ARGS)

# Maintainer-only main protection check; gh needs admin-scoped auth.
# Deliberately not wired into CI.
PROTECTION_REPO ?= tanlnm512/cairn
verify-protection:
	@cargo xtask verify-protection \
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
	@echo "│     Bumps version in pyproject.toml, commits,"
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
	@echo "Targets: dist evals ci-local ci-local-rust ci-local-parity audit-status comment-style comment-style-shrink verify-protection release help"
	@echo ""
	@echo "  dist                   build wheel + sdist into dist/ (for distribution)"
	@echo "  evals                  validate skill eval specs"
	@echo "  ci-local               clean-room CI replication in a Linux container"
	@echo "                         (apple container, not docker)"
	@echo "  ci-local-rust          clean-room rust gates (fmt/clippy/test/deny)"
	@echo "  ci-local-parity        clean-room parity gates"
	@echo "  audit-status           remaining-vs-total audit findings by priority"
	@echo "                         (ARGS=--json for machine-readable output)"
	@echo "  comment-style          gate long comment/docstring blocks against the baseline"
	@echo "                         (ARGS=--json for machine-readable output)"
	@echo "  comment-style-shrink   rewrite the baseline after cleanup; refuses growth"
	@echo "  verify-protection      maintainer-only main branch protection check"
	@echo "                         (ARGS=--json for machine-readable output)"
	@echo "  release                print the release walkthrough + preview the next bump"

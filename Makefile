.PHONY: dist evals verify-no-code-change release help

# Build the wheel + sdist into dist/. Produces:
#   dist/cairn_intel-<version>-py3-none-any.whl
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

# Verify a "comments/docstrings-only" change didn't alter executable code.
# Compares AST (docstrings blanked) of changed .py files. Run before staging
# uncommitted edits, or pass REF=HEAD~1 to verify a just-made commit.
# Catches the failure mode where sub-agents/bulk edits silently touch code
# while self-reporting "comments only." See docs/release-checklist.md.
verify-no-code-change:
	uv run python scripts/verify_no_code_change.py $(REF)

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
	@echo "Targets: dist evals verify-no-code-change release help"
	@echo ""
	@echo "  dist                   build wheel + sdist into dist/ (for distribution)"
	@echo "  evals                  validate skill eval specs"
	@echo "  verify-no-code-change  AST-check that changed .py files are comment-only"
	@echo "                         (REF=HEAD~1 to verify a commit; default: uncommitted)"
	@echo "  release                print the release walkthrough + preview the next bump"

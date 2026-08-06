.PHONY: dist evals verify-no-code-change help

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

help:
	@echo "Targets: dist evals verify-no-code-change"
	@echo ""
	@echo "  dist                   build wheel + sdist into dist/ (for distribution)"
	@echo "  evals                  validate skill eval specs"
	@echo "  verify-no-code-change  AST-check that changed .py files are comment-only"
	@echo "                         (REF=HEAD~1 to verify a commit; default: uncommitted)"

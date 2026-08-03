.PHONY: dist evals help

# Build the wheel + sdist into dist/. Produces:
#   dist/cg_intel-<version>-py3-none-any.whl
#   dist/cg_intel-<version>.tar.gz
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

help:
	@echo "Targets: dist evals"
	@echo ""
	@echo "  dist    build wheel + sdist into dist/ (for distribution)"
	@echo "  evals   validate skill eval specs"

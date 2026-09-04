# Tech stack & conventions: cairn

**Created**: 2026-08-28 | **Baseline**: 0.16.0 @ fe7a7f09edb015d6a8fb12cd5d0f1b06ed07f5c3
**Refreshed**: 2026-08-31 @ e002f9b (wiki-enhancements survey — mypy promoted from advisory to a hard gate in CI; test-runner line unchanged); re-verified @ 7663989 (2026-09-04 — ci.yml/pyproject/system.py line refs re-anchored)
Stack, build/test runners, and gates. Cited from pyproject.toml,
.pre-commit-config.yaml, and .github/workflows/ci.yml read at baseline.

## Stack
- Python >= 3.10 (classifiers cover 3.10-3.14; CI matrix runs all five,
  ci.yml:143). setuptools build backend, src-layout.
- Core deps (pyproject.toml:28-63): click >=8.0, rich >=13.0, questionary >=2.0,
  `mcp>=0.9.0,<2.0.0` (FastMCP; SSE/uvicorn/starlette are core in mcp>=0.9),
  pydantic >=2.0, pyyaml, pathspec, packaging, numpy >=1.24, sqlite-vec >=0.1.0,
  and 14 pinned tree-sitter grammars (java, python, swift, typescript,
  javascript, dart, objc, go, php, ruby, c-sharp, c, cpp + core 0.26.0).
- Optional extras (pyproject.toml:65-151): `watch` (watchdog), `test` (pytest),
  `dev` (pytest, pytest-cov, ruff, mypy==2.3.0, bandit, pip-audit, pre-commit,
  commitizen, grpcio-tools, build), `semantic` (sentence-transformers — torch,
  opt-in), `ann`, `scip` (protobuf), `otlp` (OTel SDK + exporter), `ingest`
  (pymupdf4llm, mammoth, markdownify).

## Build & run
- Repo runner is `uv`. Canonical test invocation (pipeline standard, refreshed
  2026-08-31 @ 264647ae): `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test
  pytest <path> -q` — plain `uv run pytest <path> -q` works for many single
  files but the CAIRN_LIB pin (shared-lib override, src/cairn/paths.py:117)
  keeps embedding/lib-probe paths hermetic; use the canonical form.
  `uv run pre-commit install` / `uv run pre-commit run --all-files` for the
  local gate (.pre-commit-config.yaml:9-11).
- Install for dev: `pip install -e ".[dev,ingest]"` (CI test job, ci.yml:163-165).
- Release: `cz bump` updates pyproject `version` + `src/cairn/__init__.py:__version__`
  and tags `v$version`; CHANGELOG.md stays hand-maintained
  (pyproject.toml:215-241).

## Tests
- pytest; `testpaths = ["tests"]` so bare runs never collect the vendored
  benchmark corpora (pyproject.toml:169-178).
- Markers (pyproject.toml:179-188): `core` — fast one-test-per-function smoke
  subset (`pytest -m core -q`, <3s); `real_env` — opts a test out of the
  suite-wide hermetic-env fixture (must justify in a comment); `infra` —
  bench/dataset/soak tier, excluded from per-PR CI legs (`-m "not infra"`),
  run on every main push. No addopts on purpose: the bench job's `-k t2`
  gate invokes pytest without `-m`; a global filter would silently shrink it
  (test_suite_hygiene pins the tier's shape).
- Hermetic by default: `tests/conftest.py:47-67` points HOME/CAIRN_HOME into a
  per-test tmp sandbox. Tests that exercise env propagation across a REAL
  process boundary use subprocess + `env={"CAIRN_HOME": ...}` (e.g.
  tests/test_install_uninstall_fidelity.py:509, 552, 571;
  tests/test_cli_init_rail.py:34-96; tests/test_uninstall_cmd.py:39-81).
- Conventions observable in the suite: one test file per feature area
  (`test_doctor.py`, `test_clients.py`, `test_install_uninstall_fidelity.py`,
  `test_server_robustness.py`, `test_atomic_config_writes.py`, ...), class-
  grouped (`TestHookIdempotency`, `TestStoreExistenceCheck`), behavior-named
  tests (`test_missing_store_fails_instead_of_creating`), and JSON-mode
  assertions against CLI `--json` output (test_doctor.py:96-124).

## Lint / type / security gates
- ruff, pyflakes-only (`select = ["F"]`, pyproject.toml:184-206) — a deliberate
  conservative gate; style rules excluded. Excludes tests/fixtures and the
  vendored benchmark corpora.
- mypy ==2.3.0, HARD gate (`mypy --ignore-missing-imports src`, ci.yml:67-68 —
  no `continue-on-error`; runs clean at default settings, ci.yml:50).
  bandit advisory (`continue-on-error: true`, ci.yml:46; `-ii -ll -s B608`).
  pip-audit is a HARD gate (ci.yml:43-44).
- pre-commit (Layer 0, .pre-commit-config.yaml:16-51): ruff (no --fix),
  gitleaks, check-yaml, check-toml, check-merge-conflict,
  check-added-large-files (500kb), debug-statements.

## CI (ci.yml, on push/PR to main)
Jobs: `security` (pip-audit hard + bandit advisory), `typecheck` (mypy hard
gate), `pr-title` (conventional-commit title gate — types feat fix chore
docs ci refactor perf test build style revert, ci.yml:93-105),
`pre-commit` (server-side run of all Layer-0 hooks),
`dependency-review` (PR delta vs GitHub Advisory DB),
`test` (matrix 3.10-3.14: skill-eval validation, `pytest -m core -q`, full
`pytest -q --cov`), `ds2-seal` (ground-truth dataset verifier),
`build` (wheel + sdist + import check), `bench` (advisory perf baseline
comparison, continue-on-error).

## Commit / PR conventions
- Conventional commits, enforced twice: locally by workflow discipline and in
  CI by the pr-title job. Commit AND PR title must be
  `type(optional-scope): subject`.
- PR template `.github/PULL_REQUEST_TEMPLATE.md` carries an audit checklist;
  `docs/review-checklist.md` is the procedure (blast radius via
  explore/impact_analysis, layering via ask_compass, post-task
  `cairn update` + `record_memory`).
- Never push to main directly; never `--no-verify` past a pre-commit failure.

## Runtime contracts worth knowing when adding commands
- Doctor/status/report are read-only diagnostics: doctor exits 0 when every
  check is PASS/WARN and 1 on any FAIL (doctor command system.py:1750, exit
  at 1774); a missing store is FAILed, never created
  (`_db_unavailable_results` system.py:1657, `_run_doctor` 1680).
- `cairn doctor --json` emits the raw `_result` list
  (`{name, status, detail, hint}`); the check-name sequence is pinned by
  test_doctor.py:96-124 — adding a check means updating that test.
- User-config writes must stay atomic + backup-on-malformed
  (agent_install/merge.py:25-54, 119-205).
- SSE daemon lifecycle (`serve start/stop/status/restart`) is macOS/launchd
  only; non-darwin `serve start` exits 1 with a manual-supervisor hint
  (cli/serve.py:105-108).

#!/usr/bin/env bash
# ci-local.sh -- run the GitHub Actions CI jobs locally in a Linux container.
#
# Uses Apple's `container` (Virtualization framework) instead of Docker --
# same engine this repo's Makefile `ci-local` target drives. Mirrors
# .github/workflows/ci.yml job-by-job so "green locally" means "green CI"
# (modulo GitHub-only jobs: PR title, dependency review, artifact uploads).
#
# Usage:
#   scripts/ci-local.sh [JOB] [PY_VERSION]    (JOB default: test, PY default: 3.12)
#   make ci-local                              # test job, Python 3.12
#   make ci-local PYTHON_VERSION=3.11          # test job on another version
#   make ci-local-all                          # the full 3.10-3.14 matrix
#   scripts/ci-local.sh security               # pip-audit (hard) + bandit (advisory)
#   scripts/ci-local.sh typecheck              # mypy (advisory, like CI)
#   scripts/ci-local.sh precommit              # pre-commit run --all-files
#   scripts/ci-local.sh build                  # wheel + sdist + import check
#   scripts/ci-local.sh bench                  # bench + advisory baseline compare
#
# Environment:
#   CI_LOCAL_ARCH     linux/arm64 (default, native) or linux/amd64
#                     (GitHub-runner parity via Rosetta; needs --rosetta)
#   CI_LOCAL_CPUS     CPU count for the container (default: runtime default)
#   CI_LOCAL_MEMORY   container memory, e.g. 8G (default: runtime default)
#
# Caching: the venv, pip cache, and pre-commit envs live in <repo>/.cache/
# ci-local/ inside the bind-mounted workspace, so they persist across runs
# (gitignored). Delete that directory to force a cold rebuild.
#
# First run note: `container system start` is invoked if the service isn't
# running (it boots the Linux VM, ~10s); the python:<ver>-bookworm image
# (~1 GB) is pulled once per version.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MOUNT_POINT=/workspace
CACHE_REL=.cache/ci-local
VERSIONS_ALL=(3.10 3.11 3.12 3.13 3.14)

log()  { printf '\033[1;34m[ci-local]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[ci-local]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[ci-local]\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

# ---------------------------------------------------------------------------
# Host side: dispatch the job into a fresh Linux container.
# ---------------------------------------------------------------------------
if [[ -z "${CI_LOCAL_INNER:-}" ]]; then
  command -v container >/dev/null 2>&1 || die "apple \`container\` CLI not found (brew install container)"

  JOB="${1:-test}"
  PY="${2:-${PYTHON_VERSION:-3.12}}"
  case "$JOB" in
    -h|--help|help) usage 0 ;;
    test|test-all|security|typecheck|precommit|build|bench) ;;
    *) die "unknown job '$JOB' (test|test-all|security|typecheck|precommit|build|bench)" ;;
  esac

  # Boot the container system service if needed (idempotent, ~10s cold).
  if ! container system status >/dev/null 2>&1; then
    log "starting container system service..."
    container system start >/dev/null
  fi

  if [[ "$JOB" == "test-all" ]]; then
    log "full matrix: ${VERSIONS_ALL[*]} (sequential, like CI's fail-fast=false matrix)"
    fail=0
    for v in "${VERSIONS_ALL[@]}"; do
      log "── Test (Python $v) ──────────────────────────────────"
      "$0" test "$v" || fail=1
    done
    if [[ $fail -ne 0 ]]; then die "matrix red: at least one version failed"; fi
    log "matrix green across ${VERSIONS_ALL[*]}"
    exit 0
  fi

  ARCH_FLAGS=()
  case "${CI_LOCAL_ARCH:-linux/arm64}" in
    linux/arm64|arm64) ARCH_FLAGS=(--arch arm64) ;;
    linux/amd64|amd64) ARCH_FLAGS=(--arch amd64 --rosetta) ;;
    *) die "CI_LOCAL_ARCH must be linux/arm64 or linux/amd64" ;;
  esac
  RES_FLAGS=()
  [[ -n "${CI_LOCAL_CPUS:-}" ]] && RES_FLAGS+=(--cpus "$CI_LOCAL_CPUS")
  [[ -n "${CI_LOCAL_MEMORY:-}" ]] && RES_FLAGS+=(--memory "$CI_LOCAL_MEMORY")

  log "job=$JOB python=$PY arch=${CI_LOCAL_ARCH:-linux/arm64} repo=$REPO"
  exec container run --rm \
    --mount "type=bind,source=${REPO},target=${MOUNT_POINT}" \
    --workdir "$MOUNT_POINT" \
    --uid "$(id -u)" --gid "$(id -g)" \
    "${ARCH_FLAGS[@]}" "${RES_FLAGS[@]+"${RES_FLAGS[@]}"}" \
    -e CI_LOCAL_INNER=1 -e CI_LOCAL_JOB="$JOB" -e CI_LOCAL_PY="$PY" \
    -e HOME="$MOUNT_POINT/$CACHE_REL/home" \
    "python:${PY}-bookworm" \
    bash "$MOUNT_POINT/scripts/ci-local.sh"
fi

# ---------------------------------------------------------------------------
# Inner side: runs INSIDE the container. Mirrors the ci.yml steps verbatim;
# the mapping is noted per job so the workflow stays the source of truth.
# ---------------------------------------------------------------------------
JOB="${CI_LOCAL_JOB:?inner: CI_LOCAL_JOB unset}"
PY="${CI_LOCAL_PY:?inner: CI_LOCAL_PY unset}"
CACHE="$MOUNT_POINT/$CACHE_REL"
mkdir -p "$CACHE/home" "$CACHE/pip"
export HOME="$CACHE/home"
export PIP_CACHE_DIR="$CACHE/pip"
export PRE_COMMIT_HOME="$CACHE/pre-commit"
export PIP_DISABLE_PIP_VERSION_CHECK=1

VENV="$CACHE/venv-$PY"
if [[ ! -x "$VENV/bin/python" ]]; then
  log "creating venv for Python $PY (cached under $CACHE_REL/)"
  python"$PY" -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
export PATH="$VENV/bin:$PATH"

install_extras() { # $@ = extras, e.g. dev / dev,otlp
  log "pip install -e \".[$1]\" (CI: Install step)"
  python -m pip install --upgrade pip -q
  python -m pip install -e ".[$1]" -q
}

cd "$MOUNT_POINT"

case "$JOB" in
  test)
    # ci.yml: test job -- skill-eval validation, core smoke subset, full suite.
    install_extras dev
    log "validating skill eval specs (CI: Validate skill eval specs)"
    python scripts/run_skill_evals.py
    log "core smoke subset (CI: Run core smoke subset)"
    python -m pytest -m core -q
    log "full suite (CI: Run full test suite)"
    python -m pytest -q --junitxml=test-results.xml \
      --cov=cairn --cov-report=term --cov-report=xml
    log "suite green (results: test-results.xml, coverage.xml)"
    ;;

  security)
    # ci.yml: security job -- pip-audit is a HARD gate, bandit advisory.
    install_extras dev,otlp
    log "pip-audit (CI: hard gate)"
    python -m pip_audit
    log "bandit (CI: advisory -- findings below don't fail)"
    python -m bandit -r src -ii -ll -s B608 || warn "bandit findings above (advisory in CI too)"
    log "security job green"
    ;;

  typecheck)
    # ci.yml: typecheck job -- mypy is advisory (continue-on-error in CI).
    install_extras dev
    log "mypy (CI: advisory -- findings below don't fail)"
    python -m mypy --ignore-missing-imports src \
      || warn "mypy findings above (advisory in CI too)"
    ;;

  precommit)
    # ci.yml: pre-commit job -- all Layer-0 gates server-side.
    log "pip install pre-commit (CI: Install pre-commit)"
    python -m pip install --upgrade pip -q
    python -m pip install "pre-commit>=4.0" -q
    log "pre-commit run --all-files (CI: Run all hooks)"
    pre-commit run --all-files
    ;;

  build)
    # ci.yml: build job -- distributions + wheel import check (no upload).
    log "python -m build (CI: Build distributions)"
    python -m pip install --upgrade pip build -q
    python -m build
    log "wheel import check (CI: Verify the wheel imports)"
    python -m pip install --upgrade pip -q
    python -m pip install dist/*.whl -q
    python -c "import cairn; print('ok')"
    ;;

  bench)
    # ci.yml: bench job -- fixed corpus, hash backend, advisory throughout.
    install_extras ""
    log "cairn bench (CI: Run bench -- advisory)"
    cairn bench --suite perf --n-files 60 --complexity medium \
      --embed-backend hash --repeats 3 \
      --json --save bench-current.json \
      || warn "bench failed (advisory in CI too)"
    if [[ -f bench-baseline.json && -f bench-current.json ]]; then
      log "comparing against bench-baseline.json (CI: Compare vs baseline)"
      python .github/scripts/bench_compare.py || warn "comparison failed (advisory)"
    else
      log "no baseline yet -- this run establishes bench-baseline.json"
    fi
    [[ -f bench-current.json ]] && cp bench-current.json bench-baseline.json
    ;;

  *) die "inner: unknown job '$JOB'" ;;
esac

log "job '$JOB' (python $PY) complete"

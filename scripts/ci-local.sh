#!/usr/bin/env bash
# ci-local.sh -- run the GitHub Actions CI jobs locally in a Linux container.
#
# Uses Apple's `container` (Virtualization framework) instead of Docker --
# same engine this repo's Makefile `ci-local` target drives. Mirrors
# .github/workflows/ci.yml job-by-job so "green locally" means "green CI"
# (modulo GitHub-only jobs: PR title, dependency review, artifact uploads).
#
# Usage:
#   scripts/ci-local.sh [JOB] [PY_VERSION]    (JOB default: precommit, PY default: 3.12)
#   make ci-local                              # precommit job
#   scripts/ci-local.sh precommit              # prek run --all-files
#   scripts/ci-local.sh build                  # wheel + sdist + smoke (needs cargo)
#   scripts/ci-local.sh rust                   # rust.yml ubuntu legs: fmt/clippy/test/deny
#   scripts/ci-local.sh parity                 # rust-parity.yml PR leg: oracles + G2 base
#   scripts/ci-local.sh smoke                  # rust-parity.yml nightly wheel smoke
#
# Not mirrored (GitHub-only): PR title, dependency review, windows-latest,
# macos runners, container/Dockerfile build, review.yml pre-submit.
#
# Environment:
#   CI_LOCAL_ARCH     linux/arm64 (default, native) or linux/amd64
#                     (GitHub-runner parity via Rosetta; needs --rosetta)
#   CI_LOCAL_CPUS     CPU count for the container (default: runtime default)
#   CI_LOCAL_MEMORY   container memory, e.g. 8G (default: runtime default)
#
# Caching: the venv, pip cache, and prek envs live in <repo>/.cache/
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

  JOB="${1:-precommit}"
  PY="${2:-3.12}"
  case "$JOB" in
    -h|--help|help) usage 0 ;;
    precommit|build|rust|parity|smoke) ;;
    *) die "unknown job '$JOB' (precommit|build|rust|parity|smoke)" ;;
  esac

  # Boot the container system service if needed (idempotent, ~10s cold).
  if ! container system status >/dev/null 2>&1; then
    log "starting container system service..."
    container system start >/dev/null
  fi

  ARCH_FLAGS=()
  case "${CI_LOCAL_ARCH:-linux/arm64}" in
    linux/arm64|arm64) ARCH_FLAGS=(--arch arm64) ;;
    linux/amd64|amd64) ARCH_FLAGS=(--arch amd64 --rosetta) ;;
    *) die "CI_LOCAL_ARCH must be linux/arm64 or linux/amd64" ;;
  esac
  RES_FLAGS=()
  [[ -n "${CI_LOCAL_CPUS:-}" ]] && RES_FLAGS+=(--cpus "$CI_LOCAL_CPUS")
  # Cargo jobs link whole-workspace test binaries; the default container
  # memory gets the linker OOM-killed mid-link.
  case "$JOB" in
    rust|parity|smoke|build)
      RES_FLAGS+=(--memory "${CI_LOCAL_MEMORY:-8G}") ;;
    *)
      [[ -n "${CI_LOCAL_MEMORY:-}" ]] && RES_FLAGS+=(--memory "$CI_LOCAL_MEMORY") ;;
  esac

  # GitHub's ubuntu runners carry lsof/fuser, jq and unzip; the stock python
  # image does not (the daemon's db-holder verification and the wheel smoke need them).
  BUILD_DIR="$REPO/.cache/ci-local/build/$PY"
  mkdir -p "$BUILD_DIR"
  printf 'FROM python:%s-bookworm\nRUN apt-get update && apt-get install -y --no-install-recommends lsof psmisc procps jq unzip && rm -rf /var/lib/apt/lists/*\n' "$PY" \
    > "$BUILD_DIR/Containerfile"
  # Tagged by content so a Containerfile change rebuilds instead of reusing a stale image.
  IMAGE_TAG="ci-local-python:$PY-$(shasum -a 256 "$BUILD_DIR/Containerfile" | cut -c1-12)"
  if ! container image list 2>/dev/null | awk '{print $1":"$2}' | grep -qx "$IMAGE_TAG"; then
    log "building derived image $IMAGE_TAG (github-runner parity tooling)"
    container build --tag "$IMAGE_TAG" "$BUILD_DIR" >/dev/null
  fi

  log "job=$JOB python=$PY arch=${CI_LOCAL_ARCH:-linux/arm64} repo=$REPO"
  exec container run --rm \
    --mount "type=bind,source=${REPO},target=${MOUNT_POINT}" \
    --workdir "$MOUNT_POINT" \
    --uid "$(id -u)" --gid "$(id -g)" \
    "${ARCH_FLAGS[@]}" "${RES_FLAGS[@]+"${RES_FLAGS[@]}"}" \
    -e CI_LOCAL_INNER=1 -e CI_LOCAL_JOB="$JOB" -e CI_LOCAL_PY="$PY" \
    -e CI_LOCAL_ARCH="${CI_LOCAL_ARCH:-linux/arm64}" \
    -e HOME="$MOUNT_POINT/$CACHE_REL/home" \
    "$IMAGE_TAG" \
    bash "$MOUNT_POINT/scripts/ci-local.sh"
fi

# ---------------------------------------------------------------------------
# Inner side: runs INSIDE the container. Mirrors the ci.yml steps verbatim;
# the mapping is noted per job so the workflow stays the source of truth.
# CI installs from the committed uv.lock via `uv sync --extra dev` (the
# project itself is not installed: installing it means compiling the engine);
# this script drives the SAME lockfile through a container-local uv.
# ---------------------------------------------------------------------------
JOB="${CI_LOCAL_JOB:?inner: CI_LOCAL_JOB unset}"
PY="${CI_LOCAL_PY:?inner: CI_LOCAL_PY unset}"
CACHE="$MOUNT_POINT/$CACHE_REL"
mkdir -p "$CACHE/home" "$CACHE/pip"
export HOME="$CACHE/home"
export PIP_CACHE_DIR="$CACHE/pip"
export PREK_HOME="$CACHE/prek"
PREK_VERSION="$(sed -n 's/^ *PREK_VERSION: "\(.*\)"$/\1/p' "$MOUNT_POINT/.github/workflows/ci.yml")"
export PIP_DISABLE_PIP_VERSION_CHECK=1

UV_BIN="$CACHE/uv/bin"
if [[ ! -x "$UV_BIN/uv" ]]; then
  log "installing uv into the cache (CI runners use setup-uv)"
  mkdir -p "$UV_BIN"
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$UV_BIN" sh >/dev/null 2>&1
fi
export PATH="$UV_BIN:$PATH"
command -v uv >/dev/null 2>&1 || die "uv unavailable (installer failed)"

export UV_PYTHON="$PY"
export UV_PROJECT_ENVIRONMENT="$CACHE/venv-$PY"
# uv pip/run resolve VIRTUAL_ENV before UV_PROJECT_ENVIRONMENT; without this
# they discover the bind-mounted host .venv, whose python symlink is a
# different-platform binary inside the container.
export VIRTUAL_ENV="$UV_PROJECT_ENVIRONMENT"

sync_dev() {
  log "uv sync --extra dev (CI: Install step, from the committed uv.lock)"
  cd "$MOUNT_POINT" && uv sync --extra dev
}

# Rust in the container: toolchain + cargo-deny live in the cache; the cargo
# target dir is per-arch and OUTSIDE the bind-mounted rust/target so container
# (linux) builds never thrash the host's darwin artifacts. The xtask oracles
# honor CARGO_TARGET_DIR / CAIRN_RUST_BIN.
ARCH_TAG="${CI_LOCAL_ARCH##*/}"
export CARGO_HOME="$CACHE/cargo-home"
export CARGO_TARGET_DIR="$CACHE/cargo-target-$ARCH_TAG"
export CAIRN_RUST_BIN="$CARGO_TARGET_DIR/release/cairn"

ensure_cargo() {
  if ! command -v cargo >/dev/null 2>&1; then
    log "installing rust toolchain (CI: dtolnay/rust-toolchain)"
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
      | sh -s -- -y --default-toolchain stable --profile minimal \
        --component rustfmt --component clippy >/dev/null 2>&1
  fi
  export PATH="$CARGO_HOME/bin:$PATH"
  command -v cargo >/dev/null 2>&1 || die "cargo unavailable (rustup failed)"
  command -v cargo-fmt >/dev/null 2>&1 || rustup component add rustfmt >/dev/null 2>&1
  command -v cargo-clippy >/dev/null 2>&1 || rustup component add clippy >/dev/null 2>&1
}

ensure_cargo_deny() {
  command -v cargo-deny >/dev/null 2>&1 && return 0
  log "installing cargo-deny (CI: taiki-e/install-action)"
  cargo install --locked cargo-deny >/dev/null 2>&1 \
    || die "cargo-deny install failed"
}

ensure_prek() {
  export PATH="$CACHE/prek-bin:$PATH"
  command -v prek >/dev/null 2>&1 && return 0
  log "installing prek (CI: prek-installer.sh)"
  curl --proto '=https' --tlsv1.2 -LsSf \
    "https://github.com/j178/prek/releases/download/v${PREK_VERSION}/prek-installer.sh" \
    | PREK_INSTALL_DIR="$CACHE/prek-bin" PREK_NO_MODIFY_PATH=1 sh >/dev/null 2>&1 \
    || die "prek install failed"
  command -v prek >/dev/null 2>&1 || die "prek not on PATH after install"
}

cd "$MOUNT_POINT"

case "$JOB" in
  precommit)
    # ci.yml: pre-commit job -- all Layer-0 gates server-side; the
    # comment-style hook runs through cargo xtask.
    ensure_cargo
    ensure_prek
    log "prek run --all-files (CI: Run all hooks)"
    prek run --all-files
    ;;

  build)
    # ci.yml: build job -- distributions + installed-product smoke (no
    # upload). maturin compiles the engine, so the container needs cargo.
    ensure_cargo
    log "maturin build + sdist (CI: Build distributions)"
    uvx maturin build --release --out dist
    uvx maturin sdist --out dist
    log "installed-product smoke (CI: Smoke the built wheel)"
    bash scripts/rust_migration/wheel_smoke.sh dist/*.whl
    ;;

  rust)
    # rust.yml ubuntu legs -- fmt, clippy, workspace tests, cargo-deny.
    ensure_cargo
    log "cargo fmt (rust.yml: fmt)"
    cargo fmt --all --check
    log "cargo clippy -D warnings (rust.yml: clippy)"
    cargo clippy --manifest-path Cargo.toml --workspace --all-targets -- -D warnings
    log "cargo test --workspace (rust.yml: test)"
    CAIRN_ALLOW_MISSING_GOLDENS=1 cargo test --manifest-path Cargo.toml --workspace
    log "cargo deny (rust.yml: deny)"
    ensure_cargo_deny
    cargo deny --config rust/deny.toml check advisories licenses
    ;;

  parity)
    # rust-parity.yml PR leg -- parity self, cli golden, G2 base; the
    # oracles build the release binaries themselves into the isolated
    # target dir.
    ensure_cargo
    log "parity replay self + cli golden (oracles build the binaries)"
    cargo xtask parity --store self
    cargo xtask cli-golden
    log "G2 base leg (rust-parity.yml)"
    bash scripts/rust_migration/g2_check.sh
    ;;

  smoke)
    # rust-parity.yml nightly wheel-smoke leg, through the bin-wheel script.
    ensure_cargo
    sync_dev
    log "wheel smoke (rust-parity.yml: wheel-smoke)"
    bash scripts/rust_migration/wheel_smoke.sh
    ;;

  *) die "inner: unknown job '$JOB'" ;;
esac

log "job '$JOB' (python $PY) complete"

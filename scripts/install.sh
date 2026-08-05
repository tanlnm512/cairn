#!/bin/bash
# scripts/install.sh — Install the cairn CLI binary.
#
# This script installs the `cairn` command. To wire it into AI coding
# clients afterward, run `cairn install-agents` separately.
#
# Usage:
#   ./scripts/install.sh                  # install cairn
#   ./scripts/install.sh --semantic       # also install semantic search extras
#   ./scripts/install.sh --venv           # use venv instead of uv/pipx
#
# Prerequisites:
#   - Python >= 3.10 (auto-detected; not the macOS default 3.9)

set -euo pipefail

# ─── Colors ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${CYAN}➜${NC} $*"; }
ok()    { echo -e "${GREEN}✓${NC} $*"; }
warn()  { echo -e "${YELLOW}⚠${NC} $*"; }
fail()  { echo -e "${RED}✗${NC} $*"; exit 1; }

# ─── Defaults ─────────────────────────────────────────────────────────────────
EXTRA_SEMANTIC=false
USE_VENV=false

# ─── Parse args ───────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --semantic)    EXTRA_SEMANTIC=true ;;
    --venv)         USE_VENV=true ;;
    -h|--help)
      sed -n '2,10p' "$0" | sed 's/^# //' | sed 's/^#//'
      exit 0 ;;
    *) fail "Unknown option: $1 (use --help)" ;;
  esac
  shift
done

# ─── Resolve directories ─────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# ─── Step 0: Find a suitable Python ───────────────────────────────────────
info "Checking prerequisites..."

PYTHON=""
for candidate in python3.12 python3.11 python3.10 python3; do
  if "$candidate" -c "import sys; assert sys.version_info >= (3, 10)" 2>/dev/null; then
    PYTHON="$candidate"
    break
  fi
done

if [[ -z "$PYTHON" ]]; then
  fail "Python >= 3.10 required. Found: $(python3 --version 2>&1 || echo 'not found')"
fi
PY_VER="$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
ok "Python $PY_VER ($PYTHON)"

# ─── Step 1: Clean stale build artifacts ───────────────────────────────────
# setuptools' build_py does mtime-based incremental copying into build/lib,
# not content hashing. A leftover build/ (or *.egg-info) from a previous
# install can get silently repackaged into the new wheel even with
# `uv tool install --force`, keeping old bugs alive after src/ is already
# fixed (see 2026-07-21/22 incident: mcp_server tool schemas stayed broken
# for a day after the fix landed, purely because of a stale build/ dir).
#
# 2026-07-28 follow-up: `uv tool install --force .` reused a stale build even
# with build/dist/*.egg-info absent — uv's own package cache also needs
# clearing, or the old wheel gets served again. `uv cache clean` is scoped to
# this package (not a full cache wipe) so repeat installs stay cheap.
# Always clean before building — this is cheap and has no downside, so it
# is unconditional rather than gated behind a flag.
info "Cleaning stale build artifacts..."
rm -rf "$PROJECT_DIR"/build "$PROJECT_DIR"/dist "$PROJECT_DIR"/*.egg-info
ok "Removed build/, dist/, *.egg-info"

if command -v uv >/dev/null 2>&1; then
  uv cache clean cairn-intel >/dev/null 2>&1 || true
  ok "Cleared uv's cached build for cairn-intel"
fi

# ─── Step 2: Install cairn binary ─────────────────────────────────────────
info "Installing cairn binary..."

if $USE_VENV; then
  VENV_DIR="$PROJECT_DIR/.venv"
  if [[ -d "$VENV_DIR" ]]; then
    ok "venv exists at $VENV_DIR"
  else
    "$PYTHON" -m venv "$VENV_DIR"
    ok "Created venv at $VENV_DIR"
  fi
  source "$VENV_DIR/bin/activate"
  pip install -e "${PROJECT_DIR}[dev]" >/dev/null 2>&1
  ok "Installed cairn (editable, venv mode)"
  CAIRN_CMD="$VENV_DIR/bin/cairn"
else
  # Prefer uv, fall back to pipx, then venv
  if command -v uv >/dev/null 2>&1; then
    uv tool install --force "$PROJECT_DIR" >/dev/null 2>&1
    ok "Installed cairn via uv"
  elif command -v pipx >/dev/null 2>&1; then
    pipx install --force "$PROJECT_DIR" >/dev/null 2>&1
    ok "Installed cairn via pipx"
  else
    warn "Neither uv nor pipx found. Falling back to venv..."
    VENV_DIR="$PROJECT_DIR/.venv"
    "$PYTHON" -m venv "$VENV_DIR"
    source "$VENV_DIR/bin/activate"
    pip install -e "${PROJECT_DIR}[dev]" >/dev/null 2>&1
    ok "Installed cairn (venv fallback)"
    CAIRN_CMD="$VENV_DIR/bin/cairn"
  fi
fi

# Resolve cairn command path
if [[ -z "${CAIRN_CMD:-}" ]]; then
  CAIRN_CMD="$(command -v cairn 2>/dev/null || true)"
fi
if [[ -z "$CAIRN_CMD" || ! -x "$CAIRN_CMD" ]] && command -v uv >/dev/null 2>&1; then
  UV_BIN_DIR="$(uv tool dir --bin 2>/dev/null || true)"
  if [[ -n "$UV_BIN_DIR" && -x "$UV_BIN_DIR/cairn" ]]; then
    CAIRN_CMD="$UV_BIN_DIR/cairn"
  fi
fi
if [[ -z "$CAIRN_CMD" || ! -x "$CAIRN_CMD" ]]; then
  fail "cairn binary not found. Try: $0 --venv"
fi

CAIRN_VERSION=$("$CAIRN_CMD" --version 2>/dev/null || echo "unknown")
ok "cairn --version: $CAIRN_VERSION"

# ─── Step 3: Optional — semantic search extras ─────────────────────────────
#
# Cairn resolves semantic deps (sentence-transformers, numpy, sqlite-vec) from
# a shared lib dir (~/.cairn/lib by default), NOT from its own venv. This is
# deliberate: the deps are heavy (~hundreds of MB via torch) and would be wiped
# on every `uv tool install --force`. The shared dir survives reinstalls.
#
# We install to the same target that `cairn embed --install-deps` uses, so both
# paths are interchangeable. Honor CAIRN_HOME / CAIRN_LIB overrides to match.
if $EXTRA_SEMANTIC; then
  info "Installing semantic search extras (sentence-transformers + numpy + sqlite-vec)..."

  # Resolve the shared lib dir the same way cairn does (paths.py).
  if [[ -n "${CAIRN_LIB:-}" ]]; then
    LIB_DIR="$CAIRN_LIB"
  elif [[ -n "${CAIRN_HOME:-}" ]]; then
    LIB_DIR="$CAIRN_HOME/lib"
  else
    LIB_DIR="$HOME/.cairn/lib"
  fi
  mkdir -p "$LIB_DIR"

  SEMANTIC_PKGS="sentence-transformers>=3.0 numpy>=1.24 sqlite-vec>=0.1.0"

  if $USE_VENV && [[ -f "$PROJECT_DIR/.venv/bin/pip" ]]; then
    # venv mode: the venv IS the runtime, so install there directly.
    "$PROJECT_DIR/.venv/bin/pip" install $SEMANTIC_PKGS >/dev/null 2>&1
  elif command -v uv >/dev/null 2>&1; then
    # uv tool mode: install to the shared lib dir (survives reinstalls).
    uv pip install --target "$LIB_DIR" --python "$PYTHON" $SEMANTIC_PKGS 2>/dev/null || \
      "$PYTHON" -m pip install --target "$LIB_DIR" $SEMANTIC_PKGS >/dev/null 2>&1
  else
    # No uv: install to the shared lib dir via pip --target.
    "$PYTHON" -m pip install --target "$LIB_DIR" $SEMANTIC_PKGS >/dev/null 2>&1
  fi
  ok "Semantic search extras installed to $LIB_DIR"
fi

# ─── Done ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}cairn installed${NC}"
echo ""
echo "  Binary: $CAIRN_CMD"
echo ""
echo "  Next steps:"
echo "    1. cairn init                      # register workspace + build graph"
echo "    2. cairn install-agents            # wire AI coding clients (interactive)"
echo "    3. cairn embed --install-deps      # one-time: semantic deps (bge-m3)"
echo "    4. cairn embed                     # build the embedding index"
echo ""
echo "  Verify:"
echo "    cairn --version"
echo "    cairn stats                        # (after cairn init)"
echo ""
echo "  Uninstall:"
echo "    ./scripts/uninstall.sh"

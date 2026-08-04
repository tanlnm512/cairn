#!/bin/bash
# scripts/install.sh — Install the cairn CLI binary + wire AI coding clients.
#
# This script installs the `cairn` command and (optionally) wires it into your
# AI coding clients (Claude Code, Cursor, Droid, ZCode, etc.) in one shot.
#
# Usage:
#   ./scripts/install.sh                  # install cairn, then interactively pick agents
#   ./scripts/install.sh --semantic       # also install semantic search extras
#   ./scripts/install.sh --venv           # use venv instead of uv/pipx
#   ./scripts/install.sh --no-agents      # skip the agent-wiring step
#   ./scripts/install.sh --agents all     # wire all detected clients (no prompt)
#   ./scripts/install.sh --agents claude,cursor  # wire specific clients
#   ./scripts/install.sh --scope global   # write agent configs to ~/.claude/ etc.
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
WIRE_AGENTS=true
AGENTS_TARGET=""
AGENTS_SCOPE="workspace"

# ─── Parse args ───────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --semantic)    EXTRA_SEMANTIC=true ;;
    --venv)         USE_VENV=true ;;
    --no-agents)    WIRE_AGENTS=false ;;
    --agents)       AGENTS_TARGET="$2"; shift ;;
    --scope)        AGENTS_SCOPE="$2"; shift ;;
    -h|--help)
      sed -n '2,15p' "$0" | sed 's/^# //' | sed 's/^#//'
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
if $EXTRA_SEMANTIC; then
  info "Installing semantic search extras (sentence-transformers + numpy)..."
  if $USE_VENV && [[ -f "$PROJECT_DIR/.venv/bin/pip" ]]; then
    "$PROJECT_DIR/.venv/bin/pip" install "sentence-transformers>=3.0" "numpy>=1.24" >/dev/null 2>&1
  elif command -v uv >/dev/null 2>&1; then
    uv pip install --system "sentence-transformers>=3.0" "numpy>=1.24" 2>/dev/null || \
      "$PYTHON" -m pip install "sentence-transformers>=3.0" "numpy>=1.24" >/dev/null 2>&1
  else
    "$PYTHON" -m pip install "sentence-transformers>=3.0" "numpy>=1.24" >/dev/null 2>&1
  fi
  ok "Semantic search extras installed"
fi

# ─── Step 4: Wire AI coding clients ─────────────────────────────────────────
if $WIRE_AGENTS; then
  echo ""
  echo -e "${BOLD}Step 4: Wire AI coding clients${NC}"
  echo ""

  WS_DIR="$(pwd)"

  if [[ -n "$AGENTS_TARGET" ]]; then
    # --agents was passed: use it directly, no prompt.
    info "Wiring agents: $AGENTS_TARGET (scope: $AGENTS_SCOPE)"
    "$CAIRN_CMD" install-agents --client "$AGENTS_TARGET" --scope "$AGENTS_SCOPE" --workspace "$WS_DIR"
  elif [[ ! -t 0 ]]; then
    # Non-interactive (piped/scripted): auto-install detected-not-installed.
    info "Wiring agents (non-interactive: detected, not yet installed)"
    "$CAIRN_CMD" install-agents --yes --scope "$AGENTS_SCOPE" --workspace "$WS_DIR"
  else
    # Interactive: show detection, let the user choose.
    # First, run install-agents which shows the detection table + prompts.
    "$CAIRN_CMD" install-agents --scope "$AGENTS_SCOPE" --workspace "$WS_DIR"
  fi

  echo ""
  if [[ $? -eq 0 ]]; then
    ok "Agent wiring complete"
  else
    warn "Agent wiring had issues (you can re-run: cairn install-agents)"
  fi
fi

# ─── Done ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}cairn installed${NC}"
echo ""
echo "  Binary: $CAIRN_CMD"
echo ""
echo "  Next steps:"
echo "    1. cairn init                      # register workspace + build graph"
echo "    2. cairn embed --install-deps      # one-time: semantic deps (bge-m3)"
echo "    3. cairn embed                     # build the embedding index"
echo ""
echo "  Or reconfigure agents anytime:"
echo "    cairn install-agents               # interactive client picker"
echo "    cairn install-agents --scope global   # write to ~/.claude/ etc."
echo ""
echo "  Verify:"
echo "    cairn --version"
echo "    cairn stats                        # (after cairn init)"
echo ""
echo "  Uninstall:"
echo "    ./scripts/uninstall.sh"

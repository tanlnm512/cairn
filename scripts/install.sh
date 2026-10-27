#!/bin/bash
# scripts/install.sh — Install the cairn CLI binary.
#
# This script installs the `cairn` command. To wire it into AI coding
# clients afterward, run `cairn install-agents` separately.
#
# Usage:
#   ./scripts/install.sh                  # cargo install from this checkout
#   CARGO_INSTALL_ROOT=DIR ./scripts/install.sh   # install under DIR/bin
#
# Prerequisites:
#   - A Rust toolchain (cargo), see https://rustup.rs

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

# ─── Parse args ───────────────────────────────────────────────────────────────
case "${1:-}" in
  "") ;;
  -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) fail "Unknown option: $1 (use --help)" ;;
esac

# ─── Resolve directories ─────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# ─── Step 1: Check prerequisites ──────────────────────────────────────────
info "Checking prerequisites..."
command -v cargo >/dev/null 2>&1 \
  || fail "cargo not found. Install a Rust toolchain: https://rustup.rs"
ok "$(cargo --version)"

# ─── Step 2: Install cairn binary ─────────────────────────────────────────
info "Installing cairn binary..."
cargo install --locked --path "$PROJECT_DIR/rust/crates/cairn-cli"
ok "Installed cairn"

INSTALL_ROOT="${CARGO_INSTALL_ROOT:-${CARGO_HOME:-$HOME/.cargo}}"
CAIRN_CMD="$INSTALL_ROOT/bin/cairn"
[[ -x "$CAIRN_CMD" ]] || fail "cairn binary not found at $CAIRN_CMD"

CAIRN_VERSION="$("$CAIRN_CMD" --version 2>/dev/null)" || fail "$CAIRN_CMD --version failed"
ok "cairn --version: $CAIRN_VERSION"
if [[ "$(command -v cairn 2>/dev/null || true)" != "$CAIRN_CMD" ]]; then
  warn "$INSTALL_ROOT/bin is not first on PATH for cairn"
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
echo "    3. cairn embed                     # build the embedding index (hash backend)"
echo ""
echo "  Verify:"
echo "    cairn --version"
echo "    cairn stats                        # (after cairn init)"
echo ""
echo "  Uninstall:"
echo "    cairn uninstall            # or ./scripts/uninstall.sh"

#!/bin/sh
# scripts/install.sh — Install the cairn CLI binary.
#
# Usage:
#   curl -LsSf https://raw.githubusercontent.com/tanlnm512/cairn/main/scripts/install.sh | sh
#   ./scripts/install.sh --release               # download prebuilt GitHub Release binary
#   ./scripts/install.sh --version 0.25.0        # install a specific release version
#   ./scripts/install.sh                         # build & install from local checkout
#   CAIRN_INSTALL_DIR=DIR ./scripts/install.sh   # install binary into DIR
#
# Environment:
#   CAIRN_INSTALL_DIR   Target bin directory (default: ~/.local/bin for release,
#                       or $CARGO_INSTALL_ROOT/bin when CARGO_INSTALL_ROOT is set)
#   CAIRN_VERSION       Release version to download (default: latest)

set -eu

REPO="tanlnm512/cairn"
MODE=""
VERSION="${CAIRN_VERSION:-}"
INSTALL_DIR="${CAIRN_INSTALL_DIR:-}"

if [ -t 1 ]; then
  RED='\033[0;31m'
  GREEN='\033[0;32m'
  YELLOW='\033[1;33m'
  CYAN='\033[0;36m'
  BOLD='\033[1m'
  NC='\033[0m'
else
  RED='' GREEN='' YELLOW='' CYAN='' BOLD='' NC=''
fi

info() { printf '%b\n' "${CYAN}➜${NC} $*"; }
ok()   { printf '%b\n' "${GREEN}✓${NC} $*"; }
warn() { printf '%b\n' "${YELLOW}⚠${NC} $*" >&2; }
fail() { printf '%b\n' "${RED}✗${NC} $*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Install the cairn CLI binary.

Usage:
  curl -LsSf https://raw.githubusercontent.com/tanlnm512/cairn/main/scripts/install.sh | sh
  ./scripts/install.sh [OPTIONS]

Options:
  --release             Download the prebuilt GitHub Release tarball (default when piped)
  --source              Build from the local git checkout via `cargo install --locked`
  --version <VER>       Install a specific release version (e.g. 0.25.0 or v0.25.0)
  --install-dir <DIR>   Install the binary into DIR (default: ~/.local/bin)
  -h, --help            Show this help message
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --release) MODE="release" ;;
    --source) MODE="source" ;;
    --version)
      [ "$#" -ge 2 ] || fail "--version requires a value"
      VERSION="$2"
      MODE="release"
      shift
      ;;
    --install-dir)
      [ "$#" -ge 2 ] || fail "--install-dir requires a value"
      INSTALL_DIR="$2"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "Unknown option: $1 (use --help)"
      ;;
  esac
  shift
done

# Detect whether invoked directly from a local cairn checkout.
LOCAL_PROJECT=""
case "$0" in
  */install.sh|install.sh)
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
    if [ -f "$SCRIPT_DIR/../rust/crates/cairn-cli/Cargo.toml" ]; then
      LOCAL_PROJECT="$(cd "$SCRIPT_DIR/.." && pwd)"
    fi
    ;;
esac

if [ -z "$MODE" ]; then
  if [ -n "$VERSION" ]; then
    MODE="release"
  elif [ -n "$LOCAL_PROJECT" ]; then
    MODE="source"
  else
    MODE="release"
  fi
fi

print_next_steps() {
  bin_path="$1"
  bin_dir="$(dirname "$bin_path")"
  ver="$("$bin_path" --version 2>/dev/null)" || fail "$bin_path --version failed"
  ok "cairn --version: $ver"

  resolved="$(command -v cairn 2>/dev/null || true)"
  if [ "$resolved" != "$bin_path" ]; then
    if [ -n "$resolved" ]; then
      warn "Another cairn is earlier on PATH: $resolved (installed to $bin_path)"
    else
      warn "$bin_dir is not on PATH; add: export PATH=\"$bin_dir:\$PATH\""
    fi
  fi

  printf '\n%b\n\n' "${BOLD}cairn installed${NC}"
  printf '  Binary: %s\n\n' "$bin_path"
  printf '  Next steps:\n'
  printf '    1. cairn init                      # register workspace + build graph\n'
  printf '    2. cairn install-agents            # wire AI coding clients (interactive)\n'
  printf '    3. cairn embed                     # build the embedding index (hash backend)\n\n'
  printf '  Verify:\n'
  printf '    cairn --version\n'
  printf '    cairn stats                        # (after cairn init)\n\n'
  printf '  Uninstall:\n'
  printf '    cairn uninstall                    # or ./scripts/uninstall.sh\n'
}

if [ "$MODE" = "source" ]; then
  [ -n "$LOCAL_PROJECT" ] || fail "--source requires running from a local cairn checkout"
  info "Checking prerequisites..."
  command -v cargo >/dev/null 2>&1 \
    || fail "cargo not found. Install Rust (https://rustup.rs) or run with --release."
  ok "$(cargo --version)"

  if [ -n "$INSTALL_DIR" ]; then
    case "$INSTALL_DIR" in
      */bin) CARGO_ROOT="${INSTALL_DIR%/bin}" ;;
      *) CARGO_ROOT="" ;;
    esac
    if [ -n "$CARGO_ROOT" ]; then
      info "Installing cairn binary to $INSTALL_DIR..."
      CARGO_INSTALL_ROOT="$CARGO_ROOT" cargo install --locked --path "$LOCAL_PROJECT/rust/crates/cairn-cli"
      CAIRN_CMD="$INSTALL_DIR/cairn"
    else
      TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/cairn-cargo.XXXXXX")"
      trap 'rm -rf "$TMP_ROOT"' EXIT
      info "Building cairn binary..."
      CARGO_INSTALL_ROOT="$TMP_ROOT" cargo install --locked --path "$LOCAL_PROJECT/rust/crates/cairn-cli"
      mkdir -p "$INSTALL_DIR"
      mv "$TMP_ROOT/bin/cairn" "$INSTALL_DIR/cairn"
      CAIRN_CMD="$INSTALL_DIR/cairn"
    fi
  else
    info "Installing cairn binary from checkout..."
    cargo install --locked --path "$LOCAL_PROJECT/rust/crates/cairn-cli"
    INSTALL_ROOT="${CARGO_INSTALL_ROOT:-${CARGO_HOME:-$HOME/.cargo}}"
    CAIRN_CMD="$INSTALL_ROOT/bin/cairn"
  fi
  [ -x "$CAIRN_CMD" ] || fail "cairn binary not found at $CAIRN_CMD"
  ok "Installed cairn"
  print_next_steps "$CAIRN_CMD"
  exit 0
fi

# --- Release binary mode ----------------------------------------------------

fetch_file() {
  url="$1"
  out="$2"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$url" -o "$out"
  elif command -v wget >/dev/null 2>&1; then
    wget -qO "$out" "$url"
  else
    fail "Neither curl nor wget found on PATH"
  fi
}

resolve_latest_version() {
  latest_url="https://github.com/$REPO/releases/latest"
  if command -v curl >/dev/null 2>&1; then
    final_url="$(curl -fsSL -o /dev/null -w '%{url_effective}' "$latest_url")" \
      || fail "Could not resolve latest release from $latest_url"
  elif command -v wget >/dev/null 2>&1; then
    final_url="$(wget --spider -S -O /dev/null "$latest_url" 2>&1 \
      | awk '/^  Location: / { loc=$2 } END { print loc }' | tr -d '\r')"
  else
    fail "Neither curl nor wget found on PATH"
  fi
  tag="${final_url##*/}"
  [ -n "$tag" ] && [ "$tag" != "latest" ] \
    || fail "Could not determine latest release tag from $final_url"
  printf '%s\n' "${tag#v}"
}

compute_sha256() {
  file="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$file" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$file" | awk '{print $1}'
  elif command -v openssl >/dev/null 2>&1; then
    openssl dgst -sha256 "$file" | awk '{print $NF}'
  else
    fail "No SHA-256 tool found (need sha256sum, shasum, or openssl)"
  fi
}

detect_target() {
  os="$(uname -s)"
  arch="$(uname -m)"
  case "$os" in
    Darwin)
      case "$arch" in
        arm64|aarch64) printf 'aarch64-apple-darwin\n' ;;
        x86_64|amd64)  printf 'x86_64-apple-darwin\n' ;;
        *) fail "Unsupported macOS architecture: $arch" ;;
      esac
      ;;
    Linux)
      case "$arch" in
        x86_64|amd64)  printf 'x86_64-unknown-linux-musl\n' ;;
        aarch64|arm64) printf 'aarch64-unknown-linux-musl\n' ;;
        *) fail "Unsupported Linux architecture: $arch" ;;
      esac
      ;;
    MINGW*|MSYS*|CYGWIN*)
      case "$arch" in
        x86_64|amd64)  printf 'x86_64-pc-windows-msvc\n' ;;
        *) fail "Unsupported Windows architecture: $arch" ;;
      esac
      ;;
    *)
      fail "Unsupported OS '$os'. Install via PyPI (uv tool install cairn-intel) or Cargo."
      ;;
  esac
}

TARGET="$(detect_target)"
case "$TARGET" in
  *windows*) BIN_NAME="cairn.exe" ;;
  *)         BIN_NAME="cairn" ;;
esac

if [ -z "$VERSION" ]; then
  info "Resolving latest cairn release..."
  VERSION="$(resolve_latest_version)"
else
  VERSION="${VERSION#v}"
fi

if [ -z "$INSTALL_DIR" ]; then
  if [ -n "${CARGO_INSTALL_ROOT:-}" ]; then
    INSTALL_DIR="$CARGO_INSTALL_ROOT/bin"
  else
    INSTALL_DIR="$HOME/.local/bin"
  fi
fi

ARCHIVE="cairn-cli-${VERSION}-${TARGET}.tar.gz"
BASE_URL="https://github.com/$REPO/releases/download/v${VERSION}"

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/cairn-install.XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT

info "Downloading cairn v${VERSION} (${TARGET})..."
fetch_file "${BASE_URL}/${ARCHIVE}" "$TMP_DIR/$ARCHIVE" \
  || fail "Download failed: ${BASE_URL}/${ARCHIVE}"
fetch_file "${BASE_URL}/${ARCHIVE}.sha256" "$TMP_DIR/${ARCHIVE}.sha256" \
  || fail "Checksum download failed: ${BASE_URL}/${ARCHIVE}.sha256"

EXPECTED_SHA="$(awk '{print $1}' "$TMP_DIR/${ARCHIVE}.sha256")"
ACTUAL_SHA="$(compute_sha256 "$TMP_DIR/$ARCHIVE")"
[ -n "$EXPECTED_SHA" ] && [ "$EXPECTED_SHA" = "$ACTUAL_SHA" ] \
  || fail "Checksum mismatch for $ARCHIVE (expected $EXPECTED_SHA, got $ACTUAL_SHA)"
ok "Verified SHA-256 checksum"

mkdir -p "$TMP_DIR/extract" "$INSTALL_DIR"
tar -xzf "$TMP_DIR/$ARCHIVE" -C "$TMP_DIR/extract"
[ -f "$TMP_DIR/extract/$BIN_NAME" ] || fail "Archive did not contain $BIN_NAME"
chmod +x "$TMP_DIR/extract/$BIN_NAME"
mv "$TMP_DIR/extract/$BIN_NAME" "$INSTALL_DIR/$BIN_NAME"
ok "Installed cairn to $INSTALL_DIR/$BIN_NAME"

print_next_steps "$INSTALL_DIR/$BIN_NAME"

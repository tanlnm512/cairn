#!/bin/bash
# scripts/uninstall.sh — Thin shim over `cairn uninstall` (same flags).
#
# Usage:
#   ./scripts/uninstall.sh [--full] [--agents-only|--hooks-only|--graph-only|--package-only]
#                          [--client NAME] [--scope workspace|global|all]
#                          [--workspace PATH] [--dry-run] [-y]

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

CAIRN_CMD="$(command -v cairn 2>/dev/null || true)"
for candidate in "$HOME/.cargo/bin/cairn" "$PROJECT_DIR/rust/target/release/cairn"; do
  [[ -n "$CAIRN_CMD" ]] && break
  [[ -x "$candidate" ]] && CAIRN_CMD="$candidate"
done

if [[ -z "$CAIRN_CMD" ]]; then
  echo "✗ cairn binary not found (PATH, ~/.cargo/bin, rust/target/release)." >&2
  echo "  Build it: cargo build --release -p cairn-cli" >&2
  exit 1
fi

exec "$CAIRN_CMD" uninstall "$@"

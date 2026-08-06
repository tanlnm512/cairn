#!/bin/bash
# scripts/install-dev-hooks.sh
#
# Points git's hooksPath at the version-checked scripts/hooks/ directory so
# every contributor runs the same pre-commit checks (ruff on staged .py files).
#
# Unlike scripts/install-hooks.sh (which installs post-commit graph-update
# hooks in *consumer* repos), this wires cairn's OWN development checks.
#
# Usage:   scripts/install-dev-hooks.sh
# Undo:    git config --unset core.hooksPath

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOKS_DIR="$ROOT/scripts/hooks"

if [ ! -d "$HOOKS_DIR" ]; then
  echo "❌ hooks dir not found: $HOOKS_DIR" >&2
  exit 1
fi

# Make hook scripts executable (git requires this).
chmod +x "$HOOKS_DIR"/*

git config core.hooksPath "$HOOKS_DIR"

echo "✓ Dev hooks installed (core.hooksPath -> scripts/hooks/)"
echo "  pre-commit: ruff on staged .py files"
echo ""
echo "  Bypass once: git commit --no-verify"
echo "  Uninstall:   git config --unset core.hooksPath"

#!/bin/bash
# scripts/install-hooks.sh
# Thin wrapper for `cairn hooks install`, which owns the real logic:
# workspace repo discovery and the skip of non-cairn post-commit hooks.

set -euo pipefail

exec cairn hooks install "$@"

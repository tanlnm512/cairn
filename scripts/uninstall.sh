#!/bin/bash
# scripts/uninstall.sh — Remove codegraph: agent wiring, hooks, graph, package.
#
# Usage:
#   ./scripts/uninstall.sh                    # interactive (prompt before each step)
#   ./scripts/uninstall.sh --full             # remove everything (non-interactive)
#   ./scripts/uninstall.sh --agents-only      # remove agent wiring only
#   ./scripts/uninstall.sh --hooks-only       # remove git hooks only
#   ./scripts/uninstall.sh --graph-only       # remove graph + knowledge data only
#   ./scripts/uninstall.sh --package-only     # remove cg binary only
#   ./scripts/uninstall.sh --client cursor     # remove from specific client(s) only
#   ./scripts/uninstall.sh --dry-run          # show what would be removed
#
# Safety: all destructive steps require confirmation unless --full is used.
# CLAUDE.md and AGENTS.md are never removed (created create-if-absent only).

set -euo pipefail

# ─── Colors ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

info()  { echo -e "${CYAN}➜${NC} $*"; }
ok()    { echo -e "${GREEN}✓${NC} $*"; }
warn()  { echo -e "${YELLOW}⚠${NC} $*"; }
fail()  { echo -e "${RED}✗${NC} $*"; exit 1; }

# ─── Defaults ─────────────────────────────────────────────────────────────
MODE="interactive"       # interactive | full
DO_AGENTS=true
DO_HOOKS=true
DO_GRAPH=true
DO_PACKAGE=true
DRY_RUN=false
CLIENT_FLAGS=()

# ─── Parse args ───────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --full)         MODE="full" ;;
    --agents-only)  DO_AGENTS=true; DO_HOOKS=false; DO_GRAPH=false; DO_PACKAGE=false ;;
    --hooks-only)   DO_HOOKS=true; DO_AGENTS=false; DO_GRAPH=false; DO_PACKAGE=false ;;
    --graph-only)   DO_GRAPH=true; DO_AGENTS=false; DO_HOOKS=false; DO_PACKAGE=false ;;
    --package-only) DO_PACKAGE=true; DO_AGENTS=false; DO_HOOKS=false; DO_GRAPH=false ;;
    --client)       CLIENT_FLAGS+=("--client" "$2"); shift ;;
    --dry-run)      DRY_RUN=true ;;
    -h|--help)
      head -n 8 "$0" | tail -n 7
      exit 0 ;;
    *) fail "Unknown option: $1 (use --help)" ;;
  esac
  shift
done

# ─── Helpers ───────────────────────────────────────────────────────────────
confirm() {
  if [[ "$MODE" == "full" ]]; then
    return 0
  fi
  echo -en "  ${YELLOW}Remove?${NC} [y/N] "
  read -r answer
  [[ "$answer" =~ ^[Yy]$ ]]
}

# Resolve cg binary
resolve_cg() {
  local cg
  cg="$(command -v cg 2>/dev/null || true)"
  if [[ -z "$cg" ]] && command -v uv >/dev/null 2>&1; then
    local uv_bin_dir
    uv_bin_dir="$(uv tool dir --bin 2>/dev/null || true)"
    if [[ -n "$uv_bin_dir" && -x "$uv_bin_dir/cg" ]]; then
      cg="$uv_bin_dir/cg"
    fi
  fi
  if [[ -z "$cg" ]]; then
    cg="$PROJECT_DIR/.venv/bin/cg"
  fi
  if [[ ! -x "$cg" ]]; then
    echo ""
    return 1
  fi
  echo "$cg"
}

# Resolve workspace
resolve_workspace() {
  local ws
  if [[ -n "${CODEGRAPH_WORKSPACE:-}" ]]; then
    ws="$CODEGRAPH_WORKSPACE"
  elif [[ -n "${CODEGRAPH_DB:-}" ]]; then
    ws="$(dirname "$(dirname "${CODEGRAPH_DB}")")"
  else
    local cg
    cg="$(resolve_cg)" || return 1
    ws="$("$cg" config --workspace 2>/dev/null || true)"
    if [[ -z "$ws" || ! -d "$ws" ]]; then
      ws="$(dirname "$PROJECT_DIR")"
    fi
  fi
  echo "$ws"
}

# Resolve store path.
#   ws pinned to a workspaces.json key  -> $home/<key>   (single workspace)
#   ws not pinnable, home has stores    -> $home         (whole home)
#   nothing on disk                     -> ""
resolve_store() {
  local ws="$1"
  local home="${CODEGRAPH_HOME:-$HOME/.codegraph}"

  if [[ -f "$home/workspaces.json" && -n "$ws" ]]; then
    local key
    key="$("$PYTHON" -c "
import json, sys
w = json.load(open('$home/workspaces.json'))
for k, v in w.items():
    if k == '$ws' or '$ws'.startswith(k):
        print(v); sys.exit()
" 2>/dev/null || true)"
    if [[ -z "$key" ]]; then
      # python3 might be system 3.9 — try uv run python
      key="$(uv run python -c "
import json, sys
w = json.load(open('$home/workspaces.json'))
for k, v in w.items():
    if k == '$ws' or '$ws'.startswith(k):
        print(v); sys.exit()
" 2>/dev/null || true)"
    fi
    if [[ -n "$key" ]]; then
      echo "$home/$key"
      return
    fi
  fi

  # Workspace not pinnable (e.g. uninstaller run from the tool repo, not a
  # managed workspace). Fall back to the whole home if it has any stores,
  # rather than silently saying "nothing to remove".
  if [[ -d "$home" ]] && /bin/ls -A "$home" 2>/dev/null | grep -qvE '^(workspaces\.json|.DS_Store)$'; then
    echo "$home"
    return
  fi
  echo ""
}

# ─── Resolve directories ──────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# ─── Banner ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}Codegraph Uninstaller${NC}"
echo ""

if $DRY_RUN; then
  echo -e "${DIM}(dry-run: nothing will be deleted)${NC}"
  echo ""
fi

# ─── Detect Python (for workspaces.json parsing) ──────────────────────────
PYTHON=""
for candidate in python3.12 python3.11 python3.10 python3; do
  if "$candidate" -c "import sys; assert sys.version_info >= (3, 10)" 2>/dev/null; then
    PYTHON="$candidate"
    break
  fi
done
if [[ -z "$PYTHON" ]] && command -v uv >/dev/null 2>&1; then
  PYTHON="uv run python"
fi

# ─── Step 1: Uninstall agent integrations ──────────────────────────────────
if $DO_AGENTS; then
  info "Agent integrations"

  CG_CMD="$(resolve_cg)" || true
  WORKSPACE="$(resolve_workspace)" || true

  if [[ -z "$CG_CMD" || -z "$WORKSPACE" ]]; then
    warn "Cannot resolve cg or workspace — skipping agent removal"
    warn "  Set CODEGRAPH_WORKSPACE or run from your workspace directory"
  else
    echo -e "  ${DIM}Workspace: $WORKSPACE${NC}"
    echo -e "  ${DIM}This removes: MCP configs, skills, commands, subagents, hooks${NC}"
    echo -e "  ${DIM}from Claude Code, Claude Desktop, Cursor, Droid, ZCode, agy${NC}"
    echo ""

    if confirm; then
      AGENT_ARGS=()
      if [[ ${#CLIENT_FLAGS[@]} -gt 0 ]]; then
        AGENT_ARGS+=("${CLIENT_FLAGS[@]}")
      fi

      if $DRY_RUN; then
        echo -e "  ${DIM}Would run: $CG_CMD uninstall-agents ${AGENT_ARGS[*]:-}${NC}"
      else
        cd "$WORKSPACE"
        "$CG_CMD" uninstall-agents ${AGENT_ARGS[@]+"${AGENT_ARGS[@]}"} 2>&1 | while IFS= read -r line; do
          echo "  $line"
        done
      fi
      ok "Agent integrations removed"
    else
      warn "Skipped"
    fi
  fi
fi

# ─── Step 2: Uninstall git hooks ──────────────────────────────────────────
if $DO_HOOKS; then
  info "Git hooks"

  CG_CMD="$(resolve_cg)" || true
  WORKSPACE="$(resolve_workspace)" || true

  if [[ -z "$CG_CMD" || -z "$WORKSPACE" ]]; then
    warn "Cannot resolve cg or workspace — skipping hook removal"
  else
    echo -e "  ${DIM}Removes codegraph post-commit hooks from all repos${NC}"
    echo ""

    if confirm; then
      if $DRY_RUN; then
        echo -e "  ${DIM}Would run: $CG_CMD hooks uninstall${NC}"
      else
        cd "$WORKSPACE"
        "$CG_CMD" hooks uninstall 2>&1 | while IFS= read -r line; do
          echo "  $line"
        done || true
      fi
      ok "Git hooks removed"
    else
      warn "Skipped"
    fi
  fi
fi

# ─── Step 3: Remove graph + knowledge data ────────────────────────────────
# If the workspace can be pinned to a workspaces.json key, only that store
# ($HOME/.codegraph/<key>) is removed. If it can't be pinned (e.g. the
# uninstaller is run from the tool repo rather than a managed workspace),
# the whole $HOME/.codegraph is removed instead.
if $DO_GRAPH; then
  info "Graph and knowledge data"

  WORKSPACE="${WORKSPACE:-$(resolve_workspace 2>/dev/null || true)}"
  STORE="$(resolve_store "${WORKSPACE:-}")" || true

  if [[ -z "$STORE" || ! -e "$STORE" ]]; then
    warn "No codegraph store found — nothing to remove"
    warn "  Expected: ~/.codegraph/<key>/ or CODEGRAPH_HOME"
  else
    local_home="${CODEGRAPH_HOME:-$HOME/.codegraph}"
    if [[ "$STORE" == "$local_home" ]]; then
      whole_home=true
    else
      whole_home=false
    fi

    if $whole_home; then
      echo -e "  ${DIM}Removing:  $STORE (entire codegraph home)${NC}"
      n_stores=$(/bin/ls -d "$local_home"/*/ 2>/dev/null | wc -l | tr -d ' ')
      [[ -z "$n_stores" || "$n_stores" -eq 0 ]] && n_stores="0"
      echo -e "  ${DIM}Workspaces: $n_stores${NC}"
    else
      echo -e "  ${DIM}Store:     $STORE${NC}"
    fi
    echo -e "  ${DIM}Contains:  .kg (SQLite), .knowledge/ (OKF bundle)${NC}"
    echo -e "  ${DIM}Size:$(du -sh "$STORE" 2>/dev/null | cut -f1 || echo ' unknown')${NC}"
    echo ""

    if confirm; then
      if $DRY_RUN; then
        echo -e "  ${DIM}Would rm -rf $STORE${NC}"
      else
        rm -rf "$STORE"

        # Drop stale workspace entries from workspaces.json (whole-home removal
        # also wipes workspaces.json with the directory; single-store removal
        # prunes just that workspace's entry).
        if ! $whole_home && [[ -f "$local_home/workspaces.json" ]]; then
          "$PYTHON" -c "
import json
path = '$local_home/workspaces.json'
try:
    w = json.load(open(path))
    cleaned = {k: v for k, v in w.items() if v != '$WORKSPACE'}
    if len(cleaned) < len(w):
        json.dump(cleaned, open(path, 'w'), indent=2)
        print('Cleaned workspaces.json')
except Exception:
    pass
" 2>/dev/null || uv run python -c "
import json
path = '$local_home/workspaces.json'
try:
    w = json.load(open(path))
    cleaned = {k: v for k, v in w.items() if v != '$WORKSPACE'}
    if len(cleaned) < len(w):
        json.dump(cleaned, open(path, 'w'), indent=2)
        print('Cleaned workspaces.json')
except Exception:
    pass
" 2>/dev/null || true
        fi
      fi
      if $whole_home; then
        ok "Graph and knowledge data removed (entire $local_home)"
      else
        ok "Graph and knowledge data removed"
      fi
    else
      warn "Skipped"
    fi
  fi
fi

# ─── Step 4: Uninstall cg binary ───────────────────────────────────────────
if $DO_PACKAGE; then
  info "cg binary"

  CG_PATH="$(command -v cg 2>/dev/null || true)"
  if [[ -z "$CG_PATH" ]]; then
    CG_PATH="$PROJECT_DIR/.venv/bin/cg"
  fi

  INSTALLED_VIA=""
  if command -v uv >/dev/null 2>&1; then
    if uv tool list 2>/dev/null | grep -q "cg-intel"; then
      INSTALLED_VIA="uv"
    fi
  fi
  if [[ -z "$INSTALLED_VIA" ]] && command -v pipx >/dev/null 2>&1; then
    if pipx list 2>/dev/null | grep -q "cg-intel"; then
      INSTALLED_VIA="pipx"
    fi
  fi
  if [[ -z "$INSTALLED_VIA" && -d "$PROJECT_DIR/.venv" ]]; then
    INSTALLED_VIA="venv"
  fi
  if [[ -z "$INSTALLED_VIA" ]] && [[ -n "$PYTHON" ]] && "$PYTHON" -m pip show cg-intel >/dev/null 2>&1; then
    INSTALLED_VIA="pip"
  fi

  # Stale build/, dist/, *.egg-info left in-tree by `pip install -e .` /
  # `uv tool install` (see install.sh's own pre-clean step for why these
  # can go stale) — clean these up regardless of INSTALLED_VIA so a
  # "removed" cg doesn't leave packaging debris behind.
  STALE_ARTIFACTS=()
  for p in "$PROJECT_DIR"/build "$PROJECT_DIR"/dist "$PROJECT_DIR"/*.egg-info; do
    [[ -e "$p" ]] && STALE_ARTIFACTS+=("$p")
  done

  if [[ -z "$INSTALLED_VIA" && ${#STALE_ARTIFACTS[@]} -eq 0 ]]; then
    warn "cg not found via uv, pipx, pip, or venv — nothing to remove"
  else
    if [[ -n "$INSTALLED_VIA" ]]; then
      echo -e "  ${DIM}Installed via: $INSTALLED_VIA${NC}"
    fi
    if [[ ${#STALE_ARTIFACTS[@]} -gt 0 ]]; then
      echo -e "  ${DIM}Stale build artifacts: ${STALE_ARTIFACTS[*]}${NC}"
    fi
    echo ""

    if confirm; then
      case "$INSTALLED_VIA" in
        uv)
          if $DRY_RUN; then
            echo -e "  ${DIM}Would run: uv tool uninstall cg-intel${NC}"
          else
            uv tool uninstall cg-intel 2>/dev/null || true
          fi
          ;;
        pipx)
          if $DRY_RUN; then
            echo -e "  ${DIM}Would run: pipx uninstall cg-intel${NC}"
          else
            pipx uninstall cg-intel 2>/dev/null || true
          fi
          ;;
        venv)
          if $DRY_RUN; then
            echo -e "  ${DIM}Would rm -rf $PROJECT_DIR/.venv${NC}"
          else
            rm -rf "$PROJECT_DIR/.venv"
          fi
          ;;
        pip)
          if $DRY_RUN; then
            echo -e "  ${DIM}Would run: $PYTHON -m pip uninstall -y cg-intel${NC}"
          else
            "$PYTHON" -m pip uninstall -y cg-intel 2>/dev/null || true
          fi
          ;;
      esac

      if [[ ${#STALE_ARTIFACTS[@]} -gt 0 ]]; then
        if $DRY_RUN; then
          echo -e "  ${DIM}Would rm -rf ${STALE_ARTIFACTS[*]}${NC}"
        else
          rm -rf "${STALE_ARTIFACTS[@]}"
        fi
      fi

      ok "cg binary removed${INSTALLED_VIA:+ ($INSTALLED_VIA)}"
    else
      warn "Skipped"
    fi
  fi
fi

# ─── Summary ────────────────────────────────────────────────────────────────
echo ""
if $DRY_RUN; then
  echo -e "${BOLD}Dry-run complete — nothing was deleted${NC}"
else
  echo -e "${BOLD}Uninstall complete${NC}"
fi
echo ""
echo "  Remaining (never removed by this script):"
echo "    CLAUDE.md, AGENTS.md  — instruction files (created create-if-absent only)"
echo "    Source code            — your repos are untouched"
echo ""
echo "  To reinstall:"
echo "    ./scripts/install.sh"

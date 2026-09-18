/**
 * Pure status-display mapping for the shell's status-bar item: server states
 * in, one aggregate indicator out. No editor API here so the mapping is
 * testable under plain Node.
 */
import type { ServerState } from "./server";

export interface StatusDisplay {
  text: string;
  tooltip: string;
}

/**
 * The root needing attention wins: offline > unindexed > connecting > indexed.
 * The first two inputs are the degraded states every surface reads (FR-004);
 * degradation derives from this one aggregate, never per-provider.
 */
export function worstState(states: ServerState[]): ServerState {
  const rank = (state: ServerState): number => {
    switch (state.phase) {
      case "offline":
        return 0;
      case "served":
        return state.index === "indexed" ? 3 : state.index === "unindexed" ? 1 : 2;
      case "connecting":
        return 2;
    }
  };
  return states.reduce((worst, state) => (rank(state) < rank(worst) ? state : worst));
}

/** True while inline data is withheld: the worst root is offline, connecting, or unindexed (FR-004). Every surface reads this one verdict. */
export function isDegraded(states: ServerState[]): boolean {
  if (states.length === 0) return false;
  const worst = worstState(states);
  return worst.phase !== "served" || worst.index === "unindexed";
}

export function statusDisplay(state: ServerState): StatusDisplay {
  const detail = state.detail ? ` — ${state.detail}` : "";
  switch (state.phase) {
    case "served":
      if (state.index === "indexed") {
        return { text: "$(check) cairn", tooltip: "cairn: connected — workspace indexed" };
      }
      if (state.index === "unindexed") {
        return {
          text: "$(circle-slash) cairn: unindexed",
          tooltip: "cairn: local server running, workspace not indexed — inline data appears once the workspace has a cairn graph",
        };
      }
      return {
        text: "$(plug) cairn",
        tooltip: "cairn: local server attached — index state unknown",
      };
    case "connecting":
      return { text: "$(sync~spin) cairn", tooltip: "cairn: starting the local server" };
    case "offline":
      return {
        text: "$(circle-slash) cairn: offline",
        tooltip: `cairn: local server unreachable — the editor stays usable, inline data pauses${detail}`,
      };
  }
}

export function aggregateDisplay(states: ServerState[]): StatusDisplay {
  return statusDisplay(worstState(states));
}

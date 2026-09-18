/**
 * Status-bar item rendering the shell's aggregate server/index state.
 * Non-intrusive by contract: text-only indicator, never a dialog (FR-004).
 */
import * as vscode from "vscode";
import type { ServerState } from "./server";
import { aggregateDisplay } from "./statusModel";

export function createStatusBarItem(): vscode.StatusBarItem {
  const item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 50);
  item.name = "cairn code intelligence";
  return item;
}

export function renderStatus(item: vscode.StatusBarItem, states: ServerState[]): void {
  if (states.length === 0) {
    item.hide();
    return;
  }
  const display = aggregateDisplay(states);
  item.text = display.text;
  item.tooltip = display.tooltip;
  item.show();
}

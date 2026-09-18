/**
 * Compass/memory panel for the active file (FR-002): one file-endpoint
 * request per active-file change rendered into a dockable webview. The
 * shell's aggregate degradation verdict swaps data for a status note and
 * empty content renders the empty state — never an error surface (FR-004).
 */
import * as vscode from "vscode";
import type { ServerManager, ServerState } from "../server";
import {
  createLatestWinsRunner,
  defaultFileFetcher,
  degradedNote,
  panelView,
  renderPanelHtml,
  type FilePanelPayload,
} from "./compassModel";

export interface CompassPanelHandle {
  /** Re-renders (and re-requests while served) on a shell state change. */
  onStates(states: ServerState[]): void;
}

/** Workspace-relative POSIX path for a document, or null outside a workspace folder. */
function resolveRelativePath(uri: vscode.Uri): string | null {
  if (uri.scheme !== "file") return null;
  if (!vscode.workspace.getWorkspaceFolder(uri)) return null;
  return vscode.workspace.asRelativePath(uri, false).replace(/\\/g, "/");
}

export function registerCompassPanel(
  context: vscode.ExtensionContext,
  manager: ServerManager,
): CompassPanelHandle {
  let panel: vscode.WebviewPanel | null = null;
  let activePath: string | null = null;
  let payload: FilePanelPayload | null = null;

  const ensurePanel = (): vscode.WebviewPanel => {
    if (panel) return panel;
    const created = vscode.window.createWebviewPanel(
      "cairnCompass",
      "cairn compass",
      { viewColumn: vscode.ViewColumn.Beside, preserveFocus: true },
      { enableScripts: false },
    );
    created.onDidDispose(() => {
      panel = null;
    });
    context.subscriptions.push(created);
    panel = created;
    return created;
  };

  const render = (): void => {
    if (!panel) return;
    panel.webview.html = renderPanelHtml(
      panelView(activePath === null ? null : payload, degradedNote(manager.states())),
    );
  };

  const runner = createLatestWinsRunner(defaultFileFetcher, (path, result) => {
    if (path !== activePath) return;
    payload = result;
    render();
  });

  const refresh = (): void => {
    const editor = vscode.window.activeTextEditor;
    if (!editor) return;
    const path = resolveRelativePath(editor.document.uri);
    if (path !== activePath) {
      activePath = path;
      payload = null;
    }
    if (activePath === null) {
      render();
      return;
    }
    ensurePanel();
    if (degradedNote(manager.states()) === null) runner.request(activePath);
    render();
  };

  context.subscriptions.push(vscode.window.onDidChangeActiveTextEditor(() => refresh()));
  refresh();
  return { onStates: () => refresh() };
}

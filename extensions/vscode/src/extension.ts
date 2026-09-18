/**
 * Extension shell activation: start or attach the local cairn server per
 * workspace root (D-003), reflect the aggregate state in one status-bar
 * item, register the inline hover and code-lens providers, and host the
 * active-file compass/memory panel. Zero manual configuration — activation
 * alone brings the server up.
 */
import * as vscode from "vscode";
import { ServerManager, type ServerState } from "./server";
import { createStatusBarItem, renderStatus } from "./status";
import { registerCompassPanel } from "./panel/compass";
import { SymbolHoverProvider } from "./providers/hover";
import { registerCodeLenses } from "./providers/codeLens";

export function activate(context: vscode.ExtensionContext): void {
  const item = createStatusBarItem();
  const manager = new ServerManager();
  const states = (): ServerState[] => manager.states();
  const compassPanel = registerCompassPanel(context, manager);

  // Hover: one /editor/symbol round-trip per hover; degraded shells render nothing (FR-004).
  context.subscriptions.push(
    vscode.languages.registerHoverProvider(
      { scheme: "file" },
      new SymbolHoverProvider({
        states,
        hover: (markdown) => ({ contents: [new vscode.MarkdownString(markdown)] }),
      }),
    ),
  );

  // Code lens: caller/callee counts above declarations; hidden while degraded (FR-004).
  registerCodeLenses({ context, vscodeApi: vscode, states });

  manager.onStateChange((next) => {
    renderStatus(item, next);
    compassPanel.onStates(next);
  });
  renderStatus(item, manager.states());

  const ensureAll = (): void => {
    for (const folder of vscode.workspace.workspaceFolders ?? []) {
      void manager.ensure(folder.uri.fsPath);
    }
  };

  context.subscriptions.push(
    item,
    manager,
    vscode.workspace.onDidChangeWorkspaceFolders((event) => {
      for (const removed of event.removed) manager.remove(removed.uri.fsPath);
      ensureAll();
    }),
  );
  ensureAll();
}

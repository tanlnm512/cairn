# cairn for VS Code — quick start

Zero-setup codebase intelligence: hover blast radius, caller/callee lenses,
and compass/memory for your workspace, served by a local process the
extension manages for you.

## Install and use

1. Install the extension — from the marketplace, or the manual path:
   `code --install-extension <downloaded>.vsix`
2. Open a workspace that cairn has indexed.
3. Done. On activation the extension connects to the local cairn process on
   `127.0.0.1:8765` — adopting one that is already running, or starting one
   for the workspace root itself. No tokens, no config files, no manual
   steps of any kind.

## Reading the status item

The item in the status bar is the one surface that always tells the truth:

- `$(check) cairn` — connected; the workspace graph is available inline.
- `$(sync~spin) cairn` — the local process is starting or being adopted.
- `$(circle-slash) cairn: unindexed` — the workspace has no cairn graph yet;
  inline data appears once cairn has indexed the workspace. The editor stays
  fully usable meanwhile.
- `$(circle-slash) cairn: offline` — the local process is unreachable; hover
  and lens content pause until it answers again. Hover the item for the
  reason.

## How it runs

- One local process serves each workspace root; multi-root workspaces get
  one per root. A process that is already running is adopted, never
  duplicated, and one the extension started is stopped when the editor
  session ends.
- The process is loopback-only and read-only: it never writes your graph.
- All editor features degrade to the status item — the extension never
  blocks typing, navigation, or scrolling.

# cairn — code intelligence for VS Code

Codebase intelligence from the local cairn server, inline in the editor:
blast radius, compass context, and project memory with zero manual
configuration.

## Features

- **Symbol hover** — precise caller/callee counts and a depth-limited blast
  radius for the symbol under the cursor.
- **Code lens** — caller/callee counts above declarations.
- **Compass panel** — the active file's module compass excerpt and relevant
  memories in a dockable webview, refreshed as you switch files.
- **Status item** — one honest indicator for the local server's state:
  connected, starting, unindexed workspace, or offline.

## Requirements

- The `cairn` CLI on `PATH` (the extension starts or adopts its local server).
- A workspace that cairn has indexed for inline data; unindexed workspaces
  degrade to the status item and never block the editor.

## Install

From the downloaded package:

```
code --install-extension cairn-ext.vsix
```

Then open an indexed workspace — activation connects to the local cairn
process on `127.0.0.1:8765`, adopting one that is already running or
starting one for the workspace root. No tokens, no config files. See
[QUICKSTART.md](QUICKSTART.md) for the full walkthrough.

## How it runs

- One local process per workspace root; already-running processes are
  adopted, never duplicated.
- The process is loopback-only and read-only: it never writes your graph.
- Every editor feature degrades to the status item when the server is
  stopped or the workspace is unindexed.

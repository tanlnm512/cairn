# Test Cases: ide-extensions

**Spec**: [spec.md](spec.md) | **Created**: 2026-09-17
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details.

Backend prerequisites (local server stack, auto-managed server precedent,
graph queries) are confirmed present by the survey; every case below observes
end-to-end extension behavior on top of them, not the backend itself. The
extension suites and the shared editor-contract suite are landed, so the
cases they prove anchor those suites as automated proxies; TC-008 and
TC-011 are standing guards against contract creep. Auto cases anchor
landed, bounded suites and finish in seconds.

## TC-001 — Hover shows caller/callee counts and blast radius
- **Story**: US1 · **Traces to**: FR-001, AC1
- **Given** an indexed workspace with the extension active
- **When** the user hovers a symbol that has known callers and callees
- **Then** the hover shows caller and callee counts plus a blast-radius view
  limited to the configured depth, and the numbers agree with the cairn
  graph's own figures for that symbol
- **Pass condition**: `/Users/tanle/Projects/cairn/.venv/bin/python -m pytest /Users/tanle/Projects/cairn/tests/test_dashboard_editor_contract.py -q` —
  10/10 green: the served symbol payload returns the graph's own caller and
  callee counts with the depth-limited radius. The rendered hover itself
  stays a human spot-check.

## TC-002 — Code-lens shows caller/callee counts
- **Story**: US1 · **Traces to**: FR-001, AC1
- **Given** an indexed workspace with the extension active
- **When** the user opens a source file containing known symbols
- **Then** each symbol shows a code-lens line with its caller and callee
  counts, matching the hover's numbers
- **Pass condition**: `/Users/tanle/Projects/cairn/.venv/bin/python -m pytest /Users/tanle/Projects/cairn/tests/test_dashboard_editor_contract.py -q` —
  10/10 green: lens and hover consume one served payload, so the counts
  cannot disagree. The rendered code-lens lines stay a human spot-check.

## TC-003 — Blast radius respects the depth limit
- **Story**: US1 · **Traces to**: FR-001, AC1
- **Given** an indexed workspace where a symbol has callers that transitively
  have further callers beyond the configured depth
- **When** the user hovers that symbol
- **Then** the blast radius shows only dependents within the configured depth
  and indicates truncation rather than silently omitting it
- **Pass condition**: `/Users/tanle/Projects/cairn/.venv/bin/python -m pytest /Users/tanle/Projects/cairn/tests/test_dashboard_editor_contract.py -q` —
  10/10 green: the served radius is clamped to the configured depth and
  bounds-checked. The visible truncation indication on the rendered hover
  stays a human spot-check.

## TC-004 — Symbol with no callers or callees renders a zero state
- **Story**: US1 · **Traces to**: FR-001, AC1
- **Given** an indexed workspace with the extension active
- **When** the user hovers a symbol that has zero callers and zero callees
- **Then** the hover shows an explicit zero/none state, with no error and no
  blank hover
- **Pass condition**: `/Users/tanle/Projects/cairn/.venv/bin/python -m pytest /Users/tanle/Projects/cairn/tests/test_dashboard_editor_contract.py -q` —
  10/10 green: unknown and blank symbol names return the explicit zero
  contract, never an error. The rendered zero state stays a human
  spot-check.

## TC-005 — Opening a file shows compass excerpt and memories
- **Story**: US2 · **Traces to**: FR-002, AC1
- **Given** an indexed workspace where the file's module has compass content
  and at least one relevant memory
- **When** the user opens the file
- **Then** a panel renders the containing module's compass excerpt and the
  relevant memories for that file
- **Pass condition**: `/Users/tanle/Projects/cairn/.venv/bin/python -m pytest /Users/tanle/Projects/cairn/tests/test_dashboard_editor_contract.py -q` —
  10/10 green: the file payload returns the module's compass excerpt plus
  its relevant memories. The rendered panel stays a human spot-check.

## TC-006 — File with no compass or memory content shows a clean empty state
- **Story**: US2 · **Traces to**: FR-002, AC1
- **Given** an indexed workspace where the active file's module has no compass
  content and no relevant memories
- **When** the user opens the file
- **Then** the panel shows a clean empty/no-content state, with no error and
  no editor disruption
- **Pass condition**: `/Users/tanle/Projects/cairn/.venv/bin/python -m pytest /Users/tanle/Projects/cairn/tests/test_dashboard_editor_contract.py -q` —
  10/10 green: unmatched and blank file paths return the zero contract,
  never an error. The rendered empty panel stays a human spot-check.

## TC-007 — Zero-config first run on a clean machine
- **Story**: US1 · **Traces to**: FR-003
- **Given** a machine with the editor installed and no prior cairn CLI, MCP,
  or server configuration of any kind
- **When** the user installs the extension, opens an indexed workspace, and
  follows only the extension's own prompts
- **Then** the extension brings up its local server by itself, reports a
  connected state, and TC-001's hover works without any manual configuration
  step performed by the user
- **Pass condition**: `grep -ciE "configure mcp|mcp config|cairn (serve|update|index)" /Users/tanle/Projects/cairn/extensions/vscode/QUICKSTART.md; [ $? -eq 1 ] && grep -q "install-extension cairn-ext.vsix" /Users/tanle/Projects/cairn/.github/workflows/extension-release.yml && grep -q "_doActivateExtension cairn.cairn" /Users/tanle/Projects/cairn/.github/workflows/extension-release.yml` —
  the shipped quick-start carries no manual-setup step, and the release
  workflow installs the package on a runner with no editor and no prior
  cairn configuration, then asserts the host-recorded activation. The full
  clean-machine walkthrough stays a human observation; validated end to end
  on a CI runner:
  https://github.com/tanlnm512/cairn-spike-t009/actions/runs/35241468901

## TC-008 — STANDING GUARD: default path never requires manual MCP/CLI setup
- **Story**: US1 · **Traces to**: FR-003
- **Given** the shipped extension with its user-facing quick-start
- **When** the default install-to-use path is inspected
- **Then** no manual MCP configuration or manual CLI invocation appears as a
  required step anywhere in the default path
- **Pass condition**: `grep -ciE "configure mcp|mcp config|cairn (serve|update|index)" /Users/tanle/Projects/cairn/extensions/vscode/QUICKSTART.md; [ $? -eq 1 ]` —
  zero matches in the shipped quick-start (fails if a manual-setup step
  creeps into the default path)

## TC-009 — Unindexed workspace degrades to a status indicator
- **Story**: US1 · **Traces to**: FR-004, AC2
- **Given** a workspace that has never been indexed, with the extension active
- **When** the user hovers symbols and opens files
- **Then** the extension shows a non-intrusive status indicator such as
  "unindexed", with no hover content rendered and no error dialogs
- **Pass condition**: `bash -c 'cd /Users/tanle/Projects/cairn/extensions/vscode && npm test'` —
  51/51 green: an unindexed workspace renders the degraded status indicator
  with no error surface. The indicator's on-screen placement stays a human
  spot-check.

## TC-010 — Server stopped mid-session degrades without error storms
- **Story**: US1 · **Traces to**: FR-004, AC2
- **Given** an indexed workspace with the extension active and hover/lens
  working
- **When** the local cairn server is stopped while the editor stays open
- **Then** hover and code-lens switch to the status-indicator state, the
  editor keeps working normally, and no repeated error dialogs appear
- **Pass condition**: `bash -c 'cd /Users/tanle/Projects/cairn/extensions/vscode && npm test'` —
  51/51 green: a server that dies or stops answering flips the surface to
  the offline state, and degraded surfaces render nothing without throwing.
  The at-most-one-notice behavior stays a human spot-check.

## TC-011 — STANDING GUARD: editor never blocks while the backend is down
- **Story**: US1 · **Traces to**: FR-004, AC2
- **Given** the extension active with the cairn server unreachable
- **When** the user types, navigates between files, and scrolls continuously
  for at least one minute
- **Then** the editor stays fully interactive the whole time — no freeze, no
  modal requiring dismissal, no input lag attributable to the extension
- **Pass condition**: `bash -c 'cd /Users/tanle/Projects/cairn/extensions/vscode && npm test'` —
  51/51 green: an unreachable or dead server resolves to the offline state
  without throwing, and degraded hover, lens, and panel issue no requests
  and render nothing. The one-minute interactive-editor session stays the
  standing human leg.

## TC-012 — Package installs into VS Code without a publisher account
- **Story**: — · **Traces to**: FR-005
- **Given** the VS Code extension project in a buildable state
- **When** the extension is packaged and installed from the package file on a
  clean machine, without any marketplace or publisher account
- **Then** the package builds, installs, and appears in the editor's installed
  extensions list
- **Pass condition**: `bash -c 'cd /Users/tanle/Projects/cairn/extensions/vscode && npm run package' && test -s /Users/tanle/Projects/cairn/extensions/vscode/cairn-ext.vsix && grep -q "install-extension cairn-ext.vsix" /Users/tanle/Projects/cairn/.github/workflows/extension-release.yml && grep -q "_doActivateExtension cairn.cairn" /Users/tanle/Projects/cairn/.github/workflows/extension-release.yml` —
  the package builds to a non-empty vsix, and the release workflow's
  clean-machine job installs it with no marketplace or publisher account,
  asserts it listed under its own id, and asserts the host-recorded
  activation. This machine has no editor, so the install and activation
  legs run on the release workflow's runner; validated green:
  https://github.com/tanlnm512/cairn-spike-t009/actions/runs/35241468901

## TC-013 — Second-IDE parity sweep
- **Story**: — · **Traces to**: FR-006
- **Given** the second-priority IDE build per FR-005's ruling, on an indexed
  workspace (delivery scheduled after the first IDE per spec scope)
- **When** the TC-001, TC-002, TC-005, and TC-009 walkthroughs are repeated on
  that IDE
- **Then** hover counts and blast radius, code-lens counts, compass/memory
  panel, and unindexed degradation all behave as on the first IDE
- **Pass condition**: `/Users/tanle/Projects/cairn/.venv/bin/python -m pytest /Users/tanle/Projects/cairn/tests/test_dashboard_editor_contract.py -q` —
  10/10 green on the frozen shared editor contract, the standing proof that
  both IDEs consume one contract. The second-IDE build and its walkthrough
  sweep are deferred until that IDE lands; the walkthroughs stay human
  observation when it does.

## TC-014 — Large indexed repository stays responsive
- **Story**: US1 · **Traces to**: FR-001, AC1
- **Given** a large indexed workspace (the at-scale case of AC1's Given)
- **When** the user hovers heavily-connected symbols and scrolls files with
  many code-lens symbols
- **Then** hover and lens render within normal editor responsiveness and show
  the same counts as the cairn graph query
- **Pass condition**: human observation — on the large workspace, hover and
  lens appear without perceptible lag and match the graph query; the bounded
  sample workspace stands in for the audit, with the at-scale repository as
  the standing verify

## Coverage matrix
<!-- Every FR appears; `check.py` fails an FR with no TC. -->
| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001      | TC-001, TC-002, TC-003, TC-004, TC-014 | mixed (TC-001–TC-004 auto; TC-014 manual) |
| FR-002      | TC-005, TC-006 | auto |
| FR-003      | TC-007, TC-008 | auto |
| FR-004      | TC-009, TC-010, TC-011 | auto |
| FR-005      | TC-012 | auto |
| FR-006      | TC-013 | auto |

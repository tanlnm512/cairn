# Security Policy

## Supported versions

cairn is pre-1.0 beta software. Only the **latest `0.6.x` release line**
receives security fixes. Older versions are not supported — please upgrade.

| Version | Supported |
|---------|-----------|
| 0.6.x   | Yes       |
| < 0.6   | No        |

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security reports. Instead,
open a private security advisory via GitHub's Security tab:

**https://github.com/tanlnm512/cairn/security/advisories/new**

## Response expectations

- **Acknowledgement:** within **72 hours** of the report.
- **Fix target:** within **30 days** for verified vulnerabilities, sooner for
  high-severity issues. You'll be kept in the loop on timing and credited in the
  advisory unless you prefer otherwise.

## In scope

cairn runs an **MCP server** (`cairn serve`) and parses **untrusted source
code** via tree-sitter. The following are in scope:

- Parser denial-of-service: malformed/pathological source files causing memory
  exhaustion, infinite loops, or crashes during indexing.
- `cairn serve` / MCP transport issues: unauthenticated endpoints, request
  amplification, or crashes triggered by malicious tool input.
- Path traversal or arbitrary file read/write outside the indexed workspace.
- Injection via `.knowledge/` markdown or graph data being rendered/executed
  unsafely.

The `[semantic]` extra pulls **torch + sentence-transformers** (a large
dependency tree). Treat **dependency/supply-chain** vulnerabilities in that set
as a separate category — report them here so we can track and pin, but the
upstream package is the canonical fix venue.

## Out of scope

- Vulnerabilities in third-party dependencies themselves — report upstream
  (e.g. PyTorch, sentence-transformers, tree-sitter grammars). Do let us know
  so we can pin or document a workaround.
- Issues already disclosed publicly (blog posts, public issues, CVEs).
- Bugs with no security impact (use regular GitHub Issues — see CONTRIBUTING.md).
- Theoretical issues with no realistic exploit path against a default install.

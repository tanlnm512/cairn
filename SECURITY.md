# Security Policy

## Supported versions

cairn is pre-1.0 beta software. Only the **latest release line** receives
security fixes. Older versions are not supported — please upgrade.

| Version | Supported |
|---------|-----------|
| Latest release line | Yes |
| Older lines | No |

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
  amplification, or crashes triggered by malicious tool input. Keyless
  serving is confined to loopback binds: a non-loopback bind requires an API
  key, enforced both at the bind and before any daemon start writes its
  launch configuration. Write tools (`record_memory`, `knowledge_add`,
  `knowledge_delete`, `wiki_generate`) additionally require a key or an
  explicit `serve --allow-writes` — keyless loopback serves reads only.
  The dashboard rejects any request whose Host header is not loopback (DNS
  rebinding), and `/healthz` discloses the store directory only to
  authenticated requesters (everyone else sees the file name and a `store_id`
  identity hash).
- Path traversal or arbitrary file read/write outside the indexed workspace.
- Injection via `.knowledge/` markdown or graph data being rendered/executed
  unsafely. Task-queue results (wiki/compass/catalog/memory) and their facts
  are scrubbed of secret-shaped substrings before storage, so injected
  credentials never reach `ask_compass` or the knowledge bundle.

The Rust dependency tree (tree-sitter grammars, sqlite-vec, crypto and HTTP
crates) is vendored/pinned and gated by cargo-deny. Treat
**dependency/supply-chain** vulnerabilities in that set as a separate
category — report them here so we can track and pin, but the upstream
package is the canonical fix venue. Optional semantic search talks to an
external embeddings server you configure; opt-in native embedding ships as
the `local-embed` build feature.

## Out of scope

- Vulnerabilities in third-party dependencies themselves — report upstream
  (e.g. tree-sitter grammars, the embeddings backend you run). Do let us know
  so we can pin or document a workaround.
- Issues already disclosed publicly (blog posts, public issues, CVEs).
- Bugs with no security impact (use regular GitHub Issues — see CONTRIBUTING.md).
- Theoretical issues with no realistic exploit path against a default install.

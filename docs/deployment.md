# Deployment: HTTP serving

← [README](../README.md)

Read this when you want to serve one cairn store to MCP clients over HTTP —
locally, across a network, or from a container.

`cairn serve` runs the MCP daemon: the full tool registry over Streamable
HTTP with bearer-key auth, read-write by default. The stdio and SSE
transports are gone — stdio/SSE-specific options (including any
`--transport` value) exit non-zero with a message naming the HTTP
replacement. `cairn connect` ensures the daemon is running and prints its
URL, and `cairn doctor`'s `daemon` check reports what is serving.

## Serve surface

All flags below work on both `cairn serve` and `cairn serve run`:

| Flag | Contract |
|---|---|
| `--host` | bind address, default `127.0.0.1` (loopback) |
| `--port` | listen port, default `9876` |
| `--api-key` | bearer key; takes precedence over the `CAIRN_MCP_API_KEY` environment variable |
| `--stateless` | no server-side session affinity — sequential session-less requests succeed independently, so a load balancer can distribute requests across replicas |
| `--read-only` | the daemon is read-write by default; `--read-only` opens the store read-only so the write tools cannot land |

`cairn serve start\|stop\|status\|restart` manage the daemon — the macOS
launchd LaunchAgent (the plist execs the `cairn` binary), or a daemonized
background process elsewhere. `cairn connect` is the lighter entrypoint: it
starts the daemon on first dial and reuses a healthy one (its reuse-guard
reads `/healthz`).

## Key policy

- The key resolves from the `--api-key` flag, else the `CAIRN_MCP_API_KEY`
  environment variable; the flag wins.
- `cairn init` generates and persists a key once under `CAIRN_HOME`;
  generated client configs embed it, and it stays dormant while the loopback
  daemon runs keyless.
- Keyless operation is allowed only on loopback binds. A non-loopback
  `--host` without a key refuses to start with a clear error (exit code 1,
  before binding).
- Keyless loopback serves without the auth middleware.
- Prefer `CAIRN_MCP_API_KEY` over `--api-key`: the flag is visible in
  process listings (`ps`). The key never appears in cairn's logs or boot
  errors.

## Bind classes and transport security

| Bind | Host-header (DNS-rebinding) protection | Key required |
|---|---|---|
| `127.0.0.1` (default) | on (loopback allowlist) | no |
| specific non-loopback host | on (bind host allowlisted) | yes |
| wildcard (`0.0.0.0`, `::`) | off by design — Host values cannot be enumerated, so the key is the sole gate | yes |

Operators who need Host-header protection on a network bind should bind a
specific host or front a reverse proxy.

## Health probe

`GET /healthz` requires no auth and returns HTTP 200 with a bounded JSON
payload:

```json
{"status": "ok", "store_reachable": true, "read_only": false, "degradations": 0}
```

`status` is `unhealthy` when the store is unreachable (still HTTP 200);
`degradations` counts degraded subsystem probes. The payload carries no
other fields.

## Local serving (loopback, keyless)

```
cairn connect   # or: cairn serve
```

MCP clients connect to `http://127.0.0.1:9876/mcp` with no auth header.

## Networked serving (key required)

```
CAIRN_MCP_API_KEY=<key> cairn serve --host 0.0.0.0
```

Clients connect to `http://<host>:9876/mcp` and send
`Authorization: Bearer <key>` on every request except `GET /healthz`.

cairn does not terminate TLS — front it with a reverse proxy for HTTPS and
proxy to the plain-HTTP listener. With `--stateless`, requests carry no
session state, so replicas behind a load balancer need no affinity.

## Container serving

Two image shapes serve the same store contract.

**Static binary image** — the Linux release tarballs are musl-static, so a
minimal base image suffices:

```
FROM alpine:3.20
ARG CAIRN_VERSION
ADD "https://github.com/tanlnm512/cairn/releases/download/v${CAIRN_VERSION}/cairn-cli-${CAIRN_VERSION}-x86_64-unknown-linux-musl.tar.gz" /tmp/
RUN tar -xzf /tmp/cairn-cli-*.tar.gz -C /usr/local/bin && rm -rf /tmp/cairn-cli-*
ENV CAIRN_HOME=/data
RUN adduser -D -u 1000 cairn && mkdir -p /data && chown cairn /data
VOLUME /data
USER cairn
EXPOSE 9876
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD ["wget", "-qO-", "http://127.0.0.1:9876/healthz"]
ENTRYPOINT ["cairn", "serve", "--host", "0.0.0.0"]
```

**The checked-in Dockerfile** builds the binary from the `rust/` workspace
in a `rust` build stage and ships it alone on `debian:bookworm-slim`:

```
docker build -t cairn .
docker run -d --name cairn -p 9876:9876 \
  -e CAIRN_MCP_API_KEY=<key> \
  -v cairn-data:/data \
  cairn
```

The image runs as a dedicated non-root user, its entrypoint is
`cairn serve --host 0.0.0.0` with `CAIRN_HOME=/data` and `EXPOSE 9876`, and
its `HEALTHCHECK` curls `/healthz`. The key comes from the environment: a
keyless container on the wildcard entrypoint exits nonzero at boot instead
of serving unprotected.

- The store lives under `CAIRN_HOME=/data`; mount a **local** volume, or
  point `CAIRN_DB` / `CAIRN_KNOWLEDGE` at existing store files. Never mount
  the store over NFS or another network filesystem — SQLite corrupts under
  multi-host access.
- The container serves plain HTTP on 9876; terminate TLS at a reverse proxy
  in front of it.
- Check readiness with `curl -f http://localhost:9876/healthz`.

## Pen-test checklist

- [ ] Default bind: `cairn serve` with no `--host` listens on `127.0.0.1` only.
- [ ] Refuse-to-start: a non-loopback `--host` without a key exits nonzero before binding.
- [ ] Auth gate: a missing or wrong `Authorization: Bearer` credential is rejected (401) on every endpoint except `GET /healthz`, before any store access.
- [ ] Exempt set: `/healthz` is the only unauthenticated path.
- [ ] Host header: a forged `Host` is rejected on loopback and specific-host binds; on wildcard binds protection is off by design and the key is the gate — confirm every unauthenticated request is rejected.
- [ ] Secret hygiene: no key material in logs, boot errors, or process listings.
- [ ] Health payload: `/healthz` exposes nothing beyond `status`, `store_reachable`, `read_only`, `degradations`.
- [ ] Storage: the store sits on a local volume, never a network filesystem.
- [ ] TLS: plaintext HTTP never leaves the host unproxied.

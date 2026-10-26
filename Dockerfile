# Build the engine binary from the rust workspace, then ship it alone: the
# daemon needs no Python.
FROM rust:1-slim-bookworm AS build
WORKDIR /src
COPY Cargo.toml Cargo.lock ./
COPY rust/ ./rust/
RUN cargo build --release -p cairn-cli

FROM debian:bookworm-slim

RUN useradd --create-home --uid 1000 cairn \
    && apt-get update -qq \
    && apt-get install -y --no-install-recommends ca-certificates curl git \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /src/target/release/cairn /usr/local/bin/cairn

# Store home lives on the mounted volume.
ENV CAIRN_HOME=/data
RUN mkdir -p /data && chown cairn:cairn /data
VOLUME /data

USER cairn
EXPOSE 9876

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["curl", "-sf", "http://127.0.0.1:9876/healthz"]

# Key comes from the CAIRN_MCP_API_KEY env var; without it the wildcard bind
# refuses to start (keyless serving is loopback-only).
ENTRYPOINT ["cairn", "serve", "--host", "0.0.0.0"]

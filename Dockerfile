FROM python:3.12-slim

RUN useradd --create-home --uid 1000 cairn

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

# Store home lives on the mounted volume; PYTHONUNBUFFERED keeps lifecycle
# milestones visible in container logs.
ENV CAIRN_HOME=/data \
    PYTHONUNBUFFERED=1
RUN mkdir -p /data && chown cairn:cairn /data
VOLUME /data

USER cairn
EXPOSE 9876

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request as u; u.build_opener(u.ProxyHandler({})).open('http://127.0.0.1:9876/healthz', timeout=4)"]

# Key comes from the CAIRN_MCP_API_KEY env var; without it the wildcard bind
# refuses to start (keyless serving is loopback-only).
ENTRYPOINT ["cairn", "serve", "--transport", "http", "--host", "0.0.0.0"]

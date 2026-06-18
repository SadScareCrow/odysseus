FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ARG STEAM_MCP_REF=dbddb4f981a3a79f031b359f45c1e0e91900df8a

ENV FASTMCP_HOST=0.0.0.0 \
    FASTMCP_PORT=11020 \
    FASTMCP_STREAMABLE_HTTP_PATH=/mcp \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/steam-mcp

RUN git clone --depth=1 https://github.com/Sarg338/steam-mcp.git . \
    && git checkout "$STEAM_MCP_REF" \
    && uv sync --no-dev

EXPOSE 11020

CMD ["sh", "-c", "uv run python -c \"from steam_mcp.server import mcp; mcp.settings.host='${FASTMCP_HOST:-0.0.0.0}'; mcp.settings.port=${FASTMCP_PORT:-11020}; mcp.settings.streamable_http_path='${FASTMCP_STREAMABLE_HTTP_PATH:-/mcp}'; mcp.settings.transport_security.enable_dns_rebinding_protection=False; mcp.settings.transport_security.allowed_hosts=['*']; mcp.settings.transport_security.allowed_origins=['*']; mcp.run(transport='streamable-http')\""]

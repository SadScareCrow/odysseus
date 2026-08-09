"""Authenticated, allowlisted proxy from Odysseus to Greenhouse.

The upstream URL and bearer token are deliberately read only on the server. This
module never includes either value in a response, log message, or error detail.
"""

from __future__ import annotations

import json
import logging
import os
from urllib.parse import quote, urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request, Response

from src.auth_helpers import require_authenticated_request

logger = logging.getLogger(__name__)

_ALLOWED = (
    ("v1", "memories"),
    ("v1", "memories", "search"),
    ("v1", "areas"),
)
_HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade"}


def _path_allowed(path: str) -> bool:
    if not path.startswith("/") or path.startswith("//"):
        return False
    raw_parts = path[1:].split("/")
    if any(part in {".", ".."} or "%2e" in part.lower() for part in raw_parts):
        return False
    parts = tuple(raw_parts)
    if parts in _ALLOWED:
        return True
    if len(parts) == 3 and parts[:2] == ("v1", "memories"):
        return parts[2] not in {"search", "correct", "archive", ".", ".."}
    if len(parts) == 4 and parts[:2] == ("v1", "memories"):
        return parts[3] in {"correct", "archive"} and parts[2] not in {".", "..", "search"}
    if len(parts) == 3 and parts[:2] == ("v1", "areas"):
        return bool(parts[2])
    if len(parts) == 4 and parts[:2] == ("v1", "areas") and parts[3] == "memories":
        return bool(parts[2])
    return False


def _safe_base_url() -> str:
    base = (os.getenv("GREENHOUSE_URL") or "").strip()
    parsed = urlsplit(base)
    if not base or parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    # A configured path is harmless, but never allow credentials or fragments to
    # enter the constructed upstream URL.
    if parsed.username or parsed.password or parsed.fragment:
        return ""
    return base.rstrip("/")


def _error(status: int, message: str) -> Response:
    return Response(
        content=json.dumps({"error": "GREENHOUSE_PROXY_ERROR", "message": message}),
        status_code=status,
        media_type="application/json",
    )


def _scrub(value: bytes | str, token: str, base_url: str):
    """Keep upstream diagnostic material from becoming a credential leak."""
    if isinstance(value, bytes):
        return value.replace(token.encode(), b"[redacted]").replace(base_url.encode(), b"[greenhouse]")
    return value.replace(token, "[redacted]").replace(base_url, "[greenhouse]")


async def _proxy(request: Request, greenhouse_path: str) -> Response:
    require_authenticated_request(request)
    path = "/" + greenhouse_path
    if not _path_allowed(path):
        raise HTTPException(status_code=404, detail="Not found")

    base_url = _safe_base_url()
    token = (os.getenv("GREENHOUSE_TOKEN") or "").strip()
    if not base_url or not token:
        return _error(503, "Greenhouse is not configured")

    # quote preserves the already validated path segments and prevents any
    # caller-controlled URL syntax from being interpreted by the HTTP client.
    upstream_url = base_url + "/" + "/".join(quote(part, safe="") for part in greenhouse_path.split("/"))
    if request.url.query:
        upstream_url += "?" + request.url.query
    headers = {}
    for name in ("accept", "content-type"):
        value = request.headers.get(name)
        if value:
            headers[name] = value
    headers["authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=False, trust_env=False) as client:
            upstream = await client.request(request.method, upstream_url, headers=headers, content=await request.body())
    except httpx.TimeoutException:
        return _error(504, "Greenhouse request timed out")
    except httpx.ConnectError:
        return _error(502, "Greenhouse is unreachable")
    except httpx.RequestError:
        return _error(502, "Greenhouse request failed")

    response_headers = {
        k: _scrub(v, token, base_url) for k, v in upstream.headers.items()
        if k.lower() not in _HOP_BY_HOP and k.lower() not in {"set-cookie", "location"}
    }
    content_type = upstream.headers.get("content-type", "")
    if "json" in content_type:
        try:
            json.loads(upstream.content)
        except (TypeError, ValueError):
            return _error(502, "Greenhouse returned a malformed response")
    return Response(content=_scrub(upstream.content, token, base_url), status_code=upstream.status_code, headers=response_headers)


def setup_greenhouse_routes() -> APIRouter:
    router = APIRouter(prefix="/api/greenhouse")

    @router.api_route("/{greenhouse_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def greenhouse_proxy(request: Request, greenhouse_path: str):
        return await _proxy(request, greenhouse_path)

    return router

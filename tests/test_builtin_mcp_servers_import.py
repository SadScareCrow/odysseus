"""Every built-in MCP server must survive import.

The four servers in `mcp_servers/` register their tools with module-level
decorators (`@server.list_tools()`, `@server.call_tool()`), so an incompatible
`mcp` release breaks them at import time, before the stdio handshake — which is
exactly what happened when a bare `mcp` in requirements.txt resolved to 2.1.1 on
a rebuild:

    AttributeError: 'Server' object has no attribute 'list_tools'

Memory, Image Generation, RAG and Email all went dead in the same build and it
was only noticed by hand. Only `email_server` had incidental import coverage
(tests/test_imap_mailbox_quoting.py), so the other three were invisible to the
suite. This covers all four, and pins the decorator API they depend on.
"""

import importlib

import pytest

SERVERS = ["memory_server", "image_gen_server", "rag_server", "email_server"]


@pytest.mark.parametrize("name", SERVERS)
def test_builtin_mcp_server_imports(name):
    module = importlib.import_module(f"mcp_servers.{name}")

    assert module.server is not None


@pytest.mark.parametrize("name", SERVERS)
def test_builtin_mcp_server_exposes_lowlevel_decorators(name):
    """The installed `mcp` still speaks the low-level decorator API."""
    module = importlib.import_module(f"mcp_servers.{name}")

    assert callable(module.server.list_tools)
    assert callable(module.server.call_tool)


def test_mcp_client_transports_are_importable():
    """src/mcp_manager.py reaches for these by name on its three transports."""
    from mcp import ClientSession, StdioServerParameters  # noqa: F401
    from mcp.client.sse import sse_client  # noqa: F401
    from mcp.client.stdio import stdio_client  # noqa: F401
    from mcp.client.streamable_http import streamablehttp_client  # noqa: F401

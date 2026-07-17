"""
mcp_server — Hosted MCP (Model Context Protocol) server for Snowdesk.

Exposes a small set of read-only tools (region lookup, current conditions,
danger-rating history, resort listing) over a single JSON-RPC 2.0 endpoint
so an LLM client (Claude Desktop, claude.ai remote MCP, or any other MCP
client) can query Snowdesk's avalanche-bulletin data directly rather than
scraping rendered HTML.

Deliberately **not** built on the official ``mcp`` SDK or an ASGI stack —
Snowdesk is WSGI-only (``gunicorn config.wsgi``). The MCP Streamable-HTTP
transport permits a POST-only JSON endpoint that rejects GET with 405, which
is exactly what a plain Django view provides. See
``docs/mcp-server.md`` and ``docs/decisions/`` for the full rationale.
"""

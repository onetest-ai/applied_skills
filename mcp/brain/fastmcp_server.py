#!/usr/bin/env python3
"""Dual-transport FastMCP facade for the private knowledge.sqlite brain.

Local clients normally use STDIO; remote clients such as Copilot Studio use Streamable
HTTP. Both transports expose the exact same governed semantic tools.
"""
from __future__ import annotations

import argparse
import hmac
import os
from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from semantic_core import (
    find_related_content as _find_related_content,
    get_evidence as _get_evidence,
    get_metric as _get_metric,
    get_taxonomy as _get_taxonomy,
    health as _health,
    list_metrics as _list_metrics,
    search_knowledge as _search_knowledge,
)

INSTRUCTIONS = """
This server exposes a private knowledge brain through safe semantic tools.
Route narrative questions to search_knowledge, exact figures to get_metric, taxonomy
questions to get_taxonomy, and source inspection to get_evidence. Never infer a number
from narrative text: every numeric claim must come from get_metric and cite source_file.
If a tool returns status=not_modeled, report the gap instead of guessing.
""".strip()

mcp = FastMCP(
    name="Semantic Knowledge Brain",
    version="1.0.0",
    instructions=INSTRUCTIONS,
    mask_error_details=True,
    strict_input_validation=True,
)


@mcp.tool(tags={"discovery", "numbers"})
def list_metrics() -> dict:
    """Discover governed metrics, units, grains, date ranges, and data availability."""
    return _list_metrics()


@mcp.tool(tags={"numbers"})
def get_metric(
    name: Annotated[str, Field(description="Governed metric name returned by list_metrics")],
    grain: Annotated[str | None, Field(description="Exact grain such as overall, region, division, or branch")] = None,
    entity: Annotated[str | None, Field(description="Exact entity name")] = None,
    entity_contains: Annotated[str | None, Field(description="Case-insensitive literal substring for entity discovery")] = None,
    month: Annotated[str | None, Field(description="Exact reporting period, normally YYYY-MM")] = None,
    start_month: Annotated[str | None, Field(description="Inclusive start period, normally YYYY-MM")] = None,
    end_month: Annotated[str | None, Field(description="Inclusive end period, normally YYYY-MM")] = None,
    limit: Annotated[int, Field(ge=1, le=100, description="Maximum fact rows to return")] = 50,
) -> dict:
    """Return authoritative fact rows for one governed metric, with scope and source_file.

    Use for every value, trend, comparison, or date-specific numeric claim. This tool
    does not aggregate incompatible grains and never reads numbers from prose.
    """
    return _get_metric(name, grain, entity, entity_contains, month, start_month, end_month, limit)


@mcp.tool(tags={"narrative"})
def search_knowledge(
    query: Annotated[str, Field(min_length=1, description="Natural-language narrative question or concept")],
    limit: Annotated[int, Field(ge=1, le=100, description="Maximum cited sections")] = 5,
) -> dict:
    """Search narrative evidence with hybrid BM25+vector retrieval and source citations.

    Do not use text returned here as the authority for numeric claims; call get_metric.
    """
    return _search_knowledge(query, limit)


@mcp.tool(tags={"taxonomy"})
def get_taxonomy(
    label: Annotated[str | None, Field(description="Exact node label or node id")] = None,
    relation: Annotated[str | None, Field(description="Exact edge relation to list")] = None,
    kind: Annotated[str | None, Field(description="Node kind filter")] = None,
    limit: Annotated[int, Field(ge=1, le=100, description="Maximum nodes, edges, or citations")] = 50,
) -> dict:
    """Explore taxonomy nodes, subclasses, relations, and cited tagged sections."""
    return _get_taxonomy(label, relation, kind, limit)


@mcp.tool(tags={"narrative", "relations"})
def find_related_content(
    chunk_id: Annotated[int | None, Field(description="Anchor chunk id from search_knowledge")] = None,
    query: Annotated[str | None, Field(description="Query used to discover an anchor when chunk_id is absent")] = None,
    limit: Annotated[int, Field(ge=1, le=100, description="Maximum semantic neighbors")] = 6,
) -> dict:
    """Find precomputed cross-document semantic neighbors for a cited section."""
    return _find_related_content(chunk_id, query, limit)


@mcp.tool(tags={"evidence"})
def get_evidence(
    chunk_id: Annotated[int, Field(description="Chunk id returned by search or taxonomy tools")],
    include_page_text: Annotated[bool, Field(description="Include verbatim visual-page text and extracted table cells when available")] = True,
) -> dict:
    """Inspect one cited source section and its optional verbatim page/table evidence."""
    return _get_evidence(chunk_id, include_page_text)


@mcp.tool(tags={"operations"})
def health() -> dict:
    """Check all three knowledge lanes and report the deployed knowledge version."""
    return _health()


@mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
async def healthz(_: Request) -> JSONResponse:
    status = _health()
    return JSONResponse(status, status_code=200 if status["status"] == "healthy" else 503)


class ApiKeyMiddleware:
    """Protect only the MCP HTTP endpoint with a constant-time API-key check."""

    def __init__(self, app, api_key: str, mcp_path: str = "/mcp"):
        self.app = app
        self.api_key = api_key
        self.mcp_path = "/" + mcp_path.strip("/")

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        protected = scope.get("type") == "http" and path.rstrip("/") == self.mcp_path.rstrip("/")
        if protected:
            headers = {key.lower(): value for key, value in scope.get("headers", [])}
            supplied = headers.get(b"x-api-key", b"").decode("utf-8", errors="ignore")
            if not hmac.compare_digest(supplied, self.api_key):
                response = JSONResponse(
                    {"error": "unauthorized", "message": "A valid X-API-Key header is required."},
                    status_code=401,
                    headers={"WWW-Authenticate": 'ApiKey realm="mcp"'},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def _http_middleware() -> list[Middleware]:
    api_key = os.getenv("BRAIN_API_KEY", "").strip()
    if not api_key:
        return []
    return [Middleware(ApiKeyMiddleware, api_key=api_key, mcp_path=os.getenv("BRAIN_MCP_PATH", "/mcp"))]


app = mcp.http_app(
    path=os.getenv("BRAIN_MCP_PATH", "/mcp"),
    transport="streamable-http",
    stateless_http=True,
    # Streamable HTTP clients advertise text/event-stream. Keep JSON mode disabled so
    # MCP responses are emitted as SSE events, as expected by Copilot-style clients.
    json_response=False,
    middleware=_http_middleware(),
)


def _transport(value: str) -> str:
    normalized = value.strip().lower()
    aliases = {"http": "streamable-http", "streamable_http": "streamable-http"}
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"stdio", "streamable-http"}:
        raise argparse.ArgumentTypeError("transport must be 'stdio' or 'http'")
    return normalized


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Semantic Knowledge Brain MCP server")
    parser.add_argument(
        "--transport",
        type=_transport,
        default=_transport(os.getenv("BRAIN_MCP_TRANSPORT", "stdio")),
        metavar="stdio|http",
        help="MCP transport; defaults to BRAIN_MCP_TRANSPORT or stdio",
    )
    args = parser.parse_args(argv)
    show_banner = os.getenv("BRAIN_SHOW_BANNER", "1") != "0"
    if args.transport == "stdio":
        mcp.run(transport="stdio", show_banner=show_banner)
        return
    mcp.run(
        transport="streamable-http",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        path=os.getenv("BRAIN_MCP_PATH", "/mcp"),
        stateless_http=True,
        json_response=False,
        middleware=_http_middleware(),
        show_banner=show_banner,
    )


if __name__ == "__main__":
    main()

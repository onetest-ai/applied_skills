#!/usr/bin/env python3
"""Dual-transport FastMCP facade for the private knowledge.sqlite brain.

Local clients normally use STDIO; remote clients such as Copilot Studio use Streamable
HTTP. Both transports expose the exact same governed semantic tools.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.server.middleware.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.tool import ToolResult
from mcp.types import CallToolResult, TextContent
from pydantic import Field, SkipValidation
from starlette.middleware import Middleware as ASGIMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from semantic_core import (
    find_related_content as _find_related_content,
    get_current_fact as _get_current_fact,
    get_evidence as _get_evidence,
    get_metric as _get_metric,
    get_question_status as _get_question_status,
    get_taxonomy as _get_taxonomy,
    health as _health,
    list_metrics as _list_metrics,
    search_knowledge as _search_knowledge,
)

INSTRUCTIONS = """
This server exposes a private knowledge brain through safe, read-only semantic tools.

Routing: narrative -> search_knowledge; exact figures -> get_metric (never infer a number
from prose; every number cites its source_file); taxonomy/relations -> get_taxonomy; a named
section, page figure, or table -> get_evidence. For a mutable fact call get_current_fact and
for an open question get_question_status rather than trusting the newest retrieved sentence;
a conflicted result has no current value until a supersedes/retracts relation resolves it.

Honesty: a status=not_modeled result is a valid "no data" answer -- report the gap, don't
guess. On an error result (isError=true), follow its how_to_fix and retry with corrected
arguments. limit is 1-100; for more coverage make multiple narrower calls (split by metric,
grain, or range) rather than one oversized request.

Answering the user: lead with a direct 1-2 sentence answer, then supporting detail (bullets
only when it has parts). Cite each claim with a numbered footnote [1], [2], ... and end with
a "Sources" list mapping each number to its file and section, e.g. 1. engage-ordering.md --
"Engage vs. RMS" (a number cites the get_metric source_file). Never print internal ids like
[RAG:...], [MART:...], or [GRAPH:...]; keep chunk_ids only for follow-up calls (get_evidence,
find_related_content) and pass a chunk_id back verbatim as the string it was returned as -- it
is a large id that loses precision if turned into a number. State anything unsupported as
"Not modeled: ...", never as silence.
""".strip()

_LEGACY_TOOLS = {
    "search": "search_knowledge",
    "metric": "get_metric",
    "graph": "get_taxonomy",
    "related": "find_related_content",
    "page": "get_evidence",
    "verify": "health",
    "which": "health plus deployment configuration",
    "sql": "list_metrics/get_metric; raw SQL is intentionally unavailable",
}


class FailSafeFastMCP(FastMCP):
    """Ensure genuine failures become structured MCP error results (isError=true).

    Real clients (CodeMie/Copilot Studio) only check the MCP isError flag, so a genuine
    tool error must set it - otherwise the call is rendered as a green SUCCESS. Errors
    still keep their actionable payload (status/code/message/how_to_fix/retryable) in the
    result content for body-reading clients. The fail-safe property is preserved: a bug in
    a tool must never crash the transport with an opaque HTTP 500 - it becomes an isError
    result with a message instead of an unhandled exception.
    """

    async def _call_tool_mcp(self, key: str, arguments: dict[str, Any]):
        try:
            return _finalize_error_flags(await super()._call_tool_mcp(key, arguments))
        except Exception:
            # Tool lookup and other protocol-adapter failures happen before on_call_tool
            # middleware. Surface them as structured isError results too, never HTTP 500.
            if key in _LEGACY_TOOLS:
                replacement = _LEGACY_TOOLS[key]
                error = _error_result(
                    key,
                    "legacy_tool",
                    f"Legacy tool '{key}' was removed. Use {replacement} instead.",
                    how_to_fix=f"Retry with {replacement}. See the migration table in mcp/brain/README.md.",
                )
            else:
                error = _error_result(
                    key,
                    "unknown_tool" if key not in _TOOL_FIXES else "internal_error",
                    f"Tool '{key}' is not available." if key not in _TOOL_FIXES else "The tool could not complete the request safely.",
                )
            return _finalize_error_flags(error.to_mcp_result())


mcp = FailSafeFastMCP(
    name="Semantic Knowledge Brain",
    version="1.1.0",
    instructions=INSTRUCTIONS,
    mask_error_details=True,
    # Tool functions validate inputs themselves so mistakes can be returned as structured,
    # actionable isError results with a stable payload, instead of raw protocol errors that
    # gateways may turn into opaque HTTP 500s that hide the how_to_fix guidance.
    strict_input_validation=False,
)

_TOOL_FIXES = {
    "list_metrics": "Verify BRAIN_DB and BRAIN_CATALOG point to readable files, then retry.",
    "get_metric": "Call list_metrics, use one returned metric name and valid filters, and keep limit between 1 and 100.",
    "search_knowledge": "Provide a non-empty query and keep limit between 1 and 100.",
    "get_taxonomy": "Use an optional label, relation, or kind filter and keep limit between 1 and 100.",
    "find_related_content": "Provide either chunk_id from search_knowledge/get_taxonomy or a non-empty query; keep limit between 1 and 100.",
    "get_current_fact": "Provide non-empty entity and predicate values; use ISO-8601 for optional as_of.",
    "get_question_status": "Provide a stable non-empty question_id; use ISO-8601 for optional as_of.",
    "get_evidence": "Provide a valid integer chunk_id returned by search_knowledge, get_taxonomy, or find_related_content.",
    "health": "Verify BRAIN_DB, BRAIN_CATALOG, BRAIN_SKILLS, and sqlite-vec are installed and readable.",
}


# Private marker carried on error ToolResults so _finalize_error_flags can flip the MCP
# CallToolResult.isError flag after FastMCP has converted the ToolResult. It never reaches
# the client: _finalize_error_flags strips it while setting isError.
_ERROR_META_KEY = "brain/is_error"


def _error_result(tool: str, code: str, message: str, *, how_to_fix: str | None = None) -> ToolResult:
    payload = {
        "status": "error",
        "error": {"code": code, "message": message},
        "how_to_fix": how_to_fix or _TOOL_FIXES.get(tool, "Correct the tool arguments or server configuration, then retry."),
        "retryable": code in {"invalid_arguments", "not_configured", "dependency_unavailable"},
    }
    # Genuine errors must surface as MCP isError=true so every client (even ones that only
    # check the flag) sees the failure. The structured payload is kept in BOTH the JSON text
    # content and structured_content so body-reading clients still get actionable guidance.
    # The meta marker tags this result for _finalize_error_flags; it is removed before send.
    return ToolResult(
        content=[TextContent(type="text", text=json.dumps(payload))],
        structured_content=payload,
        meta={_ERROR_META_KEY: True},
    )


def _finalize_error_flags(result: Any) -> Any:
    """Flip CallToolResult.isError=true for results _error_result tagged, then drop the tag.

    A legitimate not_modeled/ok result is never tagged, so it stays a normal (non-error)
    MCP result. Non-CallToolResult results (plain content or a (content, structured) tuple
    from successful tools) are untouched.
    """
    if isinstance(result, CallToolResult) and result.meta and result.meta.pop(_ERROR_META_KEY, None):
        result.isError = True
        if not result.meta:
            result.meta = None
    return result


class SafeToolErrorsMiddleware(Middleware):
    """Convert tool exceptions into structured, actionable error results.

    Each _error_result carries a meta marker so FailSafeFastMCP._call_tool_mcp promotes it
    to an MCP isError=true result while preserving the how_to_fix payload. This keeps the
    fail-safe property (no exception escapes to become an opaque HTTP 500) while making
    genuine failures visible to clients that only inspect the isError flag.
    """

    async def on_call_tool(self, context: MiddlewareContext, call_next: CallNext) -> ToolResult:
        tool = context.message.name
        arguments = context.message.arguments or {}
        try:
            if "limit" in arguments:
                limit = arguments["limit"]
                if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
                    return _error_result(tool, "invalid_arguments", "limit must be an integer between 1 and 100")
            return await call_next(context)
        except (ValueError, TypeError) as exc:
            return _error_result(tool, "invalid_arguments", str(exc))
        except (FileNotFoundError, PermissionError):
            return _error_result(tool, "not_configured", "A required configured file is missing or unreadable.")
        except (ImportError, ModuleNotFoundError):
            return _error_result(tool, "dependency_unavailable", "A required runtime dependency is unavailable.")
        except Exception:
            if tool in _LEGACY_TOOLS:
                replacement = _LEGACY_TOOLS[tool]
                return _error_result(
                    tool,
                    "legacy_tool",
                    f"Legacy tool '{tool}' was removed. Use {replacement} instead.",
                    how_to_fix=f"Retry with {replacement}. See the migration table in mcp/brain/README.md.",
                )
            if tool not in _TOOL_FIXES:
                return _error_result(tool, "unknown_tool", f"Tool '{tool}' is not available.")
            return _error_result(tool, "internal_error", "The tool could not complete the request safely.")


mcp.add_middleware(SafeToolErrorsMiddleware())

_LIMIT_DESCRIPTION = "Required integer range: 1..100; never send more than 100. Split broad requests into multiple focused calls."


def _required_string(tool: str, field: str, value: Any) -> tuple[str | None, ToolResult | None]:
    if not isinstance(value, str) or not value.strip():
        return None, _error_result(tool, "invalid_arguments", f"{field} must be a non-empty string")
    return value, None


def _required_integer(tool: str, field: str, value: Any) -> tuple[int | None, ToolResult | None]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None, _error_result(tool, "invalid_arguments", f"{field} must be an integer")
    return value, None


def _required_chunk_id(tool: str, field: str, value: Any) -> tuple[int | None, ToolResult | None]:
    """chunk_id may arrive as an int or a numeric string. Search tools emit it as a
    string so float64 JSON clients don't round the 63-bit id; accept that string back."""
    if isinstance(value, bool):
        return None, _error_result(tool, "invalid_arguments", f"{field} must be an integer id, not a boolean")
    if isinstance(value, int):
        return value, None
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip()), None
    return None, _error_result(tool, "invalid_arguments", f"{field} must be an integer or a numeric string")


def _optional_string(tool: str, field: str, value: Any) -> tuple[str | None, ToolResult | None]:
    if value is not None and not isinstance(value, str):
        return None, _error_result(tool, "invalid_arguments", f"{field} must be a string when provided")
    return value, None


def _safe_call(tool: str, operation, *args: Any) -> dict | ToolResult:
    try:
        return operation(*args)
    except (ValueError, TypeError) as exc:
        return _error_result(tool, "invalid_arguments", str(exc))
    except (FileNotFoundError, PermissionError):
        return _error_result(tool, "not_configured", "A required configured file is missing or unreadable.")
    except (ImportError, ModuleNotFoundError):
        return _error_result(tool, "dependency_unavailable", "A required runtime dependency is unavailable.")
    except Exception:
        return _error_result(tool, "internal_error", "The tool could not complete the request safely.")


def _limit_or_error(tool: str, value: Any) -> tuple[int | None, ToolResult | None]:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        return None, _error_result(tool, "invalid_arguments", "limit must be an integer between 1 and 100")
    return value, None


@mcp.tool(tags={"discovery", "numbers"})
def list_metrics() -> dict | ToolResult:
    """Discover governed metrics, units, grains, date ranges, and data availability."""
    return _safe_call("list_metrics", _list_metrics)


@mcp.tool(tags={"numbers"})
def get_metric(
    name: Annotated[str | None, SkipValidation, Field(description="Required non-empty governed metric name returned by list_metrics")] = None,
    grain: Annotated[str | None, SkipValidation, Field(description="Optional exact grain such as overall, region, division, or branch")] = None,
    entity: Annotated[str | None, SkipValidation, Field(description="Optional exact entity name")] = None,
    entity_contains: Annotated[str | None, SkipValidation, Field(description="Optional case-insensitive literal substring for entity discovery")] = None,
    month: Annotated[str | None, SkipValidation, Field(description="Optional exact reporting period, normally YYYY-MM")] = None,
    start_month: Annotated[str | None, SkipValidation, Field(description="Optional inclusive start period, normally YYYY-MM")] = None,
    end_month: Annotated[str | None, SkipValidation, Field(description="Optional inclusive end period, normally YYYY-MM")] = None,
    limit: Annotated[int, SkipValidation, Field(description=_LIMIT_DESCRIPTION)] = 50,
) -> dict | ToolResult:
    """Return authoritative fact rows for one governed metric, with scope and source_file.

    Use for every value, trend, comparison, or date-specific numeric claim. This tool
    does not aggregate incompatible grains and never reads numbers from prose.
    """
    valid_name, error = _required_string("get_metric", "name", name)
    if error:
        return error
    valid_limit, error = _limit_or_error("get_metric", limit)
    if error:
        return error
    optional = []
    for field, value in (("grain", grain), ("entity", entity), ("entity_contains", entity_contains), ("month", month), ("start_month", start_month), ("end_month", end_month)):
        valid, error = _optional_string("get_metric", field, value)
        if error:
            return error
        optional.append(valid)
    return _safe_call("get_metric", _get_metric, valid_name, *optional, valid_limit)


@mcp.tool(tags={"narrative"})
def search_knowledge(
    query: Annotated[str | None, SkipValidation, Field(description="Required non-empty natural-language narrative question or concept")] = None,
    limit: Annotated[int, SkipValidation, Field(description=_LIMIT_DESCRIPTION)] = 5,
    as_of: Annotated[str | None, SkipValidation, Field(description="Optional ISO event-time cutoff")] = None,
    latest_only: Annotated[bool, SkipValidation, Field(description="Exclude chunks marked SUPERSEDED")] = False,
    source_contains: Annotated[str | None, SkipValidation, Field(description="Optional literal source-path substring")] = None,
    tag: Annotated[str | None, SkipValidation, Field(description="Optional exact taxonomy tag")] = None,
    tag_boost: Annotated[str | None, SkipValidation, Field(description="Optional taxonomy tag for RRF score boost (does not exclude untagged chunks)")] = None,
) -> dict | ToolResult:
    """Search narrative evidence with hybrid BM25+vector retrieval and source citations.

    Do not use text returned here as the authority for numeric claims; call get_metric.
    """
    valid_query, error = _required_string("search_knowledge", "query", query)
    if error:
        return error
    valid_limit, error = _limit_or_error("search_knowledge", limit)
    if error:
        return error
    if not isinstance(latest_only, bool):
        return _error_result("search_knowledge", "invalid_arguments", "latest_only must be a boolean")
    optional = []
    for field, value in (("as_of", as_of), ("source_contains", source_contains), ("tag", tag), ("tag_boost", tag_boost)):
        valid, error = _optional_string("search_knowledge", field, value)
        if error:
            return error
        optional.append(valid)
    return _safe_call("search_knowledge", _search_knowledge, valid_query, valid_limit, optional[0], latest_only, optional[1], optional[2], optional[3])


@mcp.tool(tags={"temporal", "facts"})
def get_current_fact(
    entity: Annotated[str | None, SkipValidation, Field(description="Required stable entity key")] = None,
    predicate: Annotated[str | None, SkipValidation, Field(description="Required stable fact predicate")] = None,
    as_of: Annotated[str | None, SkipValidation, Field(description="Optional ISO-8601 event-time cutoff")] = None,
) -> dict | ToolResult:
    """Resolve a mutable fact without discarding prior meeting assertions."""
    valid_entity, error = _required_string("get_current_fact", "entity", entity)
    if error:
        return error
    valid_predicate, error = _required_string("get_current_fact", "predicate", predicate)
    if error:
        return error
    valid_as_of, error = _optional_string("get_current_fact", "as_of", as_of)
    if error:
        return error
    return _safe_call("get_current_fact", _get_current_fact, valid_entity, valid_predicate, valid_as_of)


@mcp.tool(tags={"temporal", "questions"})
def get_question_status(
    question_id: Annotated[str | None, SkipValidation, Field(description="Required stable question ID from extraction")] = None,
    as_of: Annotated[str | None, SkipValidation, Field(description="Optional ISO-8601 event-time cutoff")] = None,
) -> dict | ToolResult:
    """Resolve an earlier question only through an explicit later answer link."""
    valid_id, error = _required_string("get_question_status", "question_id", question_id)
    if error:
        return error
    valid_as_of, error = _optional_string("get_question_status", "as_of", as_of)
    if error:
        return error
    return _safe_call("get_question_status", _get_question_status, valid_id, valid_as_of)


@mcp.tool(tags={"taxonomy"})
def get_taxonomy(
    label: Annotated[str | None, SkipValidation, Field(description="Optional exact node label or node id")] = None,
    relation: Annotated[str | None, SkipValidation, Field(description="Optional exact edge relation to list")] = None,
    kind: Annotated[str | None, SkipValidation, Field(description="Optional node kind filter")] = None,
    limit: Annotated[int, SkipValidation, Field(description=_LIMIT_DESCRIPTION)] = 50,
) -> dict | ToolResult:
    """Explore taxonomy nodes, subclasses, relations, and cited tagged sections."""
    optional = []
    for field, value in (("label", label), ("relation", relation), ("kind", kind)):
        valid, error = _optional_string("get_taxonomy", field, value)
        if error:
            return error
        optional.append(valid)
    valid_limit, error = _limit_or_error("get_taxonomy", limit)
    if error:
        return error
    return _safe_call("get_taxonomy", _get_taxonomy, *optional, valid_limit)


@mcp.tool(tags={"narrative", "relations"})
def find_related_content(
    chunk_id: Annotated[int | str | None, SkipValidation, Field(description="Optional anchor chunk id from search_knowledge; pass it back exactly as the string it was returned as")] = None,
    query: Annotated[str | None, SkipValidation, Field(description="Optional query used to discover an anchor when chunk_id is absent")] = None,
    limit: Annotated[int, SkipValidation, Field(description=_LIMIT_DESCRIPTION)] = 6,
) -> dict | ToolResult:
    """Find precomputed cross-document semantic neighbors for a cited section."""
    if chunk_id is not None:
        valid_chunk_id, error = _required_chunk_id("find_related_content", "chunk_id", chunk_id)
        if error:
            return error
        chunk_id = valid_chunk_id
    valid_query, error = _optional_string("find_related_content", "query", query)
    if error:
        return error
    valid_limit, error = _limit_or_error("find_related_content", limit)
    if error:
        return error
    if chunk_id is None and not (valid_query and valid_query.strip()):
        return _error_result("find_related_content", "invalid_arguments", "Either chunk_id or a non-empty query is required")
    return _safe_call("find_related_content", _find_related_content, chunk_id, valid_query, valid_limit)


@mcp.tool(tags={"evidence"})
def get_evidence(
    chunk_id: Annotated[int | str | None, SkipValidation, Field(description="Required chunk id returned by search or taxonomy tools; pass it back exactly as the string it was returned as")] = None,
    include_page_text: Annotated[bool, SkipValidation, Field(description="Boolean: include verbatim visual-page text and extracted table cells when available")] = True,
) -> dict | ToolResult:
    """Inspect one cited source section and its optional verbatim page/table evidence."""
    valid_chunk_id, error = _required_chunk_id("get_evidence", "chunk_id", chunk_id)
    if error:
        return error
    if not isinstance(include_page_text, bool):
        return _error_result("get_evidence", "invalid_arguments", "include_page_text must be a boolean")
    return _safe_call("get_evidence", _get_evidence, valid_chunk_id, include_page_text)


@mcp.tool(tags={"operations"})
def health() -> dict | ToolResult:
    """Check all three knowledge lanes and report the deployed knowledge version."""
    return _safe_call("health", _health)


@mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
async def healthz(_: Request) -> JSONResponse:
    try:
        status = _health()
        return JSONResponse(status, status_code=200 if status["status"] == "healthy" else 503)
    except Exception:
        return JSONResponse(
            {
                "status": "degraded",
                "error": {"code": "health_check_failed", "message": "The health check could not complete safely."},
                "how_to_fix": _TOOL_FIXES["health"],
                "retryable": True,
            },
            status_code=503,
        )


@mcp.custom_route("/api/v1/search", methods=["POST"], include_in_schema=False)
async def search_shim(request: Request) -> JSONResponse:
    """Thin REST shim so promptfoo can hit FastMCP without MCP JSON-RPC syntax.
    Accepts: {"query": str, "searchType": "RAG_COMPLETION"|..., "limit": int}
    Returns: [{"text": str, "source": str, ...}] — same shape as Cognee v1 for eval compat.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    query = body.get("query", "").strip()
    if not query:
        return JSONResponse({"error": "query is required"}, status_code=400)
    # Bug 10 fix: cap limit at 100 before passing to _search_knowledge.
    # Before fix: limit=200 passed through unchecked, _limit_or_error raises
    # inside _safe_call, and the shim returns str(ToolResult(...)) — a
    # stringified object — instead of a structured JSON response.
    try:
        limit = max(1, min(int(body.get("limit") or 15), 100))
    except (TypeError, ValueError):
        limit = 15
    for field in ("asOf", "sourceContains", "tag", "tagBoost"):
        raw = body.get(field)
        _, err = _optional_string("search_shim", field, raw)
        if err:
            return JSONResponse({"error": f"{field} must be a string"}, status_code=400)
    as_of = body.get("asOf") or None
    source_contains = body.get("sourceContains") or None
    tag = body.get("tag") or None
    tag_boost = body.get("tagBoost") or None
    latest_only_raw = body.get("latestOnly", False)
    if not isinstance(latest_only_raw, bool):
        return JSONResponse({"error": "latestOnly must be a boolean"}, status_code=400)
    result = _safe_call(
        "search_knowledge", _search_knowledge, query, limit,
        as_of, latest_only_raw,
        source_contains, tag, tag_boost,
    )
    # _safe_call returns a dict with key "hits" (list of chunk dicts); any other
    # type means _safe_call caught an exception and returned a ToolResult.
    if not isinstance(result, dict):
        return JSONResponse({"error": "internal error"}, status_code=500)
    hits = result.get("hits", result.get("results", []))
    # Exclude orig/ chunks — they are raw JSON dumps, not structured retrieval content.
    # Do this before the emptiness check so all-orig results fall through to the fallback.
    hits = [h for h in hits if not h.get("source", "").startswith("orig/")]
    if hits:
        flat = [{"text": h.get("text", ""), "source": h.get("source", ""),
                 "title": h.get("section", h.get("title", "")),
                 "score": h.get("score", 0)} for h in hits]
        # Flatten into a single text block for promptfoo responseParser: json[0].text
        combined = "\n\n---\n\n".join(
            f"[{h['source']} / {h['title']}]\n{h['text']}" for h in flat
        )
        return JSONResponse([{
            "text": combined,
            "source": flat[0]["source"] if flat else "",
            "sources": [h["source"] for h in flat],
            "result_type": "retrieved_context",
        }])
    return JSONResponse([{"text": "No context retrieved.", "source": ""}])


class ApiKeyMiddleware:
    """Protect only the MCP HTTP endpoint with a constant-time API-key check."""

    def __init__(self, app, api_key: str, mcp_path: str = "/mcp"):
        self.app = app
        self.api_key = api_key
        self.mcp_path = "/" + mcp_path.strip("/")

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        protected = scope.get("type") == "http" and (
            path.rstrip("/") == self.mcp_path.rstrip("/")
            or path.startswith("/api/")
        )
        if protected:
            headers = {key.lower(): value for key, value in scope.get("headers", [])}
            supplied = headers.get(b"x-api-key", b"").decode("utf-8", errors="ignore").strip()
            if not hmac.compare_digest(supplied, self.api_key):
                response = JSONResponse(
                    {"error": "unauthorized", "message": "A valid X-API-Key header is required."},
                    status_code=401,
                    headers={"WWW-Authenticate": 'ApiKey realm="mcp"'},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def _http_middleware() -> list[ASGIMiddleware]:
    api_key = os.getenv("BRAIN_API_KEY", "").strip()
    if not api_key:
        return []
    return [ASGIMiddleware(ApiKeyMiddleware, api_key=api_key, mcp_path=os.getenv("BRAIN_MCP_PATH", "/mcp"))]


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
        default=None,
        metavar="stdio|http",
        help="MCP transport; defaults to BRAIN_MCP_TRANSPORT or stdio",
    )
    args = parser.parse_args(argv)
    if args.transport is None:
        try:
            args.transport = _transport(os.getenv("BRAIN_MCP_TRANSPORT", "stdio"))
        except argparse.ArgumentTypeError as exc:
            parser.error(str(exc))
    show_banner = os.getenv("BRAIN_SHOW_BANNER", "1") != "0"
    if args.transport == "stdio":
        mcp.run(transport="stdio", show_banner=show_banner)
        return
    mcp.run(
        transport="streamable-http",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        path=os.getenv("BRAIN_MCP_PATH", "/mcp"),
        stateless_http=True,
        json_response=False,
        middleware=_http_middleware(),
        show_banner=show_banner,
    )


if __name__ == "__main__":
    main()

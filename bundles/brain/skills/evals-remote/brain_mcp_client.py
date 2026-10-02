"""Thin client to a remote Streamable-HTTP MCP brain, authenticated by a static header."""
import argparse
import asyncio
import json
import os
import sys

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

# Generic env var names — no brain is hardcoded. A consuming project sets these (or passes
# --url/--key), and its config may name different secret env vars (resolved by higher-level scripts).
URL_ENV = "BRAIN_MCP_URL"
KEY_ENV = "BRAIN_API_KEY"
HEADER_ENV = "BRAIN_API_KEY_HEADER"
DEFAULT_HEADER = "X-API-Key"


class BrainClientError(RuntimeError):
    pass


def _resolve(url, key, key_header):
    url = url or os.environ.get(URL_ENV)
    key = key if key is not None else os.environ.get(KEY_ENV)
    key_header = key_header or os.environ.get(HEADER_ENV, DEFAULT_HEADER)
    if not url:
        raise BrainClientError(f"no brain url: set {URL_ENV} or pass --url")
    # key is OPTIONAL: a local, keyless brain (e.g. http://127.0.0.1:8001/mcp --allow-http)
    # is called with no auth header. Only the url is mandatory.
    return url, (key or None), key_header


def call_tool(name, args, *, url=None, key=None, key_header=None):
    """Call one MCP tool and return its structured result as a dict."""
    url, key, key_header = _resolve(url, key, key_header)

    async def _run():
        headers = {key_header: key} if key else {}
        transport = StreamableHttpTransport(url, headers=headers)
        try:
            async with Client(transport) as client:
                res = await client.call_tool(name, args or {})
        except Exception as e:  # transport / auth / protocol
            raise BrainClientError(f"{name} failed: {e}") from e
        data = getattr(res, "structured_content", None)
        if data is None:
            data = getattr(res, "data", None)
        return data

    return asyncio.run(_run())


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["call"])
    p.add_argument("tool")
    p.add_argument("--json", default="{}", help="tool args as JSON")
    p.add_argument("--url")
    p.add_argument("--key")
    p.add_argument("--key-header")
    p.add_argument("--config", help="evals config JSON (or EVALS_CONFIG env) to resolve url/key")
    a = p.parse_args(argv)
    url, key, key_header = a.url, a.key, a.key_header
    # Resolve from config when not passed explicitly (config's key_env may name a project secret var).
    if (not url or not key) and (a.config or os.environ.get("EVALS_CONFIG")):
        from evals_config import load_config, resolve_brain
        try:
            url, key, key_header = resolve_brain(load_config(a.config), url=url, key=key, key_header=key_header)
        except (ValueError, OSError) as e:
            print(str(e), file=sys.stderr)
            return 2
    try:
        out = call_tool(a.tool, json.loads(a.json), url=url, key=key, key_header=key_header)
    except BrainClientError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Project-owned Azure Container Apps adapter for immutable Brain MCP releases.

Implements the plan/deploy/verify contract that `brain-maintenance` invokes
(SKILL §8). It is agent-owned and external: `maintenance.py` never runs it.

- `plan`   read-only; prints the sanitized build/update commands, no secrets.
- `deploy` requires --yes; runs the same immutable build/update sequence.
- `verify` re-checks a live revision (health, auth rejection, typed tool contract,
           governed metric + narrative smoke tests, Azure revision/traffic).

Secrets are referenced by Azure secret name only; the verification API key is read
from the configured environment variable and never printed or accepted as a field.

Copy this file + deploy.example.toml into your project (default: `ops/deploy/`),
edit only the TOML, and point `[deployment]` in brain-maintenance.toml at them.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore

TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SUFFIX = re.compile(r"^[a-z][a-z0-9-]{0,62}$")


def load(path: Path) -> dict:
    with path.open("rb") as handle:
        cfg = tomllib.load(handle)
    if cfg.get("version") != 1:
        raise ValueError("Azure deployment profile must contain version = 1")
    for section in ("azure", "image", "runtime", "verification"):
        if not isinstance(cfg.get(section), dict):
            raise ValueError(f"missing [{section}] section")
    return cfg


def commands(cfg: dict, tag: str, suffix: str) -> list[list[str]]:
    if not TAG.fullmatch(tag): raise ValueError("invalid immutable image tag")
    if not SUFFIX.fullmatch(suffix): raise ValueError("invalid Container Apps revision suffix")
    az, image, runtime = cfg["azure"], cfg["image"], cfg["runtime"]
    full_image = f"{az['registry_server']}/{az['repository']}:{tag}"
    return [
        ["az", "acr", "build", "--subscription", az["subscription"], "--registry", az["registry"], "--image", f"{az['repository']}:{tag}",
         "--file", image["dockerfile"], image["context"]],
        ["az", "containerapp", "update", "--subscription", az["subscription"], "--name", az["container_app"], "--resource-group", az["resource_group"],
         "--image", full_image, "--revision-suffix", suffix, "--set-env-vars",
         f"BRAIN_API_KEY=secretref:{az['api_key_secret_ref']}",
         f"{runtime['knowledge_version_env']}={tag}", f"BRAIN_SHOW_BANNER={runtime.get('show_banner', '0')}"],
    ]


def sanitized_plan(cfg: dict, profile: Path, tag: str, suffix: str) -> dict:
    az = cfg["azure"]
    return {
        "version": 1,
        "provider": "azure-container-apps",
        "profile": str(profile),
        "subscription": az["subscription"],
        "resource_group": az["resource_group"],
        "container_app": az["container_app"],
        "image": f"{az['registry_server']}/{az['repository']}:{tag}",
        "revision_suffix": suffix,
        "commands": commands(cfg, tag, suffix),
        "credentials": "Azure CLI session plus existing Container Apps secret reference; no secret values in plan",
        "post_deploy": ["revision health and traffic", "healthz", "missing/wrong-key 401", "typed seven-tool contract", "metric", "search", "logs"],
    }


def _http_status(url: str, headers: dict[str, str]) -> int:
    request = urllib.request.Request(url, data=b"{}", headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def verify(cfg: dict) -> dict:
    verification, az = cfg["verification"], cfg["azure"]
    api_key = os.getenv(cfg["runtime"].get("api_key_env", "BRAIN_API_KEY"), "")
    if not api_key:
        raise RuntimeError("verification API key environment variable is not set")
    with urllib.request.urlopen(verification["health_url"], timeout=30) as response:
        health = json.load(response)
        if response.status != 200 or health.get("status") != "healthy" or health.get("database_check") != "ok":
            raise RuntimeError("health verification failed")
    missing, wrong = _http_status(verification["mcp_url"], {}), _http_status(verification["mcp_url"], {"X-API-Key": "wrong-key"})
    if missing != 401 or wrong != 401:
        raise RuntimeError("authentication rejection verification failed")

    async def mcp_checks():
        from fastmcp import Client
        from fastmcp.client.transports import StreamableHttpTransport
        transport = StreamableHttpTransport(verification["mcp_url"], headers={"X-API-Key": api_key})
        async with Client(transport) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            expected = set(verification["expected_tools"])
            if set(tools) != expected:
                raise RuntimeError("unexpected public tool set")
            for tool in tools.values():
                for field, schema in tool.inputSchema.get("properties", {}).items():
                    if "type" not in schema:
                        raise RuntimeError(f"{tool.name}.{field} has no concrete JSON Schema type")
            metric = await client.call_tool("get_metric", {"name": verification["metric_name"], "grain": verification["metric_grain"], "limit": 1})
            rows = metric.data.get("rows", [])
            if not rows or not rows[0].get("source_file"):
                raise RuntimeError("governed metric smoke test failed")
            search = await client.call_tool("search_knowledge", {"query": verification["search_query"], "limit": 1})
            if not search.data.get("hits"):
                raise RuntimeError("narrative search smoke test failed")
            return sorted(tools), rows[0]["source_file"], search.data["hits"][0].get("source")

    tools, metric_source, search_source = asyncio.run(mcp_checks())
    revision = json.loads(subprocess.run([
        "az", "containerapp", "show", "--subscription", az["subscription"], "--name", az["container_app"], "--resource-group", az["resource_group"],
        "--query", "{latestRevision:properties.latestRevisionName,latestReadyRevision:properties.latestReadyRevisionName,traffic:properties.configuration.ingress.traffic,image:properties.template.containers[0].image}", "-o", "json",
    ], check=True, text=True, capture_output=True).stdout)
    return {"status": "ok", "health": health, "auth": {"missing": missing, "wrong": wrong}, "tools": tools,
            "metric_source_file": metric_source, "search_source": search_source, "azure": revision}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("plan", "deploy"):
        p = sub.add_parser(name); p.add_argument("--profile", required=True); p.add_argument("--tag", required=True)
        p.add_argument("--suffix"); p.add_argument("--yes", action="store_true")
    p = sub.add_parser("verify"); p.add_argument("--profile", required=True)
    a = ap.parse_args(argv)
    try:
        profile = Path(a.profile).expanduser().resolve(); cfg = load(profile)
        if a.command == "verify":
            print(json.dumps(verify(cfg), indent=2)); return 0
        # Container Apps revision suffixes must start with a letter, so a date-leading
        # tag (e.g. 20260101-r1) gets an "r" prefix when no explicit --suffix is given.
        derived = re.sub(r"[^a-z0-9-]", "-", a.tag.lower()).strip("-")
        if derived and not derived[0].isalpha():
            derived = f"r{derived}"
        suffix = a.suffix or derived[:63]
        plan = sanitized_plan(cfg, profile, a.tag, suffix)
        print(json.dumps(plan, indent=2))
        if a.command == "plan": return 0
        if not a.yes: raise RuntimeError("deployment requires --yes after reviewing the plan")
        for command in commands(cfg, a.tag, suffix):
            subprocess.run(command, check=True)
        print(json.dumps(verify(cfg), indent=2))
        return 0
    except (ValueError, KeyError, FileNotFoundError, RuntimeError, subprocess.CalledProcessError, ImportError) as exc:
        print(f"Azure deployment adapter error: {exc}", file=sys.stderr); return 2


if __name__ == "__main__":
    raise SystemExit(main())

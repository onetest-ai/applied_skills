#!/usr/bin/env python3
"""Local Docker adapter for the Brain MCP server — dev-loop deployment.

Same plan/deploy/verify contract as the cloud adapters, so it wires into
`brain-maintenance` `[deployment]` identically, but targets a local Docker daemon
instead of a cloud runtime. Adds a `stage` step that assembles the image build
context from source paths in the profile (the fixed layout mcp/brain/Dockerfile
expects) — the piece the cloud adapters otherwise assume is pre-staged.

Operations:
  stage   assemble the build context (read source paths → context layout).
  plan    read-only; print the staging actions + docker build/run commands.
  deploy  requires --yes; stage → build → (re)run the container → verify.
  verify  re-check the running container end to end (localhost).
  stop    stop and remove the running container.

Self-contained: stdlib + the `docker` CLI (+ fastmcp for the verify tool checks).
Copy this file + deploy.example.toml into ops/deploy/ and edit only the TOML.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore


def load(path: Path) -> dict:
    with path.open("rb") as handle:
        cfg = tomllib.load(handle)
    if cfg.get("version") != 1:
        raise ValueError("local deployment profile must contain version = 1")
    for section in ("image", "stage", "run", "verification"):
        if not isinstance(cfg.get(section), dict):
            raise ValueError(f"missing [{section}] section")
    return cfg


def _base(profile: Path) -> Path:
    return profile.parent


def stage_actions(cfg: dict, profile: Path) -> list[tuple[str, str]]:
    """Return (source, dest-in-context) copy pairs for the build context layout."""
    base, stage = _base(profile), cfg["stage"]
    context = (base / cfg["image"]["context"]).resolve()
    server = base / stage["server"]
    skills = base / stage["skills"]
    pairs = [
        (str(server / "fastmcp_server.py"), "server/fastmcp_server.py"),
        (str(server / "semantic_core.py"), "server/semantic_core.py"),
        (str(server / "requirements.txt"), "server/requirements.txt"),
        (str(base / stage["db"]), "data/knowledge.sqlite"),
        (str(base / stage["catalog"]), "schema/metrics.json"),
        (str(skills / "knowledge-index"), "skills/knowledge-index"),
        (str(skills / "corpus-taxonomy-extraction"), "skills/corpus-taxonomy-extraction"),
    ]
    assets = stage.get("assets")
    pairs.append((str(base / assets) if assets else "(empty dir)", "assets"))
    return [(src, str(context / dest)) for src, dest in pairs]


def stage(cfg: dict, profile: Path) -> Path:
    context = (_base(profile) / cfg["image"]["context"]).resolve()
    if context.exists():
        shutil.rmtree(context)
    for src, dest in stage_actions(cfg, profile):
        dpath = Path(dest)
        dpath.parent.mkdir(parents=True, exist_ok=True)
        if src == "(empty dir)":
            dpath.mkdir(parents=True, exist_ok=True)
            continue
        spath = Path(src)
        if not spath.exists():
            raise FileNotFoundError(f"stage source not found: {src}")
        if spath.is_dir():
            shutil.copytree(spath, dpath, dirs_exist_ok=True)
        else:
            shutil.copy2(spath, dpath)
    return context


def commands(cfg: dict, profile: Path, tag: str) -> list[list[str]]:
    image, run = cfg["image"], cfg["run"]
    base = _base(profile)
    context = str((base / image["context"]).resolve())
    dockerfile = str((base / image["dockerfile"]).resolve())
    full = f"{image['name']}:{tag}"
    port = int(run["port"])
    return [
        ["docker", "build", "-f", dockerfile, "-t", full, context],
        ["docker", "rm", "-f", run["container"]],  # tolerated if absent
        ["docker", "run", "-d", "--name", run["container"], "-p", f"{port}:8000",
         "-e", f"BRAIN_API_KEY={{{run.get('api_key_env', 'BRAIN_API_KEY')}}}",
         "-e", f"BRAIN_KNOWLEDGE_VERSION={tag}", full],
    ]


def sanitized_plan(cfg: dict, profile: Path, tag: str) -> dict:
    run = cfg["run"]
    cmds = commands(cfg, profile, tag)
    # Mask the API key placeholder in the printed plan.
    printable = [[("-e BRAIN_API_KEY=***" if str(a).startswith("BRAIN_API_KEY=") else a) for a in c] for c in cmds]
    return {
        "version": 1,
        "provider": "local-docker",
        "profile": str(profile),
        "image": f"{cfg['image']['name']}:{tag}",
        "container": run["container"],
        "port": int(run["port"]),
        "stage": [{"from": s, "to": Path(d).name if s == "(empty dir)" else d} for s, d in stage_actions(cfg, profile)],
        "commands": printable,
        "credentials": f"API key read from ${run.get('api_key_env', 'BRAIN_API_KEY')} at run time; never printed",
        "post_deploy": ["healthz", "missing/wrong-key 401", "typed seven-tool contract", "metric", "search"],
    }


def _http_status(url: str, headers: dict[str, str]) -> int:
    request = urllib.request.Request(url, data=b"{}", headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def _wait_healthy(url: str, timeout: float = 90.0) -> dict:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                health = json.load(response)
                if response.status == 200 and health.get("status") == "healthy" and health.get("database_check") == "ok":
                    return health
                last = json.dumps(health)
        except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
            last = str(exc)
        time.sleep(2)
    raise RuntimeError(f"container did not become healthy within {int(timeout)}s (last: {last})")


def verify(cfg: dict) -> dict:
    verification = cfg["verification"]
    api_key = os.getenv(cfg["run"].get("api_key_env", "BRAIN_API_KEY"), "")
    if not api_key:
        raise RuntimeError("verification API key environment variable is not set")
    health = _wait_healthy(verification["health_url"])
    missing, wrong = _http_status(verification["mcp_url"], {}), _http_status(verification["mcp_url"], {"X-API-Key": "wrong-key"})
    if missing != 401 or wrong != 401:
        raise RuntimeError("authentication rejection verification failed")

    async def mcp_checks():
        from fastmcp import Client
        from fastmcp.client.transports import StreamableHttpTransport
        transport = StreamableHttpTransport(verification["mcp_url"], headers={"X-API-Key": api_key})
        async with Client(transport) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            if set(tools) != set(verification["expected_tools"]):
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
    return {"status": "ok", "health": health, "auth": {"missing": missing, "wrong": wrong}, "tools": tools,
            "metric_source_file": metric_source, "search_source": search_source}


def _run_docker(cmd: list[str], tag: str, run: dict) -> None:
    # Substitute the real API key only at exec time (never in the printed plan).
    resolved = [a.replace(f"BRAIN_API_KEY={{{run.get('api_key_env', 'BRAIN_API_KEY')}}}",
                          f"BRAIN_API_KEY={os.getenv(run.get('api_key_env', 'BRAIN_API_KEY'), '')}") for a in cmd]
    tolerate = resolved[:3] == ["docker", "rm", "-f"]
    subprocess.run(resolved, check=not tolerate)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("plan", "deploy"):
        p = sub.add_parser(name); p.add_argument("--profile", required=True); p.add_argument("--tag", default="local")
        p.add_argument("--yes", action="store_true")
    for name in ("stage", "verify", "stop"):
        p = sub.add_parser(name); p.add_argument("--profile", required=True)
    a = ap.parse_args(argv)
    try:
        profile = Path(a.profile).expanduser().resolve(); cfg = load(profile)
        if a.command == "stage":
            context = stage(cfg, profile); print(json.dumps({"status": "staged", "context": str(context)}, indent=2)); return 0
        if a.command == "verify":
            print(json.dumps(verify(cfg), indent=2)); return 0
        if a.command == "stop":
            subprocess.run(["docker", "rm", "-f", cfg["run"]["container"]], check=False); return 0
        plan = sanitized_plan(cfg, profile, a.tag)
        print(json.dumps(plan, indent=2))
        if a.command == "plan": return 0
        if not a.yes: raise RuntimeError("deployment requires --yes after reviewing the plan")
        if not os.getenv(cfg["run"].get("api_key_env", "BRAIN_API_KEY"), ""):
            raise RuntimeError(f"${cfg['run'].get('api_key_env', 'BRAIN_API_KEY')} must be set before deploy")
        stage(cfg, profile)
        for command in commands(cfg, profile, a.tag):
            _run_docker(command, a.tag, cfg["run"])
        print(json.dumps(verify(cfg), indent=2))
        return 0
    except (ValueError, KeyError, FileNotFoundError, RuntimeError, subprocess.CalledProcessError, ImportError) as exc:
        print(f"Local deployment adapter error: {exc}", file=sys.stderr); return 2


if __name__ == "__main__":
    raise SystemExit(main())

# Deploying the Brain MCP server

Deployment is an **agent-owned, external step** — `brain-maintenance`'s
`maintenance.py` never runs it (SKILL §8). This directory ships **reference
adapters** a project copies in; the plugin stays engagement-agnostic.

Each provider adapter implements the same `plan` / `deploy` / `verify` contract:

- `plan` — read-only; prints the exact build/update commands, no secrets.
- `deploy` — requires `--yes`; runs the immutable build + revision update.
- `verify` — re-checks a live revision end to end.

## What's here

```
mcp/brain/
  Dockerfile                         # provider-neutral image (also local-buildable)
  requirements.txt                   # pinned server deps baked into the image
  deploy/
    README.md                        # this file
    azure-container-apps/
      brain_deploy.py                # cloud adapter
      deploy.example.toml            # profile template (no secrets)
    local-docker/
      brain_deploy.py                # local dev-loop adapter (stage/plan/deploy/verify/stop)
      deploy.example.toml            # local profile template
```

Other providers (GCP Cloud Run, AWS App Runner, …) get a sibling folder under
`deploy/` implementing the same subcommands.

## Local pipeline (`local-docker`)

For the dev loop — build and run the image on a local Docker daemon, no cloud. It
adds a `stage` step that **assembles the build context** (the fixed layout above)
from source paths in its profile, so you don't hand-stage anything.

```bash
mkdir -p ops/deploy
cp <plugin>/mcp/brain/deploy/local-docker/brain_deploy.py    ops/deploy/
cp <plugin>/mcp/brain/deploy/local-docker/deploy.example.toml ops/deploy/deploy.local.toml
# edit [stage] paths to point at your server dir, store, catalog, and skills

export BRAIN_API_KEY=dev-key                 # any value; local only, never committed

python ops/deploy/brain_deploy.py stage  --profile ops/deploy/deploy.local.toml   # assemble context
python ops/deploy/brain_deploy.py plan   --profile ops/deploy/deploy.local.toml   # review (no side effects)
python ops/deploy/brain_deploy.py deploy --profile ops/deploy/deploy.local.toml --yes   # stage+build+run+verify
python ops/deploy/brain_deploy.py verify --profile ops/deploy/deploy.local.toml   # re-check the running container
python ops/deploy/brain_deploy.py stop   --profile ops/deploy/deploy.local.toml   # stop + remove
```

Same `plan`/`deploy`/`verify` contract as the cloud adapters, so it wires into
`brain-maintenance.toml` `[deployment]` identically (point `adapter` at the local
copy, `profile` at `deploy.local.toml`). `deploy` refuses to run unless the API-key
env var is set, and the key is substituted only at `docker run` exec time — never
in the printed plan.

## Wiring it into a project

1. Copy the adapter + profile into the project (the `deploy/` convention):

   ```bash
   mkdir -p ops/deploy
   cp <plugin>/mcp/brain/deploy/azure-container-apps/brain_deploy.py     ops/deploy/
   cp <plugin>/mcp/brain/deploy/azure-container-apps/deploy.example.toml ops/deploy/deploy.toml
   ```

2. Edit `ops/deploy/deploy.toml` — subscription, resource group, registry,
   container app, the secret name, and the verification URLs + smoke-test values.
   **No secrets go in this file.**

3. Point `brain-maintenance.toml` `[deployment]` at it:

   ```toml
   [deployment]
   enabled = true
   adapter = "ops/deploy/brain_deploy.py"
   profile = "ops/deploy/deploy.toml"
   secret_env = ["BRAIN_API_KEY"]
   require_immutable_version = true
   ```

## Build context (assemble before building)

Docker forbids build ARGs in `COPY` *source* paths, so the image expects a fixed
layout under the build context (`[image].context`) instead of per-path variables.
Stage these before `plan`/`deploy` (or point `context` at a dir you assemble):

```
<context>/
  server/                              # fastmcp_server.py, semantic_core.py, requirements.txt
                                       #   (vendored from the plugin's mcp/brain/)
  data/knowledge.sqlite                # the built, immutable store
  schema/metrics.json                  # governed metric catalog (BRAIN_CATALOG)
  assets/                              # page images / table evidence (keep dir even if empty)
  skills/knowledge-index/
  skills/corpus-taxonomy-extraction/
```

## Run it (after local verification passes)

```bash
# 1. review the plan (no side effects)
python ops/deploy/brain_deploy.py plan   --profile ops/deploy/deploy.toml --tag 20260101-r1

# 2. deploy after human approval
python ops/deploy/brain_deploy.py deploy --profile ops/deploy/deploy.toml --tag 20260101-r1 --yes

# 3. verify the live revision (BRAIN_API_KEY must be exported)
export BRAIN_API_KEY=…            # never committed; read only here
python ops/deploy/brain_deploy.py verify --profile ops/deploy/deploy.toml
```

A deployment is complete only after **all** post-deploy checks pass: revision
health/traffic, `/healthz`, missing/wrong-key → 401, the typed seven-tool
contract, a governed-metric smoke test, a narrative-search smoke test, and logs.

## Rules (from brain-maintenance §deploy)

- No deploy before local verification passes; the maintenance report always keeps
  `ready_to_deploy=false` — only this adapter's `verify` + a human gate flips it.
- No credentials in profiles, plans, build args, or logs.
- Immutable releases only — never mutate the deployed SQLite in place; build a new
  tagged image + revision, and keep the prior image/snapshot for rollback.
- **Resync every secondary copy** of the store (bundled client instances, baked
  images, Copilot-Studio packs) after a rebuild, with `assets/` alongside — no
  consumer should read a pre-rebuild snapshot.

## Local run (no cloud)

The Dockerfile doubles as a local image:

```bash
docker build -f mcp/brain/Dockerfile -t brain-mcp .   # from an assembled context
docker run --rm -p 8000:8000 -e BRAIN_API_KEY=dev-key brain-mcp
curl localhost:8000/healthz
```

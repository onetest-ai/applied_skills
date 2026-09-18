# Entra OAuth facade for an APIM-fronted Brain MCP server

A sibling deploy adapter (same `plan` / `deploy` / `verify` contract as
`azure-container-apps/`) that lets MCP **clients** — Claude Desktop, VS Code, MCP
Inspector — complete OAuth against a Brain MCP server that sits behind Azure API
Management with Microsoft Entra.

This is **auth in front of** the server; it does not build or run the server image
(that's `azure-container-apps/`). Run it once after the Container App + Entra app
exist, then again whenever the facade config changes.

## The problem it solves

MCP clients send an RFC 8707 `resource=<server URL>` indicator to the token/authorize
endpoints, in addition to `scope=api://<app>/<scope>`. Entra v2 rejects that pair:

```
AADSTS9010010 — the provided resource value doesn't match the requested scopes
```

because the `resource` (an `https://…azure-api.net/…` URL) can't prefix-match an
`api://` scope audience. You can't register the server URL as an Entra identifier URI
(the domain isn't verifiable), and the client insists the advertised PRM `resource`
equals the server URL — so the fix is to **strip `resource` before it reaches Entra**.
This adapter makes APIM a transparent authorization-server facade that does that.

Four layers have to line up (each is a separate failure the adapter fixes):

| Client/Entra symptom | Cause | What the adapter does |
|---|---|---|
| `AADSTS9010010` | `resource` ≠ scope audience | `/authorize` + `/token` strip `resource` |
| generic "authorization failed" | `OPTIONS /token` 404 (CORS preflight) | API-level `<cors>` policy |
| `invalid_client` | callback on **Web** platform ⇒ secret required | move callback to **SPA** |
| `invalid_request` | proxied **SPA** redemption lacks `Origin` | `/token` forwards the callback origin to Entra |

## What it creates

On the existing anonymous `well-known` API (it must already serve the Protected
Resource Metadata):

- `GET /.well-known/oauth-authorization-server` — AS metadata pointing at the facade
- `GET /authorize` — 302 → Entra, `resource` stripped, all other params preserved
- `POST /token` — proxy → Entra, `resource` stripped from the body, callback `Origin` forwarded
- an API-level `<cors>` policy (answers the `/token` preflight)
- repoints the PRM `authorization_servers` at the APIM gateway (keeps `resource` = server URL)

and, on Entra, moves the MCP client callback from the **Web** to the **SPA** platform
(secret-less PKCE). No tokens are ever read, logged, or stored.

## Use

```bash
mkdir -p ops/deploy
cp <plugin>/mcp/brain/deploy/azure-oauth-apim/apim_oauth_deploy.py  ops/deploy/
cp <plugin>/mcp/brain/deploy/azure-oauth-apim/deploy.example.toml   ops/deploy/oauth.toml
# edit ops/deploy/oauth.toml — APIM ids, Entra tenant/app ids, the MCP URL + client callback

az login   # a session with APIM contributor + Application.ReadWrite on the Entra app

python ops/deploy/apim_oauth_deploy.py plan   --profile ops/deploy/oauth.toml         # read-only
python ops/deploy/apim_oauth_deploy.py deploy --profile ops/deploy/oauth.toml --yes    # create facade + move callback
python ops/deploy/apim_oauth_deploy.py verify --profile ops/deploy/oauth.toml          # end-to-end re-check
```

`verify` checks: PRM `authorization_servers` = APIM, AS metadata served, `/authorize`
302s to Entra with `resource` stripped, `OPTIONS /token` returns 200 + CORS, and the
callback is registered under SPA. Then, in the client, remove + re-add the MCP
connector (a plain retry reuses cached discovery), enter the resource app id as the
client id when prompted, and sign in.

## Notes

- Idempotent — `deploy` re-PUTs the operations/policies and re-derives the SPA move, so
  it's safe to re-run after edits.
- Rollback: set the PRM `authorization_servers` back to
  `https://login.microsoftonline.com/<tenant>/v2.0`, delete the three facade operations,
  and move the callback back to the Web platform.
- This facade is needed for **any** Entra-fronted MCP server an RFC-8707 client connects
  to — not just the Brain.
- APIM/Graph writes may be blocked by agent sandboxes as "shared resources"; run the
  adapter in a normal shell / CI with the Azure CLI session.

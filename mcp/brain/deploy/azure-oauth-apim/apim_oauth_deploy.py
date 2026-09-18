#!/usr/bin/env python3
"""Project-owned adapter that fronts an APIM-hosted Brain MCP server with a
Microsoft Entra OAuth facade, so MCP clients (e.g. Claude) can authorize.

Why this exists: MCP clients send an RFC 8707 `resource=<server URL>` indicator
alongside `scope=api://<app>/<scope>`. Entra v2 rejects that pair with
`AADSTS9010010` (the `resource` must prefix-match the scope's audience, and an
`https://…azure-api.net/…` URL never matches an `api://` audience). The server URL
cannot be registered as an Entra identifier URI (unverified domain), and the client
requires the advertised PRM `resource` to equal the server URL — so the only fix is
to strip `resource` before it reaches Entra. This adapter turns the APIM instance
into a transparent OAuth authorization-server facade that does exactly that.

Same plan/deploy/verify contract as the sibling deploy adapters:
- `plan`   read-only; prints the operations, policies, and Entra change it would make.
- `deploy` requires --yes; creates the facade endpoints + policies and moves the
           MCP client's callback to the SPA platform.
- `verify` re-checks the live facade end to end (PRM, AS metadata, resource-stripping
           /authorize redirect, /token CORS preflight, SPA callback registration).

No secrets: it uses the Azure CLI session only. It never reads, prints, or stores a
token — the facade forwards Entra's own token response untouched.

Copy this file + deploy.example.toml into your project (default: ops/deploy/), edit
only the TOML, and run the subcommands after the Container App + Entra app exist.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore

API_VERSION = "2024-06-01-preview"
MGMT = "https://management.azure.com"

# --- APIM policy templates (tokens are literal placeholders, replaced per profile).
#     In rawxml, runtime '&' '<' '>' are stored as '&amp;' '&lt;' '&gt;'. ---
PRM = """<policies><inbound>
  <base />
  <return-response>
    <set-status code="200" reason="OK" />
    <set-header name="Content-Type" exists-action="override"><value>application/json</value></set-header>
    <set-body>{
  "resource": "__RESOURCE_URL__",
  "authorization_servers": [ "__GATEWAY__" ],
  "scopes_supported": [ "api://__RESOURCE_APP__/__SCOPE__" ],
  "bearer_methods_supported": [ "header" ]
}</set-body>
  </return-response>
</inbound><backend><base/></backend><outbound><base/></outbound><on-error><base/></on-error></policies>"""

AS_METADATA = """<policies><inbound>
  <base />
  <return-response>
    <set-status code="200" reason="OK" />
    <set-header name="Content-Type" exists-action="override"><value>application/json</value></set-header>
    <set-header name="Access-Control-Allow-Origin" exists-action="override"><value>*</value></set-header>
    <set-body>{
  "issuer": "__GATEWAY__",
  "authorization_endpoint": "__GATEWAY__/authorize",
  "token_endpoint": "__GATEWAY__/token",
  "response_types_supported": [ "code" ],
  "grant_types_supported": [ "authorization_code", "refresh_token" ],
  "code_challenge_methods_supported": [ "S256" ],
  "token_endpoint_auth_methods_supported": [ "none" ],
  "scopes_supported": [ "api://__RESOURCE_APP__/__SCOPE__", "offline_access", "openid", "profile" ]
}</set-body>
  </return-response>
</inbound><backend><base/></backend><outbound><base/></outbound><on-error><base/></on-error></policies>"""

AUTHORIZE = """<policies><inbound>
  <base />
  <return-response>
    <set-status code="302" reason="Found" />
    <set-header name="Location" exists-action="override">
      <value>@{
        var raw = context.Request.OriginalUrl.QueryString ?? "";
        if (raw.StartsWith("?")) { raw = raw.Substring(1); }
        var cleaned = System.Text.RegularExpressions.Regex.Replace(
          raw, "(^|&amp;)resource=[^&amp;]*", "",
          System.Text.RegularExpressions.RegexOptions.IgnoreCase);
        cleaned = cleaned.TrimStart('&amp;');
        return "https://login.microsoftonline.com/__TENANT__/oauth2/v2.0/authorize?" + cleaned;
      }</value>
    </set-header>
    <set-header name="Cache-Control" exists-action="override"><value>no-store</value></set-header>
  </return-response>
</inbound><backend><base/></backend><outbound><base/></outbound><on-error><base/></on-error></policies>"""

TOKEN = """<policies><inbound>
  <base />
  <set-backend-service base-url="https://login.microsoftonline.com/__TENANT__/oauth2/v2.0" />
  <rewrite-uri template="/token" copy-unmatched-params="false" />
  <set-header name="Content-Type" exists-action="override"><value>application/x-www-form-urlencoded</value></set-header>
  <set-header name="Origin" exists-action="override"><value>@(context.Request.Headers.GetValueOrDefault("Origin","__ORIGIN__"))</value></set-header>
  <set-body>@{
    var body = context.Request.Body.As&lt;string&gt;(preserveContent: true);
    if (string.IsNullOrEmpty(body)) { return body; }
    var pairs = body.Split('&amp;');
    var kept = new System.Text.StringBuilder();
    foreach (var p in pairs) {
      if (string.IsNullOrEmpty(p)) { continue; }
      var eq = p.IndexOf('=');
      var key = eq &gt;= 0 ? p.Substring(0, eq) : p;
      if (string.Equals(key, "resource", StringComparison.OrdinalIgnoreCase)) { continue; }
      if (kept.Length &gt; 0) { kept.Append("&amp;"); }
      kept.Append(p);
    }
    return kept.ToString();
  }</set-body>
</inbound><backend><base/></backend>
<outbound>
  <base />
  <set-header name="Access-Control-Allow-Origin" exists-action="override"><value>*</value></set-header>
</outbound><on-error><base/></on-error></policies>"""

API_CORS = """<policies>
  <inbound>
    <base />
    <cors allow-credentials="false" terminate-unmatched-request="false">
      <allowed-origins><origin>*</origin></allowed-origins>
      <allowed-methods preflight-result-max-age="300"><method>GET</method><method>POST</method><method>OPTIONS</method></allowed-methods>
      <allowed-headers><header>*</header></allowed-headers>
      <expose-headers><header>*</header></expose-headers>
    </cors>
  </inbound>
  <backend><base /></backend><outbound><base /></outbound><on-error><base /></on-error>
</policies>"""


def load(path: Path) -> dict:
    with path.open("rb") as handle:
        cfg = tomllib.load(handle)
    if cfg.get("version") != 1:
        raise ValueError("OAuth facade profile must contain version = 1")
    for section in ("apim", "entra", "mcp"):
        if not isinstance(cfg.get(section), dict):
            raise ValueError(f"missing [{section}] section")
    return cfg


def render(template: str, cfg: dict) -> str:
    apim, entra, mcp = cfg["apim"], cfg["entra"], cfg["mcp"]
    origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlparse(mcp["callback_url"]))
    return (template
            .replace("__TENANT__", entra["tenant_id"])
            .replace("__RESOURCE_APP__", entra["resource_app_id"])
            .replace("__SCOPE__", entra["scope"])
            .replace("__RESOURCE_URL__", mcp["resource_url"])
            .replace("__GATEWAY__", apim["gateway_url"].rstrip("/"))
            .replace("__ORIGIN__", origin))


def base_url(cfg: dict) -> str:
    a = cfg["apim"]
    return (f"{MGMT}/subscriptions/{a['subscription']}/resourceGroups/{a['resource_group']}"
            f"/providers/Microsoft.ApiManagement/service/{a['service']}")


def operations(cfg: dict) -> list[dict]:
    """The three anonymous facade operations to create on the well-known API."""
    return [
        {"id": "oauth-as-metadata", "method": "GET", "url": "/.well-known/oauth-authorization-server",
         "display": "OAuth AS Metadata", "policy": AS_METADATA},
        {"id": "authorize", "method": "GET", "url": "/authorize",
         "display": "OAuth Authorize (Entra facade)", "policy": AUTHORIZE},
        {"id": "token", "method": "POST", "url": "/token",
         "display": "OAuth Token (Entra facade)", "policy": TOKEN},
    ]


def az(args: list[str], capture: bool = True) -> str:
    result = subprocess.run(["az", *args], check=True, text=True,
                            capture_output=capture)
    return result.stdout if capture else ""


def az_rest(method: str, url: str, body: str | None = None) -> str:
    args = ["rest", "--method", method, "--url", url]
    if body is not None:
        args += ["--headers", "Content-Type=application/json", "--body", body]
    return az(args)


def put_policy(cfg: dict, scope_path: str, xml: str) -> None:
    body = json.dumps({"properties": {"format": "rawxml", "value": render(xml, cfg)}})
    az_rest("PUT", f"{base_url(cfg)}/{scope_path}/policies/policy?api-version={API_VERSION}", body)


PRM_URL_TEMPLATE = "/.well-known/oauth-protected-resource/*"


def put_operation(cfg: dict, op_id: str, method: str, url: str, display: str) -> None:
    api = cfg["apim"]["wellknown_api"]
    body = json.dumps({"properties": {"displayName": display, "method": method,
                                      "urlTemplate": url, "templateParameters": [], "responses": []}})
    az_rest("PUT", f"{base_url(cfg)}/apis/{api}/operations/{op_id}?api-version={API_VERSION}", body)


def ensure_wellknown_api(cfg: dict) -> None:
    """Create the anonymous well-known API (path ""). It is NOT present by default;
    a plain PUT is idempotent, so re-running deploy is safe."""
    api = cfg["apim"]["wellknown_api"]
    body = json.dumps({"properties": {"displayName": api, "path": cfg["apim"].get("wellknown_path", ""),
                                      "protocols": ["https"], "subscriptionRequired": False}})
    az_rest("PUT", f"{base_url(cfg)}/apis/{api}?api-version={API_VERSION}", body)


def deploy(cfg: dict) -> None:
    api = cfg["apim"]["wellknown_api"]
    prm_op = cfg["apim"].get("prm_operation_id", "get")
    # 1. the anonymous well-known API (created if absent) and its PRM operation
    ensure_wellknown_api(cfg)
    put_operation(cfg, prm_op, "GET", PRM_URL_TEMPLATE, "OAuth Protected Resource Metadata")
    # 2. facade operations
    for op in operations(cfg):
        put_operation(cfg, op["id"], op["method"], op["url"], op["display"])
    # 3. API-level CORS (must precede the operation return-responses so preflight is answered)
    put_policy(cfg, f"apis/{api}", API_CORS)
    # 4. PRM + facade endpoint policies
    put_policy(cfg, f"apis/{api}/operations/{prm_op}", PRM)
    for op in operations(cfg):
        put_policy(cfg, f"apis/{api}/operations/{op['id']}", op["policy"])
    # 5. move the MCP client callback to the SPA platform (secret-less PKCE redemption)
    move_callback_to_spa(cfg)


def move_callback_to_spa(cfg: dict) -> None:
    entra, mcp = cfg["entra"], cfg["mcp"]
    obj = entra["resource_app_object_id"]
    callback = mcp["callback_url"]
    current = json.loads(az(["ad", "app", "show", "--id", entra["resource_app_id"],
                             "--query", "{web:web.redirectUris, spa:spa.redirectUris}", "-o", "json"]))
    web = [u for u in (current.get("web") or []) if u != callback]
    spa = sorted(set((current.get("spa") or []) + [callback]))
    body = json.dumps({"web": {"redirectUris": web}, "spa": {"redirectUris": spa}})
    az_rest("PATCH", f"https://graph.microsoft.com/v1.0/applications/{obj}", body)


def sanitized_plan(cfg: dict, profile: Path, checks: list[dict]) -> dict:
    apim, entra, mcp = cfg["apim"], cfg["entra"], cfg["mcp"]
    api = apim["wellknown_api"]
    prm_op = apim.get("prm_operation_id", "get")
    return {
        "version": 1,
        "provider": "azure-oauth-apim",
        "profile": str(profile),
        "apim": {"service": apim["service"], "resource_group": apim["resource_group"], "api": api,
                 "gateway_url": apim["gateway_url"]},
        "preflight": checks,
        "ensures_api": f"{api} (anonymous, path '{apim.get('wellknown_path', '')}') — created if absent",
        "creates_operations": [f"GET {PRM_URL_TEMPLATE} (id {prm_op}) — PRM",
                               *[f"{op['method']} {op['url']} (id {op['id']})" for op in operations(cfg)]],
        "sets_policies": [f"apis/{api} (CORS)", f"apis/{api}/operations/{prm_op} (PRM)",
                          *[f"apis/{api}/operations/{op['id']}" for op in operations(cfg)]],
        "prm_change": {"resource": mcp["resource_url"], "authorization_servers": [apim["gateway_url"].rstrip("/")]},
        "entra_change": {"app": entra["resource_app_id"], "callback": mcp["callback_url"], "platform": "Web -> SPA"},
        "credentials": "Azure CLI session only; no secrets and no tokens read or printed",
        "post_deploy": ["PRM authorization_servers = APIM", "AS metadata served", "/authorize 302 strips resource",
                        "OPTIONS /token 200 CORS", "callback registered under SPA"],
    }


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):  # capture the 302, do not follow
        return None


def _get(url: str, headers: dict[str, str] | None = None, method: str = "GET"):
    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(url, headers=headers or {}, method=method)
    try:
        return opener.open(request, timeout=30)
    except urllib.error.HTTPError as error:
        return error


def verify(cfg: dict) -> dict:
    apim, entra, mcp = cfg["apim"], cfg["entra"], cfg["mcp"]
    gateway = apim["gateway_url"].rstrip("/")
    resource_path = urllib.parse.urlparse(mcp["resource_url"]).path.lstrip("/")

    prm = json.load(_get(f"{gateway}/.well-known/oauth-protected-resource/{resource_path}"))
    if prm.get("authorization_servers") != [gateway] or prm.get("resource") != mcp["resource_url"]:
        raise RuntimeError("PRM does not point authorization_servers at APIM / wrong resource")

    meta = json.load(_get(f"{gateway}/.well-known/oauth-authorization-server"))
    if meta.get("token_endpoint") != f"{gateway}/token" or meta.get("authorization_endpoint") != f"{gateway}/authorize":
        raise RuntimeError("AS metadata endpoints are wrong")

    query = urllib.parse.urlencode({
        "response_type": "code", "client_id": entra["resource_app_id"], "redirect_uri": mcp["callback_url"],
        "scope": f"api://{entra['resource_app_id']}/{entra['scope']} offline_access", "state": "verify",
        "code_challenge": "verify", "code_challenge_method": "S256", "resource": mcp["resource_url"]})
    authz = _get(f"{gateway}/authorize?{query}")
    location = authz.headers.get("Location", "")
    if authz.status != 302 or "login.microsoftonline.com" not in location or "resource=" in location:
        raise RuntimeError("/authorize did not 302 to Entra with resource stripped")

    cors = _get(f"{gateway}/token", {"Origin": "https://claude.ai",
                "Access-Control-Request-Method": "POST"}, method="OPTIONS")
    if cors.status != 200 or not cors.headers.get("Access-Control-Allow-Origin"):
        raise RuntimeError("/token CORS preflight did not return 200 with CORS headers")

    app = json.loads(az(["ad", "app", "show", "--id", entra["resource_app_id"],
                         "--query", "{spa:spa.redirectUris}", "-o", "json"]))
    if mcp["callback_url"] not in (app.get("spa") or []):
        raise RuntimeError("callback is not registered under the SPA platform")

    return {"status": "ok", "prm_authorization_servers": prm["authorization_servers"],
            "as_token_endpoint": meta["token_endpoint"], "authorize_resource_stripped": True,
            "token_cors_preflight": cors.status, "callback_platform": "spa"}


def _last_line(exc: subprocess.CalledProcessError) -> str:
    text = (exc.stderr or exc.stdout or "").strip()
    return text.splitlines()[-1] if text else str(exc)


def preflight(cfg: dict) -> list[dict]:
    """Read-only checks that the base (which this adapter does NOT create) is in place:
    APIM service, the Entra resource app + scope + registered callback + v2 tokens, and
    the MCP endpoint's discovery challenge. The well-known API is intentionally NOT
    checked — deploy creates it. A failed non-optional check blocks deploy."""
    apim, entra, mcp = cfg["apim"], cfg["entra"], cfg["mcp"]
    checks: list[dict] = []
    try:
        az_rest("GET", f"{base_url(cfg)}?api-version={API_VERSION}")
        checks.append({"check": f"APIM service '{apim['service']}' reachable", "ok": True})
    except subprocess.CalledProcessError as exc:
        checks.append({"check": f"APIM service '{apim['service']}' reachable", "ok": False, "detail": _last_line(exc)})
    try:
        app = json.loads(az(["ad", "app", "show", "--id", entra["resource_app_id"], "--query",
              "{scopes:api.oauth2PermissionScopes[].value, web:web.redirectUris, spa:spa.redirectUris,"
              " atv:api.requestedAccessTokenVersion}", "-o", "json"]))
        checks.append({"check": f"Entra app {entra['resource_app_id']} exists", "ok": True})
        checks.append({"check": f"scope '{entra['scope']}' exposed", "ok": entra["scope"] in (app.get("scopes") or [])})
        registered = mcp["callback_url"] in ((app.get("web") or []) + (app.get("spa") or []))
        checks.append({"check": "client callback registered on the app", "ok": registered})
        checks.append({"check": "accessTokenVersion == 2", "ok": app.get("atv") == 2})
    except subprocess.CalledProcessError as exc:
        checks.append({"check": f"Entra app {entra['resource_app_id']} exists", "ok": False, "detail": _last_line(exc)})
    try:
        www = _get(mcp["resource_url"], method="GET").headers.get("WWW-Authenticate", "")
        checks.append({"check": "MCP endpoint challenges discovery (WWW-Authenticate resource_metadata)",
                       "ok": "resource_metadata" in www, "optional": True,
                       "detail": (www[:120] or "no WWW-Authenticate header")})
    except (urllib.error.URLError, OSError) as exc:
        checks.append({"check": "MCP endpoint reachable", "ok": False, "optional": True, "detail": str(exc)})
    return checks


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("plan", "deploy"):
        p = sub.add_parser(name); p.add_argument("--profile", required=True); p.add_argument("--yes", action="store_true")
    sub.add_parser("verify").add_argument("--profile", required=True)
    a = ap.parse_args(argv)
    try:
        profile = Path(a.profile).expanduser().resolve(); cfg = load(profile)
        if a.command == "verify":
            print(json.dumps(verify(cfg), indent=2)); return 0
        checks = preflight(cfg)
        print(json.dumps(sanitized_plan(cfg, profile, checks), indent=2))
        if a.command == "plan":
            return 0
        if not a.yes:
            raise RuntimeError("deployment requires --yes after reviewing the plan")
        blocking = [c["check"] for c in checks if not c["ok"] and not c.get("optional")]
        if blocking:
            raise RuntimeError("preflight failed — fix the base first: " + "; ".join(blocking))
        deploy(cfg)
        print(json.dumps(verify(cfg), indent=2))
        return 0
    except (ValueError, KeyError, FileNotFoundError, RuntimeError,
            subprocess.CalledProcessError, urllib.error.URLError) as exc:
        print(f"APIM OAuth adapter error: {exc}", file=sys.stderr); return 2


if __name__ == "__main__":
    raise SystemExit(main())

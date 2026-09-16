#!/usr/bin/env node
import http from "node:http";
import https from "node:https";
import fs from "node:fs";

const mcpEndpoint = process.env.BRAIN_MCP_ENDPOINT;
const apiKeyEnvVar = process.env.BRAIN_API_KEY_VAR;
const port = Number(process.env.BRAIN_BRIDGE_PORT || 4318);
const host = "127.0.0.1";

if (!mcpEndpoint) { console.error("BRAIN_MCP_ENDPOINT is not set"); process.exit(1); }
if (!apiKeyEnvVar) { console.error("BRAIN_API_KEY_VAR is not set"); process.exit(1); }

const target = new URL(mcpEndpoint);

function readSecret() {
  const envFile = process.env.BRAIN_ENV_FILE;
  if (!envFile) throw new Error("BRAIN_ENV_FILE is not set");
  const text = fs.readFileSync(envFile, "utf8");
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const i = line.indexOf("=");
    if (i < 1 || line.slice(0, i).trim() !== apiKeyEnvVar) continue;
    let v = line.slice(i + 1).trim();
    if (v.length >= 2 && v[0] === v.at(-1) && ["'", '"'].includes(v[0])) v = v.slice(1, -1);
    if (v) return v;
  }
  throw new Error(`${apiKeyEnvVar} not found in ${envFile}`);
}

const hopHeaders = new Set(["host", "connection", "content-length", "x-api-key"]);
const responseHopHeaders = new Set(["connection", "content-length", "transfer-encoding"]);

http.createServer((req, res) => {
  if (req.url === "/healthz") {
    res.writeHead(200, { "content-type": "application/json" });
    return res.end('{"status":"ok"}');
  }
  if (req.url !== "/mcp") {
    res.writeHead(404, { "content-type": "application/json" });
    return res.end(JSON.stringify({ error: "not found", hint: "endpoint is /mcp" }));
  }
  let key;
  try { key = readSecret(); }
  catch (e) {
    res.writeHead(503, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: e.message }));
    req.resume(); // drain body so keep-alive connection stays usable
    return;
  }

  const headers = {};
  for (const [k, v] of Object.entries(req.headers))
    if (!hopHeaders.has(k.toLowerCase()) && v !== undefined) headers[k] = v;
  headers.host = target.host;
  headers["x-api-key"] = key;

  const up = https.request(
    { hostname: target.hostname, port: Number(target.port) || 443, path: target.pathname + (target.search || ""), method: req.method, headers },
    (u) => {
      const h = {};
      for (const [k, v] of Object.entries(u.headers))
        if (!responseHopHeaders.has(k.toLowerCase()) && v !== undefined) h[k] = v;
      res.writeHead(u.statusCode || 502, h);
      u.pipe(res);
    }
  );
  up.on("error", (e) => {
    if (!res.headersSent) {
      res.writeHead(502);
      res.end(JSON.stringify({ error: "upstream unavailable", detail: e.code || e.message }));
    } else {
      res.destroy();
    }
  });
  req.on("close", () => up.destroy());
  req.pipe(up);
}).listen(port, host, () =>
  console.log(`Brain MCP bridge listening at http://${host}:${port}/mcp → ${mcpEndpoint}`)
);

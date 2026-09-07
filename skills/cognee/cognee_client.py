#!/usr/bin/env python3
"""Minimal, dependency-free Cognee REST client (stdlib only).

Connection-agnostic: base URL from --url or $COGNEE_URL. Auth is optional — if
$COGNEE_USER/$COGNEE_PASS are set it logs in (form-encoded) and sends a Bearer
token; on a no-auth deployment it just calls directly.

Subcommands:
  datasets                                   list datasets (brains)
  upload-ontology --key K --file F.owl [--description D]
  cognify --dataset NAME [--ontology-key K ...] [--background]
  search  --dataset NAME --query Q [--type GRAPH_COMPLETION] [--top-k 15]
  status  --dataset UUID

Examples:
  export COGNEE_URL=http://192.168.68.123:8000
  cognee_client.py upload-ontology --key primo --file schema/primo.owl
  cognee_client.py cognify --dataset primo --ontology-key primo --background
  cognee_client.py search --dataset primo --query "what is this brain about?"
"""
import argparse, json, os, sys, uuid, urllib.request, urllib.parse, urllib.error, socket

def base_url(a):
    u = a.url or os.environ.get("COGNEE_URL")
    if not u: sys.exit("error: set --url or $COGNEE_URL")
    return u.rstrip("/")

def token(base):
    user, pw = os.environ.get("COGNEE_USER"), os.environ.get("COGNEE_PASS")
    if not user: return None                       # no-auth deployment
    data = urllib.parse.urlencode({"username": user, "password": pw or ""}).encode()
    req = urllib.request.Request(base + "/api/v1/auth/login", data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    return json.load(urllib.request.urlopen(req, timeout=30))["access_token"]

def hdrs(tok, extra=None):
    h = dict(extra or {})
    if tok: h["Authorization"] = "Bearer " + tok
    return h

def call(base, tok, method, path, jbody=None, timeout=600):
    data = json.dumps(jbody).encode() if jbody is not None else None
    h = hdrs(tok, {"Content-Type": "application/json"} if data else {})
    req = urllib.request.Request(base + path, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode()
    try: return json.loads(raw)
    except ValueError: return raw

def multipart(fields, files):
    """fields: {name: value}; files: [(field, filename, bytes, content_type)]."""
    b = "----cognee" + uuid.uuid4().hex
    body = b""
    for k, v in fields.items():
        body += (f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n').encode()
    for field, fname, content, ctype in files:
        body += (f'--{b}\r\nContent-Disposition: form-data; name="{field}"; filename="{fname}"\r\n'
                 f'Content-Type: {ctype}\r\n\r\n').encode() + content + b"\r\n"
    body += f'--{b}--\r\n'.encode()
    return b, body

def multipart_post(base, tok, path, fields, files, timeout=300):
    boundary, body = multipart(fields, files)
    req = urllib.request.Request(base + path, data=body,
        headers=hdrs(tok, {"Content-Type": f"multipart/form-data; boundary={boundary}"}), method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode()

def upload_ontology(base, tok, key, path, desc):
    fields = {"ontology_key": key}
    if desc: fields["description"] = desc
    with open(path, "rb") as f: content = f.read()
    files = [("ontology_file", os.path.basename(path), content, "application/rdf+xml")]
    return multipart_post(base, tok, "/api/v1/ontologies", fields, files, timeout=120)

def add_data(base, tok, dataset, paths, node_set, background):
    fields = {"datasetName": dataset}
    if node_set: fields["node_set"] = node_set
    if background: fields["run_in_background"] = "true"
    files = []
    for p in paths:
        with open(p, "rb") as f:
            files.append(("data", os.path.basename(p), f.read(), "text/markdown"))
    return multipart_post(base, tok, "/api/v1/add", fields, files)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["datasets", "add", "upload-ontology", "cognify", "search", "status"])
    ap.add_argument("--url"); ap.add_argument("--key"); ap.add_argument("--file")
    ap.add_argument("--files", nargs="+", default=[]); ap.add_argument("--node-set")
    ap.add_argument("--description"); ap.add_argument("--dataset")
    ap.add_argument("--ontology-key", action="append", default=[])
    ap.add_argument("--custom-prompt"); ap.add_argument("--custom-prompt-file")
    ap.add_argument("--background", action="store_true")
    ap.add_argument("--query"); ap.add_argument("--type", default="GRAPH_COMPLETION")
    ap.add_argument("--top-k", type=int, default=15)
    a = ap.parse_args()
    base = base_url(a); tok = token(base)

    if a.cmd == "datasets":
        print(json.dumps(call(base, tok, "GET", "/api/v1/datasets"), indent=2))
    elif a.cmd == "add":
        if not (a.dataset and a.files): sys.exit("need --dataset and --files")
        print(add_data(base, tok, a.dataset, a.files, a.node_set, a.background))
    elif a.cmd == "upload-ontology":
        if not (a.key and a.file): sys.exit("need --key and --file")
        print(upload_ontology(base, tok, a.key, a.file, a.description))
    elif a.cmd == "cognify":
        body = {"datasets": [a.dataset], "runInBackground": a.background}
        if a.ontology_key: body["ontologyKey"] = a.ontology_key
        cp = a.custom_prompt
        if a.custom_prompt_file: cp = open(a.custom_prompt_file).read()
        if cp: body["customPrompt"] = cp
        print(json.dumps(call(base, tok, "POST", "/api/v1/cognify", body), indent=2))
    elif a.cmd == "search":
        body = {"searchType": a.type, "query": a.query, "datasets": [a.dataset], "topK": a.top_k}
        print(json.dumps(call(base, tok, "POST", "/api/v1/search", body), indent=2))
    elif a.cmd == "status":
        print(json.dumps(call(base, tok, "GET", f"/api/v1/datasets/status?dataset={a.dataset}"), indent=2))

if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        body = ""
        try: body = e.read().decode()[:400]
        except Exception: pass
        sys.exit(f"HTTP {e.code} {e.reason} at {e.url}\n{body}")
    except (urllib.error.URLError, socket.timeout, ConnectionError) as e:
        sys.exit(f"cannot reach Cognee at {os.environ.get('COGNEE_URL','?')}: {getattr(e,'reason',e)}")

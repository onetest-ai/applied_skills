"""Pull/merge requests and issues/work items from a code host, through the CLI the user is already signed in to:
`gh` (GitHub), `glab` (GitLab) or `az` (Azure DevOps, with the azure-devops extension). Taska holds no tokens.

Every provider is mapped to the same evidence shape. A new item is evidence when it is first seen, and again only
when its state or title changes (opened → merged/closed/reopened, active → completed/abandoned), never on every
comment, so a busy repository does not flood the ledger.

    [sources.app]
    adapter = "code_host"
    provider = "github"            # github | gitlab | azure
    repo = "org/app"               # github/gitlab: owner/name; azure: repository name
    organization = "https://dev.azure.com/org"   # azure only
    project = "Platform"                          # azure only
    include = ["prs", "issues"]    # azure: "prs", "workitems"
    since = "2026-01-05"
"""
import datetime as dt
import json
import shutil
import subprocess

from .. import store as S

CLI = {"github": "gh", "gitlab": "glab", "azure": "az"}
BODY_CHARS = 1200


class HostError(RuntimeError):
    pass


def _run(args, timeout=120):
    if not shutil.which(args[0]):
        raise HostError(f"{args[0]} is not on PATH")
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        raise HostError((r.stderr or r.stdout).strip()[-400:])
    return json.loads(r.stdout or "[]")


# ---------- providers: fetch -> [{key, kind, number, title, body, state, author, created, updated, closed, merged, url, ref}]
def fetch_github(scfg, since, run=_run):
    repo, out = scfg["repo"], []
    if "prs" in scfg.get("include", ["prs", "issues"]):
        for p in run(["gh", "pr", "list", "--repo", repo, "--state", "all", "--search", f"updated:>={since}",
                      "--limit", str(scfg.get("limit", 200)), "--json",
                      "number,title,body,state,author,createdAt,updatedAt,mergedAt,closedAt,url,headRefName"]):
            out.append({"key": f"pr#{p['number']}", "kind": "pr", "number": p["number"], "title": p["title"],
                        "body": p.get("body") or "", "state": p["state"].lower(),
                        "author": (p.get("author") or {}).get("login"), "created": p["createdAt"],
                        "updated": p["updatedAt"], "closed": p.get("closedAt"), "merged": p.get("mergedAt"),
                        "url": p["url"], "ref": p.get("headRefName")})
    if "issues" in scfg.get("include", ["prs", "issues"]):
        for i in run(["gh", "issue", "list", "--repo", repo, "--state", "all", "--search", f"updated:>={since}",
                      "--limit", str(scfg.get("limit", 200)), "--json",
                      "number,title,body,state,author,createdAt,updatedAt,closedAt,url,labels"]):
            out.append({"key": f"issue#{i['number']}", "kind": "issue", "number": i["number"], "title": i["title"],
                        "body": i.get("body") or "", "state": i["state"].lower(),
                        "author": (i.get("author") or {}).get("login"), "created": i["createdAt"],
                        "updated": i["updatedAt"], "closed": i.get("closedAt"), "merged": None, "url": i["url"],
                        "ref": ", ".join(l["name"] for l in i.get("labels") or [])})
    return out


def fetch_gitlab(scfg, since, run=_run):
    repo, out = scfg["repo"], []
    if "prs" in scfg.get("include", ["prs", "issues"]):
        for m in run(["glab", "mr", "list", "-R", repo, "--all", "-F", "json", "-P", str(scfg.get("limit", 100))]):
            if (m.get("updated_at") or "") < since:
                continue
            out.append({"key": f"mr!{m['iid']}", "kind": "pr", "number": m["iid"], "title": m["title"],
                        "body": m.get("description") or "", "state": m["state"].lower(),
                        "author": (m.get("author") or {}).get("username"), "created": m["created_at"],
                        "updated": m["updated_at"], "closed": m.get("closed_at"), "merged": m.get("merged_at"),
                        "url": m.get("web_url"), "ref": m.get("source_branch")})
    if "issues" in scfg.get("include", ["prs", "issues"]):
        for i in run(["glab", "issue", "list", "-R", repo, "--all", "-F", "json", "-P", str(scfg.get("limit", 100))]):
            if (i.get("updated_at") or "") < since:
                continue
            out.append({"key": f"issue#{i['iid']}", "kind": "issue", "number": i["iid"], "title": i["title"],
                        "body": i.get("description") or "", "state": i["state"].lower(),
                        "author": (i.get("author") or {}).get("username"), "created": i["created_at"],
                        "updated": i["updated_at"], "closed": i.get("closed_at"), "merged": None,
                        "url": i.get("web_url"), "ref": ", ".join(i.get("labels") or [])})
    return out


def fetch_azure(scfg, since, run=_run):
    org, proj, out = scfg["organization"], scfg["project"], []
    if "prs" in scfg.get("include", ["prs", "workitems"]):
        args = ["az", "repos", "pr", "list", "--organization", org, "--project", proj, "--status", "all",
                "--top", str(scfg.get("limit", 200)), "--output", "json"]
        if scfg.get("repo"):
            args += ["--repository", scfg["repo"]]
        for p in run(args):
            changed = p.get("closedDate") or p.get("creationDate") or ""
            if changed < since:
                continue
            status = {"completed": "merged", "abandoned": "closed", "active": "open"}.get(p.get("status"), p.get("status"))
            repo = p.get("repository") or {}
            out.append({"key": f"pr#{p['pullRequestId']}", "kind": "pr", "number": p["pullRequestId"],
                        "title": p["title"], "body": p.get("description") or "", "state": status,
                        "author": (p.get("createdBy") or {}).get("displayName"), "created": p.get("creationDate"),
                        "updated": changed, "closed": p.get("closedDate"),
                        "merged": p.get("closedDate") if status == "merged" else None,
                        "url": f"{repo.get('webUrl', '')}/pullrequest/{p['pullRequestId']}" if repo.get("webUrl") else None,
                        "ref": (p.get("sourceRefName") or "").replace("refs/heads/", "")})
    if "workitems" in scfg.get("include", ["prs", "workitems"]):
        wiql = ("SELECT [System.Id] FROM workitems WHERE [System.TeamProject] = @project "
                f"AND [System.ChangedDate] >= '{since}' ORDER BY [System.ChangedDate] DESC")
        for w in run(["az", "boards", "query", "--organization", org, "--project", proj, "--wiql", wiql,
                      "--output", "json"])[: scfg.get("limit", 200)]:
            f = w.get("fields") or {}
            out.append({"key": f"wi#{w['id']}", "kind": "issue", "number": w["id"], "title": f.get("System.Title", ""),
                        "body": f.get("System.Description") or "", "state": (f.get("System.State") or "").lower(),
                        "author": (f.get("System.AssignedTo") or {}).get("displayName")
                        if isinstance(f.get("System.AssignedTo"), dict) else f.get("System.AssignedTo"),
                        "created": f.get("System.CreatedDate"), "updated": f.get("System.ChangedDate"),
                        "closed": f.get("Microsoft.VSTS.Common.ClosedDate"), "merged": None,
                        "url": f"{org.rstrip('/')}/{proj}/_workitems/edit/{w['id']}",
                        "ref": f.get("System.WorkItemType")})
    return out


FETCH = {"github": fetch_github, "gitlab": fetch_gitlab, "azure": fetch_azure}


def _local(ts, tzname):
    if not ts:
        return None
    try:
        from zoneinfo import ZoneInfo
        z = ZoneInfo(tzname or "UTC")
    except Exception:
        z = None
    x = dt.datetime.fromisoformat(ts.replace("Z", "+00:00")[:32])
    if x.tzinfo is None:
        x = x.replace(tzinfo=dt.timezone.utc)
    return x.astimezone(z).replace(tzinfo=None).isoformat(timespec="seconds")


def _text(it, what, cfg):
    who = cfg.canonical(it["author"]) if it.get("author") else "unknown"
    noun = {"pr": "Pull request", "issue": "Issue"}[it["kind"]]
    num = it["key"].split("#")[-1].split("!")[-1]
    head = f"{noun} {'!' if it['key'].startswith('mr!') else '#'}{num} “{it['title']}” {what}"
    when = it.get("merged") or it.get("closed") or it.get("updated")
    head += f" ({when[:10] if when else ''}; by {who}{'; ' + it['ref'] if it.get('ref') else ''})."
    body = " ".join((it.get("body") or "").split())[:BODY_CHARS]
    return head + (f"\n\n{body}" if body else "")


def sync(cfg, key, scfg, store, cursor):
    provider = scfg.get("provider", "github")
    if provider not in FETCH:
        return cursor, {"source": key, "status": "unavailable", "reason": f"provider must be one of {sorted(FETCH)}"}
    cur = dict(cursor or {})
    since = cur.get("_since") or scfg.get("since") or (dt.date.today() - dt.timedelta(days=14)).isoformat()
    try:
        items = FETCH[provider](scfg, since)
    except (HostError, KeyError, json.JSONDecodeError, subprocess.SubprocessError) as e:
        return cur, {"source": key, "status": "unavailable", "reason": f"{CLI[provider]}: {e}"}
    new, changed = [], []
    for it in sorted(items, key=lambda x: x.get("updated") or ""):
        state = "merged" if it.get("merged") else it["state"]
        sig = f"{state}|{it['title']}"
        prev = cur.get(it["key"])
        if prev == sig:
            continue
        what = (state if prev is None and state in ("merged", "closed") else
                "opened" if prev is None else f"now {state}" if prev.split("|")[0] != state else "renamed")
        at = _local(it.get("merged") or it.get("closed") or it.get("updated") or it.get("created"), cfg.timezone)
        item = {"id": S.make_id(at, key, f"{it['key']}|{sig}"), "source": key, "kind": it["kind"], "at": at,
                "title": f"{it['key']} {it['title']}", "text": _text(it, what, cfg), "url": it.get("url"),
                "meta": {"provider": provider, "state": state, "what": what, "author": cfg.canonical(it.get("author"))}}
        if S.write_evidence(store, item):
            (changed if prev else new).append(item["id"])
        cur[it["key"]] = sig
    cur["_since"] = (dt.date.today() - dt.timedelta(days=1)).isoformat()  # re-list a day back: updates land late
    return cur, {"source": key, "status": "ok", "new": new, "changed": changed}

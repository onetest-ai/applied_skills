"""Delivery of a week's report: `deliver` builds the mail content (reports/WEEK/deliver.json, the shape a mail step
and the dashboard read) and a ready-to-send `mail.md` the owner can paste into any mail client: subject, To, Cc, body,
and the attachments the schedule's Include checklist selects and that exist. Mail is never required: a project with a
mail route sends deliver.json itself.

The send decision (`decide`): only the file the newest gate passed, only while the ledger is unchanged since that gate,
never the same week twice; a live send always carries the owner in cc (`recipients`).

Settings, all in `[report]` of taska.toml: title (default: the project name), greet (the reader's first name), sender (a
[[people]] name), org, folder_url (with `{week}`), docx_glob."""
import datetime as dt
import json
import re
from pathlib import Path

from . import ledger as L

MON = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
CITE = re.compile(r"\s+\[[A-Z]\d{3}(?:,\s*[A-Z]\d{3})*\]")
md_ = lambda x: f"{MON[x.month - 1]} {x.day}"


def decide(verdict, ledger_mtime, gate_mtime, sent, resend=False):
    """("send" | "skip" | "hold", why). `sent`: this week already went to the reader. Every rebuild stamps its build
    time, so the week, not the file, is what must never be sent twice; `resend` overrides."""
    if not verdict.get("ok"):
        return "hold", verdict.get("why", "the gate did not pass")
    if ledger_mtime > gate_mtime:
        return "hold", "the ledger changed after the gate ran: rebuild and re-run the gate"
    if sent and not resend:
        return "skip", "this week was already sent to the reader; --resend sends it again"
    return "send", verdict.get("why", "gated")


def recipients(mode, to, cc, owner):
    """(to, cc, subject prefix): live goes to the reader with the owner always in cc; anything else is a shadow send
    to the owner only, the subject saying who it would have gone to."""
    if mode == "live":
        cc = list(cc) + ([owner] if owner.lower() not in {c.lower() for c in cc} else [])
        return [to], cc, ""
    return [owner], [], f"[Shadow: would go to {to}" + (f", cc {', '.join(cc)}" if cc else "") + "] "


def was_sent(rc):
    """A mail step's exit: 0 sent; 14 = a recipient was refused after the mail went to the others, so it was sent."""
    return rc in (0, 14)


def read_mode(path):
    try:
        return json.loads(Path(path).read_text()).get("mode") or "shadow"
    except (OSError, ValueError):
        return "shadow"


def report_docx(week_dir, pattern="*.docx"):
    """The newest report of the week by name (names carry the build day), never a Word lock file."""
    files = [f for f in Path(week_dir).glob(pattern) if not f.name.startswith(("~$", "."))]
    return max(files, key=lambda f: f.name) if files else None


def minimal_held(week, reader, why, owner, title, greet="there"):
    """A held notice that needs nothing but the reason, for when the week's mail content cannot be built."""
    return {"week": week, "to": [owner], "preview_to": [owner], "cc": [], "greet": greet,
            "subject": f"[HELD, not sent to {reader}] {title} {week}", "lead": f"Not sent to {reader}: {why}."}


def week_label(week):
    w0 = dt.date.fromisoformat(week)
    w1 = w0 + dt.timedelta(days=4)
    return f"{md_(w0)}–{w1.day if w1.month == w0.month else md_(w1)}, {w1.year}"


def deliver(cfg, week_dir, to, schedule=None, status=None, kind="preview", today=None):
    """The week's mail content as a dict (written to week_dir/deliver.json) and mail.md beside it."""
    d = Path(week_dir)
    week = d.name
    rep = cfg.report
    title = rep.get("title") or cfg.name
    include = dict(schedule.include) if schedule else {"body": True, "docx": True, "link": True, "pptx": False, "workbench": False}
    gates = json.loads(Path(status).read_text())["steps"] if status and Path(status).exists() else []
    gates = list({g["id"]: g for g in gates}.values())   # each step by its latest result
    lead, summary, prios = "", [], []
    if (d / "prose.json").exists():
        p = json.loads((d / "prose.json").read_text())
        lead = CITE.sub("", p.get("lead", ""))
        summary = [CITE.sub("", s) for s in p.get("slide3", {}).get("summary", [])]
        prios = p.get("priorities") or []
    docs = sorted((x for x in d.glob(rep.get("docx_glob") or "*.docx") if x.name != "check.docx" and not x.name.startswith(("~$", "."))),
                  key=lambda x: x.stat().st_mtime, reverse=True)
    version = built = None
    chk = Path(str(docs[0]) + ".check.json") if docs else None
    if chk and chk.exists():
        c = json.loads(chk.read_text())
        version, built = c.get("version"), c.get("built")
        gates.append({"id": "prose_checked", "ok": not c["errors"],
                      "detail": f"{len(c['slots'])} slots, {len(c['errors'])} failed (marked (confirm) in the docx)"})
    failed = [g for g in gates if not g["ok"]]
    N, *_ = L.fold(cfg.store)
    today = today or dt.date.today()
    coming = []
    for r in prios[:5]:
        n = N.get(r.get("ref"))
        if not n or n["state"] in ("done", "cancelled", "merged", "deleted"):
            continue
        dd = dt.date.fromisoformat(n["due"]) if n.get("due") else None
        coming.append({"title": r.get("label") or n["title"], "due": md_(dd) if dd else "", "overdue": bool(dd and dd < today)})
    sender = rep.get("sender") or ""
    me = next((x for x in cfg.people if x.name == sender), None)
    attach = (docs[:1] if include.get("docx") else [])
    if include.get("pptx"):
        attach += sorted(d.glob("*.pptx"), key=lambda x: x.stat().st_mtime, reverse=True)[:1]
    if include.get("workbench"):
        attach += sorted(d.glob("workbench*.html"), key=lambda x: x.stat().st_mtime, reverse=True)[:1]
    link = rep["folder_url"].format(week=week) if rep.get("folder_url") and docs and include.get("link", True) else None
    out = {"kind": kind, "week": week, "week_label": week_label(week), "to": [to], "preview_to": [to],
           "cc": list(schedule.cc) if schedule else [], "subject": f"{title} – Weekly status, {week_label(week)}",
           "version": version, "built": built,
           "sharepoint": next((g["detail"] for g in reversed(gates) if g["id"] == "publish_verified" and not g["ok"]), None),
           "needs_attention": bool(failed), "greet": rep.get("greet") or "there", "lead": lead, "summary": summary,
           "coming": coming, "link": link, "attach": [str(x) for x in attach],
           "signature": {"name": sender, "role": getattr(me, "role", "") or "", "org": rep.get("org") or ""},
           "gates": gates, "hold_reason": "; ".join(f"{g['id']}: {g.get('detail', '')}" for g in failed) or None}
    (d / "deliver.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    (d / "mail.md").write_text(mail_md(out, include))
    return out


def mail_md(d, include):
    """The mail as text the owner pastes: headers, then the body the schedule's Include checklist selects."""
    lines = [f"Subject: {d['subject']}", f"To: {', '.join(d['to'])}"]
    if d.get("cc"):
        lines.append(f"Cc: {', '.join(d['cc'])}")
    lines += ["", f"Hi {d['greet']},", ""]
    if include.get("body", True):
        if d["lead"]:
            lines += [d["lead"], ""]
        if d["summary"]:
            lines += [f"- {s}" for s in d["summary"]] + [""]
        if d["coming"]:
            lines += ["Coming up:"] + [f"- {c['title']}" + (f" ({c['due']})" if c["due"] else "") for c in d["coming"]] + [""]
        if not (d["lead"] or d["summary"] or d["coming"]):
            lines += ["Nothing changed this week in the project's tree.", ""]
    if d.get("link"):
        lines += [f"Documents: {d['link']}", ""]
    sig = d.get("signature") or {}
    lines += [sig.get("name") or "", sig.get("role") or "", sig.get("org") or ""]
    names = [Path(a).name for a in d.get("attach") or []]
    lines += ["", "Attachments: " + ("none" if not names else "")] + [f"- {n}" for n in names]
    return "\n".join(l for l in lines if l is not None).rstrip() + "\n"

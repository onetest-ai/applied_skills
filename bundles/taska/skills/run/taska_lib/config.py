"""taska.toml: everything project-specific lives in the consuming project, never in the bundle."""
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_NAME = "taska.toml"
ADAPTERS = {"meetings_folder", "msgraph_transcripts", "msgraph_calendar", "files_folder", "code_host"}


class ConfigError(ValueError):
    pass


@dataclass
class Person:
    name: str
    side: str = "internal"          # internal | client | external
    aliases: list = field(default_factory=list)
    role: str = ""


@dataclass
class Config:
    project_dir: Path
    name: str
    store: Path
    timezone: str
    working_days: tuple
    plan: dict
    people: list
    sources: dict
    media: dict
    run: dict

    def person(self, label):
        """A speaker label, email or login -> Person, tolerant of Teams suffixes ("Ada Example1")."""
        k = _key(label)
        if not k:
            return None
        for p in self.people:
            if k in {_key(p.name)} | {_key(a) for a in p.aliases}:
                return p
        return None

    def canonical(self, label):
        p = self.person(label)
        return p.name if p else (label or None)

    def side(self, label):
        p = self.person(label)
        return p.side if p else None


def _key(s):
    s = (s or "").strip().lower()
    s = s.split("@")[0] if "@" in s and " " not in s else s
    s = re.sub(r"[._]", " ", s)
    s = re.sub(r"\d+$", "", s).strip()           # Teams duplicate-name suffix
    s = re.sub(r"\s*\((external|guest|ext)\)\s*$", "", s)
    return re.sub(r"\s+", " ", s)


def find_project(start="."):
    d = Path(start).resolve()
    for x in [d, *d.parents]:
        if (x / CONFIG_NAME).is_file():
            return x
    raise ConfigError(f"no {CONFIG_NAME} in {d} or its parents; run the taska onboard skill first")


def load(project_dir="."):
    pd = find_project(project_dir)
    try:
        raw = tomllib.loads((pd / CONFIG_NAME).read_text())
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{CONFIG_NAME}: {e}") from e
    pr = raw.get("project") or {}
    if not pr.get("name"):
        raise ConfigError("[project].name is required")
    people = []
    for p in raw.get("people") or []:
        if not p.get("name"):
            raise ConfigError("every [[people]] entry needs a name")
        if p.get("side", "internal") not in ("internal", "client", "external"):
            raise ConfigError(f"{p['name']}: side must be internal, client or external")
        people.append(Person(p["name"], p.get("side", "internal"), list(p.get("aliases") or []), p.get("role", "")))
    sources = {}
    for k, s in (raw.get("sources") or {}).items():
        if s.get("adapter") not in ADAPTERS:
            raise ConfigError(f"[sources.{k}].adapter must be one of {sorted(ADAPTERS)}")
        if s.get("path"):
            s = dict(s, path=str(_resolve(pd, s["path"])))
        sources[k] = s
    plan = dict(raw.get("plan") or {})
    if plan.get("file"):
        plan["file"] = str(_resolve(pd, plan["file"]))
    return Config(project_dir=pd, name=pr["name"], store=_resolve(pd, pr.get("store", ".taska")),
                  timezone=pr.get("timezone", "UTC"), working_days=tuple(pr.get("working_days", (1, 2, 3, 4, 5))),
                  plan=plan, people=people, sources=sources, media=dict(raw.get("media") or {}),
                  run=dict(raw.get("run") or {}))


def _resolve(base, p):
    p = Path(os.path.expanduser(str(p)))
    return p if p.is_absolute() else (base / p)


TEMPLATE = '''# taska.toml: one per project. Paths are relative to this file unless absolute.
[project]
name = "{name}"
store = ".taska"                 # the ledger, evidence and workbench export live here
timezone = "{timezone}"
working_days = [1, 2, 3, 4, 5]   # ISO weekdays; "tomorrow" on a Friday means Monday

[plan]
# Where epics come from. One of:
#   file = "plan.json"     # {{"activities": [{{"name", "start", "end", "kind", "phase"}}]}}
#   file = "epics.csv"     # name,start,end
#   epics = ["Discovery", "Build", "Launch"]
# or nothing: epics are created in the workbench.

# [[people]]
# name = "Ada Example"
# side = "client"                # internal | client | external
# aliases = ["Ada Example1", "ada@example.com", "adaex"]   # Teams labels, emails, GitHub logins

# [sources.daily]
# adapter = "meetings_folder"    # transcripts (.vtt .srt .docx .txt) and recordings (video or audio)
# path = "~/Library/CloudStorage/OneDrive-Company/Project/Call Transcripts"
# include = ["*.vtt", "*.docx", "*.mp4", "*.m4a"]
# client_patterns = ["weekly status"]   # title patterns that mark a client call

# [sources.teams]
# adapter = "msgraph_transcripts" # Teams transcripts through the msgraph skill
# subject = "Project"            # meetings whose subject contains this
# since = "2026-01-05"

# [sources.deliverables]
# adapter = "files_folder"       # a synced SharePoint/OneDrive library: new/changed documents are evidence
# path = "~/Library/CloudStorage/OneDrive-Company/Project/Deliverables"
# include = ["*.docx", "*.pptx", "*.xlsx", "*.pdf", "*.md"]
# exclude = ["Call Transcripts/*"]
# since = "2026-01-05"

# [sources.app]
# adapter = "code_host"          # pull/merge requests and issues through the CLI you are signed in to
# provider = "github"            # github (gh) | gitlab (glab) | azure (az + azure-devops extension)
# repo = "org/app"               # azure: repository name, plus organization = "https://dev.azure.com/org", project = "..."
# include = ["prs", "issues"]    # azure: ["prs", "workitems"]
# since = "2026-01-05"

# [media]
# whisper_model = "~/models/ggml-base.en.bin"   # only for recordings without a transcript
# language = "en"
'''

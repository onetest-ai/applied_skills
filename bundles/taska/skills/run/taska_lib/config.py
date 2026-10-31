"""taska.toml: everything project-specific lives in the consuming project, never in the bundle."""
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_NAME = "taska.toml"
def adapters():
    """The adapter names a taska.toml may use: exactly the registered sources (one list, so they cannot drift)."""
    from .sources import ADAPTERS
    return set(ADAPTERS)


class ConfigError(ValueError):
    pass


@dataclass
class Person:
    name: str
    side: str = "internal"          # internal | client | external
    aliases: list = field(default_factory=list)
    role: str = ""


@dataclass
class Team:
    """A group named in meetings ("the AI guys"): it can own work; your Confirm can hand that work to one member."""
    name: str
    members: list = field(default_factory=list)
    aliases: list = field(default_factory=list)


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
    teams: list = field(default_factory=list)
    schedules: list = field(default_factory=list)
    report: dict = field(default_factory=dict)

    def person(self, label):
        """A speaker label, email or login -> Person, tolerant of Teams suffixes ("Ada Example1")."""
        k = _key(label)
        if not k:
            return None
        for p in self.people:
            if k in {_key(p.name)} | {_key(a) for a in p.aliases}:
                return p
        return None

    def team(self, label):
        """A team name or alias ("AI guys") -> Team, else None."""
        art = lambda x: re.sub(r"^the\s+", "", x)   # "the AI guys" is said as often as "AI guys"
        k = art(_key(label))
        if not k:
            return None
        for t in self.teams:
            if k in {art(_key(t.name))} | {art(_key(a)) for a in t.aliases}:
                return t
        return None

    def canonical(self, label):
        p = self.person(label)
        if p:
            return p.name
        t = self.team(label)
        return t.name if t else (label or None)

    def side(self, label):
        p = self.person(label)
        if p:
            return p.side
        t = self.team(label)
        return (self.person(t.members[0]).side if t.members and self.person(t.members[0]) else "internal") if t else None


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
    teams = []
    for t in raw.get("teams") or []:
        if not t.get("name"):
            raise ConfigError("every [[teams]] entry needs a name")
        unknown = [m for m in t.get("members") or [] if m not in {p.name for p in people}]
        if unknown:
            raise ConfigError(f"team {t['name']}: members must be [[people]] names; unknown: {unknown}")
        teams.append(Team(t["name"], list(t.get("members") or []), list(t.get("aliases") or [])))
    sources = {}
    for k, s in (raw.get("sources") or {}).items():
        if s.get("adapter") not in adapters():
            raise ConfigError(f"[sources.{k}].adapter must be one of {sorted(adapters())}")
        if s.get("path"):
            s = dict(s, path=str(_resolve(pd, s["path"])))
        sources[k] = s
    plan = dict(raw.get("plan") or {})
    if plan.get("file"):
        plan["file"] = str(_resolve(pd, plan["file"]))
    wd = tuple(pr.get("working_days", (1, 2, 3, 4, 5)))
    if not wd or any(x not in range(1, 8) for x in wd):
        raise ConfigError("[project].working_days must be a non-empty list of ISO weekdays (1-7)")
    from . import schedules as SCH
    try:
        scheds = SCH.parse(raw.get("schedules"))
    except ValueError as e:
        raise ConfigError(str(e)) from e
    return Config(project_dir=pd, name=pr["name"], store=_resolve(pd, pr.get("store", ".taska")),
                  timezone=pr.get("timezone", "UTC"), working_days=wd,
                  plan=plan, people=people, sources=sources, media=dict(raw.get("media") or {}),
                  run=dict(raw.get("run") or {}), teams=teams, schedules=scheds, report=dict(raw.get("report") or {}))


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

# [sources.chat]
# adapter = "teams_chat"         # a Teams meeting/group chat: each message is judged by a local decision model
# chat_id = "19:meeting_...@thread.v2"   # from `node msgraph.js teams --chats --json`
# chat_name = "Project daily sync"
# model_card = "~/project/teams/model_card.json"  # thresholds measured on your own labelled messages
# judge_url = "http://127.0.0.1:8009/v1/systemone"  # a local kev.serve; unreachable → the source fails, nothing applies
# cli = "~/.claude/skills/msgraph/scripts/msgraph.js"
# (adapter = "teams_channel" takes team_id + channel_id + channel_name instead of chat_id)
# Teams findings never change the tree by themselves: each waits for Confirm or Reject in the workbench.

# [sources.app]
# adapter = "code_host"         # pull/merge requests and issues through the CLI you are signed in to
# provider = "github"            # github (gh) | gitlab (glab) | azure (az + azure-devops extension)
# repo = "org/app"               # azure: repository name, plus organization = "https://dev.azure.com/org", project = "..."
# include = ["prs", "issues"]    # azure: ["prs", "workitems"]
# since = "2026-01-05"

# [media]
# whisper_model = "~/models/ggml-base.en.bin"   # only for recordings without a transcript
# language = "en"
'''

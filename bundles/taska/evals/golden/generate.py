"""The public golden bank's generator: invented meetings with exactly known answers, written as task-bank tasks.

generate(seed) -> {"vtts": {file name: VTT text}, "slots": [...]}
    Every meeting of one seed (the three planted meetings, through planted/generate.py, and the archetypes of
    templates.json) with one slot per expected answer. A seed changes the cast's roles, dates, entry order within a
    part and filler, never the structure of the expected answers. Rule-category slots are provisional must_not_create
    with pending_ruling true; bank_tasks applies the owner's rulings.

bank_tasks(seeds, project, rulings) -> (tasks, problems)
    Syncs every seed's meetings into the scratch folder `project` with the real taska CLI, locates every slot in the
    synced evidence (spans on the synced text, never the VTT), computes each task's class from the synced text and
    writes the task-bank tasks. A slot that cannot be located exactly once is a problem, never a guessed span.

review_page(tasks, problems) -> str
    A self-contained page: the class matrix and any empty class, the rule questions, every check with its id and answer.

mark_reviewed(tasks, ids) -> (tasks, pending)
    The owner's approval: every listed check that waits for no ruling becomes `reviewed`; an unknown id is refused.

build_project(bank, project) -> project
    A project synced from the seeds a bank's tasks were generated from: what `bank.py run` and `run.py --smart` grade.

regeneration_problems(bank_path, project) -> [problems]
    The bank's recorded write inputs (bank.vN.meta.json: seeds, rulings, reviewed ids, date) rebuilt through bank_tasks
    and compared with the bank: every task's transcript_sha and spans, and the whole body for a bank `write` made.
"""
import argparse
import datetime as dt
import html
import json
import random
import re
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
EVALS = _HERE.parent
RUN_DIR = EVALS.parent / "skills" / "run"
for _p in (RUN_DIR, EVALS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import bank as B  # noqa: E402
import evalview  # noqa: E402
import meetings as M  # noqa: E402
from planted import generate as P  # noqa: E402
from taska_lib import config as C, sensitive, store as S  # noqa: E402
from taska_lib.text import norm  # noqa: E402

TEMPLATES = json.loads((_HERE / "templates.json").read_text(encoding="utf-8"))
CATEGORIES: dict[str, dict[str, str]] = TEMPLATES["categories"]
VALUES = ("work", "not_work")
CLASS_MIN = 1
ONE_TURN_CHARS = 20000
ONE_SPEAKER_SHARE = 0.8
ONE_SPEAKER_MIN_WORDS = 1500
_ROLE = re.compile(r"\{([ic]\d)(\.first)?\}")
_COMMITS = re.compile(r"\b(?:i|we)(?:'ll|\s+will|\s+shall|\s+am going to|\s+are going to)\b|\bwill do\b|\bfollow up\b",
                      re.I)


def cast_names() -> list[str]:
    """Every invented person of the cast, internal first."""
    return list(TEMPLATES["cast"]["internal"]) + list(TEMPLATES["cast"]["client"])


def _short_lines() -> list[tuple[str, str]]:
    """The planted filler pool as (side, text), without a line that reads as a commitment or a people matter."""
    client = set(TEMPLATES["cast"]["client"])
    return [("client" if f["speaker"] in client else "internal", f["utterance"]) for f in P.FILLER
            if not _COMMITS.search(f["utterance"]) and not sensitive.people_matter(f["utterance"], names=cast_names())]


def filler_lines() -> list[str]:
    """Every fixed filler line a meeting can draw: the invented sentences and the neutral planted lines."""
    return list(TEMPLATES["filler_sentences"]) + [t for _, t in _short_lines()]


def frame_sentence(rng: random.Random) -> str:
    """One filler sentence from a frame with its fields drawn: the long meetings' variety."""
    fr = TEMPLATES["filler_frames"]
    s = rng.choice(fr["frames"]).format(**{k: rng.choice(v) for k, v in sorted(fr["fields"].items())})
    return s[0].upper() + s[1:]


def _roles(seed: int) -> dict[str, str]:
    rng = random.Random(f"golden-cast-{seed}")
    internal, client = list(TEMPLATES["cast"]["internal"]), list(TEMPLATES["cast"]["client"])
    rng.shuffle(internal)
    rng.shuffle(client)
    return {f"i{n}": name for n, name in enumerate(internal, 1)} | {f"c{n}": name for n, name in enumerate(client, 1)}


def _fill(text: str, roles: dict[str, str]) -> str:
    return _ROLE.sub(lambda m: roles[m[1]].split()[0] if m[2] else roles[m[1]], text)


def _caps(expected: str, row: dict, why: str | None, base: list[str]) -> list[str]:
    """The bank's capabilities of a must_create row, the why of a must_not_create, plus the meeting's own."""
    caps = (B._caps(row) if expected == "must_create" else [] if expected == "no_duplicate"
            else ["people" if why == "people" else "not_work"])
    return caps + [c for c in base if c not in caps]


def _slot(file: str, archetype: str, family: str, speaker: str, quote: str, spec: dict, owner_of, base: list[str],
          after: str | None) -> dict:
    category = spec.get("category")
    none = {"kind": None, "commitment": None, "owner": None, "client_owed": False, "work": None}
    if category:
        work = dict(spec["work"], owner=owner_of(spec["work"].get("owner")))
        out = none | {"expected": "must_not_create", "why": CATEGORIES[category]["why"], "pending_ruling": True,
                      "work": work}
    elif spec["expect"] == "create":
        out = {"expected": "must_create", "kind": spec["kind"], "commitment": spec["commitment"],
               "owner": owner_of(spec.get("owner")), "client_owed": bool(spec.get("client_owed")), "why": None,
               "pending_ruling": False, "work": None}
    elif spec["expect"] == "duplicate":
        out = none | {"expected": "no_duplicate", "why": "duplicate", "pending_ruling": bool(spec.get("pending"))}
    else:
        out = none | {"expected": "must_not_create", "why": spec["why"], "pending_ruling": False}
    out["caps"] = _caps(out["expected"], out, out["why"], base)
    return {"file": file, "archetype": archetype, "family": family, "speaker": speaker, "utterance": quote,
            "category": category, "after": after, "title": spec.get("title"), "tag": spec.get("tag"),
            "of": spec.get("of"), "expect_status": spec.get("expect_status")} | out


def _sentence(rng: random.Random) -> str:
    if rng.random() < TEMPLATES["filler_frames"]["share"]:
        return frame_sentence(rng)
    return rng.choice(TEMPLATES["filler_sentences"])


def _paragraph(rng: random.Random, span: list[int]) -> str:
    return " ".join(_sentence(rng) for _ in range(rng.randint(span[0], span[1])))


def _filler_cue(rng: random.Random, arch: dict) -> tuple[str, str]:
    layout = arch["layout"]
    if layout == "asr":
        return "?", f"{rng.choice(TEMPLATES['asr_fillers'])} {_sentence(rng).lower().rstrip('.')}"
    if layout == "one_block":
        return arch["presenter"], _paragraph(rng, arch["sentences_per_cue"])
    role = rng.choice(arch["speakers"])
    if rng.random() < arch.get("short_share", 0):
        side = "client" if role.startswith("c") else "internal"
        return role, rng.choice([t for s, t in _short_lines() if s == side])
    return role, _paragraph(rng, arch["sentences_per_cue"])


def _file(arch: dict, seed: int) -> str:
    return f"{M.meeting_date(seed * 10 + arch['offset'])} {arch['title']}.vtt"


def _archetype(arch: dict, seed: int, roles: dict[str, str]) -> tuple[str, str, list[dict]]:
    """One meeting. Each part gets an equal share of the characters, so a part's entries lie in its own share of the
    text (the XL conversation's quiet middle holds only its own two commitments); a part's filler is spread over the
    gaps around its entries."""
    rng = random.Random(f"golden-{arch['name']}-{seed}")
    file = _file(arch, seed)
    after = _file(next(a for a in TEMPLATES["archetypes"] if a["name"] == arch["after"]), seed) if arch.get("after") else None
    one_block, asr = arch["layout"] == "one_block", arch["layout"] == "asr"

    def who(role: str) -> str:
        return "?" if asr else roles[arch["presenter"]] if one_block else roles[role]

    def owner_of(owner: str | None) -> str | None:
        return roles.get(owner, owner) if owner else None

    parts = [list(p) for p in arch["parts"]]
    for p in parts:
        rng.shuffle(p)
    lines = [(who(r), _fill(t, roles)) for r, t in arch["opening"]]
    share = arch["target_chars"] / len(parts)
    slots, written = [], len(lines[0][1])

    def pad(goal: float) -> None:
        nonlocal written
        while True:
            role, text = _filler_cue(rng, arch)
            lines.append((who(role), text))
            written += len(text)
            if written >= goal:
                return

    for k, p in enumerate(parts):
        start, fixed = written, sum(len(_fill(t, roles)) for e in p for _, t in e["cues"])
        step = max(0.0, share * (k + 1) - start - fixed) / (len(p) + 1)
        for i, e in enumerate(p):
            pad(start + step * (i + 1) + sum(len(_fill(t, roles)) for e2 in p[:i] for _, t in e2["cues"]))
            cues = [(r, _fill(t, roles)) for r, t in e["cues"]]
            lines += [(who(r), t) for r, t in cues]
            written += sum(len(t) for _, t in cues)
            spec = e.get("slot")
            if spec:
                q = spec.get("quote")
                quote = " ".join(t for _, t in cues) if q == "@cues" else _fill(q, roles) if q else cues[0][1]
                role = spec.get("speaker") or next((r for r, t in cues if quote in t), cues[0][0])
                slots.append(_slot(file, arch["name"], spec["family"], who(role), quote, spec, owner_of,
                                   list(arch["caps"]), after))
        pad(share * (k + 1))
    t, cues = 5, []
    for speaker, text in lines:
        dur = arch["cue_seconds"] - 1 if asr else max(2, len(text) // 15)
        cues.append((t, t + dur, speaker, text))
        t += arch["cue_seconds"] if asr else dur + 1
    return file, M.build_vtt(cues), slots


def _planted_slots(gold: list[dict]) -> list[dict]:
    out = []
    for g in gold:
        form = g["form"]
        category = TEMPLATES["planted"]["categories"].get(g.get("bullet") or form)
        if category:
            spec = {"category": category, "work": {"kind": "task", "commitment": "committed", "owner": None}}
        elif form in P.DECISION_FORMS:
            spec = {"expect": "create", "kind": g["kind"], "commitment": "committed"}
        elif form == "declined":
            spec = {"expect": "create", "kind": "decision", "commitment": "declined"}
        elif form in P.NOT_WORK_FORMS:
            spec = {"expect": "not", "why": "not_work"}
        else:
            spec = {"expect": "create", "kind": g["kind"], "commitment": g["commitment"], "owner": g["owner"],
                    "client_owed": g["client_owed"]}
        spec["title"] = g["title"]
        out.append(_slot(g["file"], "planted", f"planted:{form}", g["speaker"], g["quote"], spec,
                         lambda o: o, [], None))
    return out


def generate(seed: int) -> dict:
    """{"vtts": {file name: VTT text}, "slots": [...]} for one seed, deterministic."""
    roles = _roles(seed)
    vtts: dict[str, str] = {}
    slots: list[dict] = []
    for fn in (P.generate, P.generate_planning, P.generate_workshop):
        vtt, gold = fn(seed)
        vtts[gold[0]["file"]] = vtt
        slots += _planted_slots(gold)
    for arch in TEMPLATES["archetypes"]:
        file, vtt, arch_slots = _archetype(arch, seed, roles)
        vtts[file] = vtt
        slots += arch_slots
    return {"vtts": vtts, "slots": slots}


def meeting_class(item: dict, owners: set[str]) -> dict[str, str]:
    """size by characters, structure, owners among must_create checks and audience, from the synced item."""
    n = len(S.full_text(item))
    size = "S" if n < 10000 else "M" if n < 30000 else "L" if n < 60000 else "XL"
    turns = item.get("turns") or []
    words: dict[str, int] = {}
    total = 0
    for t in turns:
        w = len(t["text"].split())
        total += w
        if t["speaker"] != "?":   # speech recognition has no speakers: "?" is nobody, never one speaker
            words[t["speaker"]] = words.get(t["speaker"], 0) + w
    block = any(len(t["text"]) >= ONE_TURN_CHARS for t in turns) or (
        total >= ONE_SPEAKER_MIN_WORDS and bool(words) and max(words.values()) >= ONE_SPEAKER_SHARE * total)
    k = len(owners)
    o = B.CLASSES["owners"]
    client = set(TEMPLATES["cast"]["client"])
    return {"size": size, "structure": "one block" if block else "conversation",
            "owners": o[0] if k <= 1 else o[1] if k <= 3 else o[2],
            "audience": "client" if any(t["speaker"] in client for t in turns) else "internal"}


def check_rulings(rulings: object, where: str = "rulings") -> dict[str, str]:
    """The one rulings check: an object {category: work|not_work}; an absent category stays pending."""
    if not isinstance(rulings, dict):
        raise ValueError(f"{where}: expected a JSON object {{category: work|not_work}}")
    bad = {c: v for c, v in rulings.items() if c not in CATEGORIES or v not in VALUES}
    if bad:
        raise ValueError(f"{where}: unknown category or value {bad}; categories {sorted(CATEGORIES)}, values work or "
                         "not_work (leave a category out to keep it pending)")
    if rulings.get("PEOPLE_MATTER") == "work":
        raise ValueError("PEOPLE_MATTER cannot be ruled work: the ledger rejects every agent create that is a people "
                         "matter, so no run could pass such a check")
    return rulings


def _ruled(slot: dict, rulings: dict[str, str], base: list[str]) -> dict:
    cat = slot["category"]
    if not cat:
        return slot
    ruling = rulings.get(cat, "pending")
    if ruling == "work":
        w = slot["work"]
        out = slot | {"expected": "must_create", "kind": w["kind"], "commitment": w["commitment"], "owner": w["owner"],
                      "why": None, "pending_ruling": False}
        return out | {"caps": _caps("must_create", out, None, base)}
    return slot | {"pending_ruling": ruling != "not_work"}


_LETTER = {"must_create": "c", "must_not_create": "n", "no_duplicate": "d"}


def bank_tasks(seeds: list[int], project: Path, rulings: dict[str, str]) -> tuple[list[dict], list[str]]:
    """Sync the seeds' meetings into `project` (a fresh scratch folder) and write one task per meeting."""
    check_rulings(rulings)
    vtts: dict[str, str] = {}
    slots: list[dict] = []
    for seed in seeds:
        g = generate(seed)
        if set(vtts) & set(g["vtts"]):
            raise ValueError(f"seed {seed}: file names repeat another seed's: {sorted(set(vtts) & set(g['vtts']))}")
        vtts |= g["vtts"]
        slots += [s | {"seed": seed} for s in g["slots"]]
    people = [{"name": n, "side": "internal"} for n in TEMPLATES["cast"]["internal"]]
    people += [{"name": n, "side": "client"} for n in TEMPLATES["cast"]["client"]]
    project = Path(project)
    project.mkdir(parents=True, exist_ok=True)
    evidence = M.sync_project(vtts, people, project, [sys.executable, str(RUN_DIR / "taska.py")], TEMPLATES["teams"])
    cfg = C.load(project)
    ev = S.evidence(cfg.store)
    names = sensitive.names(cfg)
    arch_of = {a["name"]: a for a in TEMPLATES["archetypes"]}
    problems: list[str] = []
    tasks: list[dict] = []
    task_of: dict[str, str] = {}
    tagged: dict[tuple[str, str], str] = {}
    for file in vtts:
        mine = [s for s in slots if s["file"] == file]
        item = ev.get(evidence.get(file) or "")
        if item is None:
            problems.append(f"{file}: no evidence after sync; its {len(mine)} slots are not written")
            continue
        name = mine[0]["archetype"] if mine else ""
        base = list(arch_of.get(name, {"caps": []})["caps"])
        task = B._task_shell(item["id"], item) | {"no_duplicate": []}
        task_of[file] = task["task"]
        text = norm(S.full_text(item))
        for slot in mine:
            s = _ruled(slot, rulings, base)
            span = B._locate(item, s["utterance"])
            if span is None:
                problems.append(f"{file}: {s['family']} quote not found in {item['id']}; no span written: "
                                f"{s['utterance']!r}")
                continue
            if text.find(norm(s["utterance"]), span[0] + 1) >= 0:
                problems.append(f"{file}: {s['family']} quote occurs more than once in {item['id']}; no span written: "
                                f"{s['utterance']!r}")
                continue
            pm = sensitive.people_matter(s["utterance"], names=names)
            if pm != (s["why"] == "people" or s["category"] == "PEOPLE_MATTER"):
                problems.append(f"{file}: {s['family']} quote {'reads' if pm else 'does not read'} as a people matter: "
                                f"{s['utterance']!r}")
            check = {"id": "", "span": span, "quote": s["utterance"]}
            if s["expected"] == "must_create":
                check |= {"kind": s["kind"], "commitment": s["commitment"], "owner": s["owner"]}
            else:
                check |= {"why": s["why"]}
            check |= {"caps": s["caps"], "source": f"golden seed {s['seed']}: {s['archetype']}/{s['family']}",
                      "status": "draft", "difficulty": None, "pending_ruling": s["pending_ruling"],
                      "category": s["category"], "family": s["family"]}
            for key in ("title", "expect_status"):
                if s.get(key):
                    check[key] = s[key]
            if s["category"] and s["work"] and s["work"].get("effect"):
                check["effect"] = s["work"]["effect"]
            if s.get("of"):
                of = tagged.get((s["after"], s["of"]))
                if of is None:
                    problems.append(f"{file}: {s['family']} repeats {s['of']!r} of {s['after']}, which has no check")
                    continue
                check["of"] = of["id"]
            task[s["expected"]].append(check)
            if s.get("tag"):
                tagged[(file, s["tag"])] = check
        for typ, letter in _LETTER.items():
            for i, c in enumerate(task[typ], 1):
                c["id"] = f"{task['task']}-{letter}{i:02d}"
        if not task["no_duplicate"]:
            del task["no_duplicate"]
        klass = meeting_class(item, {c["owner"] for c in task["must_create"] if c["owner"]})
        for dim, want in arch_of.get(name, {}).get("class", {}).items():
            if klass[dim] != want:
                problems.append(f"{file}: {name} should be {dim} {want!r}, the synced text is {klass[dim]!r}")
        task |= {"class": klass, "archetype": name, "seed": mine[0]["seed"] if mine else None, "file": file}
        prev = next((s["after"] for s in mine if s.get("after")), None)
        if prev:
            if prev in task_of:
                task["after"] = [task_of[prev]]
            else:
                problems.append(f"{file}: continues {prev}, which has no task")
        tasks.append(task)
    return tasks, problems


def class_matrix(tasks: list[dict]) -> dict[str, dict[str, int]]:
    """{dimension: {class value: tasks}} over every value of bank.CLASSES."""
    out = {dim: {v: 0 for v in vals} for dim, vals in B.CLASSES.items()}
    for t in tasks:
        for dim, v in (t.get("class") or {}).items():
            out[dim][v] += 1
    return out


def empty_classes(tasks: list[dict]) -> dict[str, list[str]]:
    """{dimension: [values with fewer than CLASS_MIN tasks]}, only dimensions with a gap."""
    return {dim: gaps for dim, vals in class_matrix(tasks).items() if (gaps := [v for v, n in vals.items() if n < CLASS_MIN])}


_TYPES = (("must create", "must_create"), ("must not create", "must_not_create"), ("no duplicate", "no_duplicate"))


def review_page(tasks: list[dict], problems: list[str]) -> str:
    """The class matrix (an empty class named), the rule questions, every check with its expected answer."""
    e = lambda x: html.escape(str(x if x is not None else ""))
    every = [c for t in tasks for _, typ in _TYPES for c in t.get(typ, [])]
    out = ["<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
           "<title>Golden bank review</title><style>" + evalview.STYLE +
           "table{border-collapse:collapse;width:100%}td,th{border:1px solid var(--line);padding:4px 6px;"
           "text-align:left;vertical-align:top}.gap{background:var(--bad-bg);color:var(--bad)}"
           ".wrap{overflow-x:auto}</style><main><h1>Golden bank review</h1>"
           f"<p class=m>{len(tasks)} tasks · " +
           " · ".join(f"{sum(len(t.get(typ, [])) for t in tasks)} {label}" for label, typ in _TYPES) +
           f" · {sum(bool(c['pending_ruling']) for c in every)} pending ruling</p>"]
    matrix, gaps = class_matrix(tasks), empty_classes(tasks)
    out.append(f"<h2>Classes (at least {CLASS_MIN} task each)</h2><div class=wrap><table>")
    for dim, vals in matrix.items():
        cells = "".join(f"<td{' class=gap' if n < CLASS_MIN else ''}>{e(v)}: {n}</td>" for v, n in vals.items())
        out.append(f"<tr><th>{e(dim)}</th>{cells}</tr>")
    out.append("</table></div>")
    out.append("<h3>Empty classes</h3>" + ("<ul>" + "".join(f"<li class=gap>{e(d)} = {e(v)}</li>" for d, vs in
                                                            gaps.items() for v in vs) + "</ul>" if gaps else "<p>None.</p>"))
    out.append("<h2>Rule questions</h2><ul>")
    for cat, c in CATEGORIES.items():
        checks = [x for x in every if x.get("category") == cat]
        pend = sum(bool(x["pending_ruling"]) for x in checks)
        out.append(f"<li><code>{e(cat)}</code> {e(c['question'])} <span class=m>{len(checks)} checks, "
                   f"{pend} pending ruling</span></li>")
    out.append("</ul>")
    out.append(f"<h2>Problems ({len(problems)})</h2>")
    out.append("<ul>" + "".join(f"<li>{e(p)}</li>" for p in problems) + "</ul>" if problems else "<p>None.</p>")
    for t in tasks:
        k = t.get("class") or {}
        out.append(f"<h2>Task {e(t['task'])} <small>{e(t.get('file'))}</small></h2><p class=m>{e(t.get('archetype'))}"
                   f" · seed {e(t.get('seed'))} · " + " · ".join(f"{e(d)} {e(v)}" for d, v in k.items()) +
                   (f" · after {e(', '.join(t['after']))}" if t.get("after") else "") + "</p>")
        out.append("<div class=wrap><table><tr><th>id</th><th>expected</th><th>answer</th><th>capabilities</th>"
                   "<th>category</th><th>span</th><th>quote</th></tr>")
        for label, typ in _TYPES:
            for c in t.get(typ, []):
                if typ == "must_create":
                    answer = f"{c.get('kind')} · {c.get('commitment')} · {c.get('owner') or 'no owner'}"
                elif typ == "no_duplicate":
                    answer = f"no new node for {c.get('of')}" + (f"; status {c['expect_status']}" if c.get("expect_status") else "")
                else:
                    answer = f"why {c.get('why')}"
                pend = " · pending ruling" if c.get("pending_ruling") else ""
                out.append(f"<tr><td><code>{e(c['id'])}</code></td><td>{e(label)}</td><td>{e(answer)}</td>"
                           f"<td>{e(', '.join(c.get('caps', [])))}</td><td>{e(c.get('category') or '')}{e(pend)}</td>"
                           f"<td><code>{c['span'][0]}-{c['span'][1]}</code></td><td>{e(c['quote'])}</td></tr>")
        out.append("</table></div>")
    out.append("</main>")
    return "\n".join(out)


def _read_json(path: str, what: str) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise ValueError(f"{what} {path}: {err}") from err


def read_rulings(path: str | None) -> dict[str, str]:
    """The owner's rulings from a JSON file {category: work|not_work}; no file leaves every category pending."""
    return check_rulings(_read_json(path, "rulings file"), f"rulings file {path}") if path else {}


def read_reviewed(path: str | None) -> list[str]:
    """The check ids the owner approved on the review page, a JSON list of strings; no file approves none."""
    if not path:
        return []
    ids = _read_json(path, "reviewed file")
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise ValueError(f"reviewed file {path}: expected a JSON list of check ids")
    return ids


def mark_reviewed(tasks: list[dict], ids: list[str]) -> tuple[list[dict], list[str]]:
    """Set `reviewed` on every listed check that waits for no ruling (in place); returns the tasks and the listed checks
    left as they were because they wait for a ruling. An id that is no check of these tasks is refused."""
    checks = {c["id"]: c for t in tasks for _, typ in _TYPES for c in t.get(typ, [])}
    unknown = sorted(set(ids) - set(checks))
    if unknown:
        raise ValueError(f"reviewed ids that are no check of this bank: {', '.join(unknown)}")
    pending = sorted({i for i in ids if checks[i].get("pending_ruling")})
    for i in ids:
        if i not in pending:
            checks[i]["status"] = "reviewed"
    return tasks, pending


def meta_path(bank_path: Path) -> Path:
    """bank.vN.meta.json beside bank.vN.jsonl: the inputs that wrote it."""
    p = Path(bank_path)
    return p.with_name(p.name.removesuffix(".jsonl") + ".meta.json")


def write_meta(bank_path: Path, meta: dict) -> Path:
    """The bank's write inputs beside it; like the bank, never overwritten."""
    out = meta_path(bank_path)
    if out.exists():
        raise FileExistsError(f"{out} exists: a bank version's inputs are never overwritten")
    out.write_text(json.dumps(meta, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


def _spans(task: dict) -> dict[str, list[int]]:
    return {c["id"]: c["span"] for _, typ in _TYPES for c in task.get(typ, [])}


def regeneration_problems(bank_path: Path, project: Path) -> list[str]:
    """Rebuild a bank from its recorded inputs into `project` (a fresh folder) and name every difference: a task
    missing on either side, a transcript_sha or a span that moved, and, for a bank `write` made (not a promotion),
    any other byte of a task."""
    bank_path = Path(bank_path)
    if not meta_path(bank_path).is_file():
        return [f"{bank_path.name}: no {meta_path(bank_path).name}, so it cannot be regenerated"]
    meta = json.loads(meta_path(bank_path).read_text(encoding="utf-8"))
    _, bank = B.load_bank(bank_path)
    tasks, problems = bank_tasks(list(meta["seeds"]), Path(project), check_rulings(meta["rulings"], "meta rulings"))
    try:
        tasks, _ = mark_reviewed(tasks, list(meta.get("reviewed") or []))
    except ValueError as err:
        problems.append(str(err))
    mine, theirs = {t["task"]: t for t in tasks}, {t["task"]: t for t in bank}
    problems += [f"{tid}: in the bank, not regenerated" for tid in sorted(set(theirs) - set(mine))]
    problems += [f"{tid}: regenerated, not in the bank" for tid in sorted(set(mine) - set(theirs))]
    for tid in sorted(set(mine) & set(theirs)):
        a, b = mine[tid], theirs[tid]
        if a["transcript_sha"] != b["transcript_sha"]:
            problems.append(f"{tid}: transcript_sha differs")
        if _spans(a) != _spans(b):
            problems.append(f"{tid}: spans differ")
        if "promoted_from" not in meta and json.dumps(a, ensure_ascii=False) != json.dumps(b, ensure_ascii=False):
            problems.append(f"{tid}: the task differs from the written bank")
    return problems


def build_project(bank: list[dict], project: Path) -> Path:
    """Sync the meetings of the seeds the bank's tasks were generated from into `project` (a fresh folder)."""
    if not bank or any(t.get("seed") is None for t in bank):
        raise ValueError("the bank's tasks name no seed to rebuild their meetings from: pass a project that holds them")
    bank_tasks(sorted({t["seed"] for t in bank}), Path(project), {})
    return Path(project)


def counts(tasks: list[dict]) -> dict:
    """What the review prints: bank's own description of the tasks."""
    return B.describe(tasks)


def _build(a: argparse.Namespace, rulings: dict[str, str]) -> tuple[list[dict], list[str]]:
    """The tasks of the seeds, synced into --project or into a temporary folder removed afterwards."""
    if a.project:
        return bank_tasks(a.seeds, Path(a.project), rulings)
    with tempfile.TemporaryDirectory(prefix="golden-") as tmp:
        return bank_tasks(a.seeds, Path(tmp) / "project", rulings)


def _project_cmd(a: argparse.Namespace) -> int:
    out = Path(a.out)
    if out.exists() and any(out.iterdir()):
        print(f"error: {out} is not empty: the project is synced into a fresh folder", file=sys.stderr)
        return 2
    try:
        _, bank = B.load_bank(a.bank)
        build_project(bank, out)
        B.check_transcripts(out, bank)
    except ValueError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    print(json.dumps({"project": str(out), "tasks": len(bank)}, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Generate the golden meetings, sync them and review or write the bank")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, help_ in (("review", "write the review page and print the counts; writes no bank"),
                        ("write", "write bank.<version>.jsonl (bank.write_bank; a version is never overwritten)")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--seeds", nargs="+", type=int, required=True, metavar="SEED")
        p.add_argument("--project", help="a fresh scratch folder to sync the meetings into (default: a temp folder)")
        p.add_argument("--rulings", help="JSON file {category: work|not_work}; absent categories stay pending")
    sub.choices["review"].add_argument("--out", required=True, help="folder for golden-review.html")
    w = sub.choices["write"]
    w.add_argument("--version", required=True)
    w.add_argument("--out", help=f"folder for the bank file (default {_HERE})")
    w.add_argument("--date", default=dt.date.today().isoformat())
    w.add_argument("--note", default="golden generator")
    w.add_argument("--reviewed", help="JSON list of the check ids the owner approved on the review page")
    pj = sub.add_parser("project", help="sync the bank's meetings into a fresh project for bank.py run / run.py --smart")
    pj.add_argument("--bank", required=True, type=Path)
    pj.add_argument("--out", required=True, help="a fresh folder for the project")
    a = ap.parse_args(argv)
    if a.cmd == "project":
        return _project_cmd(a)
    pending: list[str] = []
    try:
        rulings = read_rulings(a.rulings)
        reviewed = read_reviewed(a.reviewed) if a.cmd == "write" else []
        if a.cmd == "write":
            target = Path(a.out) if a.out else _HERE
            for p in (target / f"bank.{a.version}.jsonl", meta_path(target / f"bank.{a.version}.jsonl")):
                if p.exists():
                    raise ValueError(f"{p} exists: a bank version is never overwritten")
        tasks, problems = _build(a, rulings)
        if reviewed:
            tasks, pending = mark_reviewed(tasks, reviewed)
    except ValueError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    if a.cmd == "review":
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "golden-review.html").write_text(review_page(tasks, problems), encoding="utf-8")
    extra = {"reviewed_but_pending": pending} if a.cmd == "write" else {}
    print(json.dumps({**counts(tasks), "problems": problems} | extra, indent=1, ensure_ascii=False))
    if problems:
        return 1
    if a.cmd == "write":
        note = f"{a.note}: seeds {a.seeds}"
        try:
            written = B.write_bank(target, tasks, a.version, a.date, note)
            write_meta(written, {"version": a.version, "seeds": a.seeds, "rulings": rulings, "reviewed": reviewed,
                                 "date": a.date, "note": note})
            print(written)
        except (ValueError, FileExistsError) as err:
            print(f"error: {err}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

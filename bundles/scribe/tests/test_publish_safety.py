"""Task 9 (A10; PoC m1, m2, m3, m5): journaled atomic publish, a locked-docx
failure mode, an offline raw root that is never read as deletion, daily
cadence from `last_checked`, `<word>` survival in render, and non-ok evidence
recorded as null.

Field-name adaptation from the task-9 brief: the brief's
`test_daily_cadence_uses_last_checked` reads each `plan["tasks"]` entry by
`t["task"]`, but `scribe.compute_plan`'s entries (unchanged from `cmd_plan`,
which every other plan test already pins — see `test_config_plan.py`) key
each task by `"id"`, not `"task"`. Adapted to `t["id"]` here rather than
renaming the real field and breaking every other plan test.
"""
import json
import shutil
import pytest
from scribe_fixtures import (
    fixture_brain_db, setup_mini_project, load, doc, publish_seed, fake_evidence, write_json, write_text,
)
from scribe_lib import brain as brain_mod, publish as publish_mod
from scribe_lib.checkfile import check_file_task
from scribe_lib.config import sha256_file, read_state, raw_snapshot, resolve_instance_inputs
from scribe_lib.fingerprint import fingerprint_task
from scribe_lib.merge import merge_task
from scribe_lib.pack import prepare_task
from scribe_lib.render import render_task

DETAILS = "D. [RAG:2] <!-- c:bbbb0001 -->"


def _ready(tmp_path, monkeypatch, overview="New text. [RAG:1] <!-- c:aaaa0001 -->"):
    monkeypatch.setattr(brain_mod, "evidence", fake_evidence)
    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    config, data = load(proj)
    inst, tpl = data["instances"]["m1"], data["templates"]["mini-profile"]
    out = config.out_root / "m1"; out.mkdir(parents=True)
    (out / "Mini m1.docx").write_bytes(b"v1-docx"); (out / "Mini m1.pdf").write_bytes(b"v1-pdf")
    publish_seed(config, inst, doc("m1", 1, "Mini m1", {"overview": "Old. [RAG:1] <!-- c:aaaa0001 -->", "details": DETAILS}),
                 1, docx_sha=sha256_file(out / "Mini m1.docx"))
    render = config.work_dir / "m1" / "render"; render.mkdir(parents=True)
    (render / "Mini m1.docx").write_bytes(b"v2-docx"); (render / "Mini m1.pdf").write_bytes(b"v2-pdf")
    (render / "render.json").write_text(json.dumps({"ok": True, "docx": str(render / "Mini m1.docx"),
                                                      "pdf": str(render / "Mini m1.pdf"), "diagrams": [], "errors": []}))
    (config.work_dir / "m1" / "next.md").write_text(doc("m1", 2, "Mini m1", {"overview": overview, "details": DETAILS}))
    return config, data, inst, tpl, out


def test_crash_mid_publish_resumes_without_losing_the_previous_version(tmp_path, monkeypatch):
    config, data, inst, tpl, out = _ready(tmp_path, monkeypatch)
    real = publish_mod._write_state
    monkeypatch.setattr(publish_mod, "_write_state", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("crash")))
    with pytest.raises(RuntimeError):
        publish_mod.publish_task(config, "m1", inst, tpl, data["instances"], data["edges"], no_render=False)
    monkeypatch.setattr(publish_mod, "_write_state", real)
    r = publish_mod.publish_task(config, "m1", inst, tpl, data["instances"], data["edges"], no_render=False)
    assert r["status"] == "ok"
    archived = next((out / "_versions").glob("v001_*.docx"))
    assert archived.read_bytes() == b"v1-docx" and (out / "Mini m1.docx").read_bytes() == b"v2-docx"
    assert read_state(config, inst)["version"] == 2 and not (out / "_src" / ".pending").exists()


def test_publish_locked_docx_fails_cleanly_and_retry_completes(tmp_path, monkeypatch):
    config, data, inst, tpl, out = _ready(tmp_path, monkeypatch)
    real_replace = publish_mod._move
    def locked(src, dst):
        if str(src).endswith("Mini m1.docx") and "_versions" in str(dst):
            raise PermissionError(f"locked: {src}")
        return real_replace(src, dst)
    monkeypatch.setattr(publish_mod, "_move", locked)
    r = publish_mod.publish_task(config, "m1", inst, tpl, data["instances"], data["edges"], no_render=False)
    assert r["status"] == "failed" and r["reason"].startswith("locked:")
    assert (out / "Mini m1.docx").read_bytes() == b"v1-docx" and read_state(config, inst)["version"] == 1
    monkeypatch.setattr(publish_mod, "_move", real_replace)
    assert publish_mod.publish_task(config, "m1", inst, tpl, data["instances"], data["edges"], no_render=False)["status"] == "ok"


def test_non_ok_evidence_is_recorded_as_null(tmp_path, monkeypatch):
    config, data, inst, tpl, out = _ready(tmp_path, monkeypatch, overview="New. [RAG:9] <!-- c:aaaa0001 -->")
    monkeypatch.setattr(brain_mod, "evidence", lambda cfg, cid: {"status": "not_modeled", "chunk_id": str(cid)})
    publish_mod.publish_task(config, "m1", inst, tpl, data["instances"], data["edges"], no_render=False)
    assert read_state(config, inst)["sections"]["overview"]["cited_chunks"] == {"9": None}


@pytest.mark.skipif(not (shutil.which("pandoc") and shutil.which("soffice")), reason="pandoc/soffice missing")
def test_angle_bracket_words_survive_render(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setattr(brain_mod, "evidence", fake_evidence)
    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    config, data = load(proj)
    (config.work_dir / "m1").mkdir(parents=True)
    (config.work_dir / "m1" / "next.md").write_text(doc("m1", 1, "Mini m1", {
        "overview": "Agents greet <Customer Name> first. [RAG:1] <!-- c:aaaa0001 -->", "details": DETAILS}))
    r = render_task(config, "m1", data["instances"]["m1"], data["templates"]["mini-profile"])
    # Adapted from the brief: `render_task`'s result carries the docx's
    # `.name` (relative), not a full path — join it onto the render dir
    # rather than passing a bare filename to a subprocess run from an
    # unrelated cwd.
    docx_path = config.work_dir / "m1" / "render" / r["docx"]
    text = subprocess.run(["pandoc", str(docx_path), "-t", "plain"], capture_output=True, text=True).stdout
    assert "<Customer Name>" in text


def test_unavailable_raw_root_is_not_deletion(tmp_path, monkeypatch):
    monkeypatch.setattr(brain_mod, "search", lambda cfg, q, limit, tag: [])
    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    config, data = load(proj)
    inst = data["instances"]["m1"]
    publish_seed(config, inst, doc("m1", 1, "Mini m1", {"overview": "A. [FILE:a.md#p1] <!-- c:aaaa0001 -->", "details": DETAILS}),
                 1, docx_sha=None, cited_raw={"a.md": "sha"}, extra_state={"raw_snapshot": {"a.md": "sha"}})
    shutil.rmtree(config.raw_root)   # the synced folder is offline
    fp = fingerprint_task(config, "m1", data["instances"], data["templates"], data["edges"])
    assert "raw_file_removed" not in fp["sections"]["overview"]["stale_reasons"]
    assert "raw_root_unavailable" in fp["notes"]
    assert read_state(config, inst)["raw_snapshot"] == {"a.md": "sha"}


def test_daily_cadence_uses_last_checked(tmp_path, monkeypatch):
    from scribe_fixtures import mini_task, make_project, write_text, MINI_TEMPLATE
    import scribe
    monkeypatch.setenv("SCRIBE_NOW", "2026-01-05T09:00:00")
    proj = make_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    write_text(proj / "templates" / "mini-profile.tmpl.md", MINI_TEMPLATE)
    mini_task(proj, "m1", cadence="daily")
    config, data = load(proj)
    publish_seed(config, data["instances"]["m1"], doc("m1", 1, "Mini m1", {"overview": "A. [RAG:1] <!-- c:aaaa0001 -->", "details": DETAILS}),
                 1, docx_sha=None, extra_state={"built_at": "2026-01-01T00:00:00", "last_checked": "2026-01-05T06:00:00"})
    plan = scribe.compute_plan(config, data)
    assert next(t for t in plan["tasks"] if t["id"] == "m1")["due"] is False


# ----------------------------------------------------------- fix round 1 --

def test_offline_raw_root_freezes_the_raw_lane_end_to_end(tmp_path, monkeypatch):
    """Review fix round 1, Important #1: an offline raw root must FREEZE the
    raw lane for the whole run, never empty it. End to end: prepare -> draft
    a [FILE:] claim -> check-file -> merge -> publish (raw available), then
    raw_root removed -> prepare -> the claim survives check-file, fingerprint
    reports no `fingerprint_changed` from raw (plus the top-level note), and
    the published state's `raw_snapshot` is the ACTUAL prior value, not a
    tautological re-read."""
    from scribe_lib.basedoc import split_by_section_id

    monkeypatch.setattr(brain_mod, "evidence", fake_evidence)
    monkeypatch.setattr(brain_mod, "search", lambda cfg, q, limit, tag: [])
    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    # Matches the mini-profile template's "{{name}} overview" query (name =
    # "Widget") so round 1 actually gets a raw hit that contributes to
    # `considered.raw` — otherwise the freeze fix would be untested by
    # coincidence (an empty `considered.raw` on both sides proves nothing).
    write_text(proj / "raw-replay" / "a.md", "Widget overview: alpha note recorded for the record.\n")
    config, data = load(proj)
    inst, tpl = data["instances"]["m1"], data["templates"]["mini-profile"]

    # -- round 1: raw available --
    prepare_task(config, "m1")
    write_text(
        config.work_dir / "m1" / "sections" / "overview.md",
        "Widget overview note. [FILE:a.md#p1]\n",
    )
    write_json(
        config.work_dir / "m1" / "sections" / "overview.evidence.json",
        [{"claim_ref": 0, "tag": "[FILE:a.md#p1]", "quote": "alpha note recorded"}],
    )
    write_text(config.work_dir / "m1" / "sections" / "details.md", DETAILS + "\n")
    cf1 = check_file_task(config, "m1", inst)
    assert cf1["failed"] == [], cf1
    merged = merge_task(config, "m1", data["instances"], data["templates"])
    assert merged["noop"] is False
    published = publish_mod.publish_task(config, "m1", inst, tpl, data["instances"], data["edges"], no_render=True)
    assert published["status"] == "ok", published

    # The real published text, id merge assigned included — never a
    # hand-picked id, so round 2's "carried" match is exercised honestly.
    published_overview = split_by_section_id(
        (config.out_root / "m1" / "_src" / "v001.md").read_text(encoding="utf-8")
    )["overview"]
    assert "[FILE:a.md#p1]" in published_overview

    raw_inputs = resolve_instance_inputs(inst, tpl).get("raw") or {}
    snapshot_before = raw_snapshot(config, raw_inputs)
    assert snapshot_before, "sanity: round 1 must have actually snapshotted a.md"
    assert read_state(config, inst)["raw_snapshot"] == snapshot_before

    # -- round 2: raw root offline --
    shutil.rmtree(config.raw_root)
    prepare_task(config, "m1")

    manifest = json.loads((config.work_dir / "m1" / "raw" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest and manifest[0]["status"] == "ok"  # frozen from round 1, not wiped

    fp = json.loads((config.work_dir / "m1" / "fingerprint.json").read_text(encoding="utf-8"))
    assert "fingerprint_changed" not in fp["sections"]["overview"]["stale_reasons"]
    assert "raw_root_unavailable" in fp["notes"]

    # A carried [FILE:] claim (as `base_task` would carry it forward, byte
    # for byte) must survive check-file, not be rewritten to `Not modeled:`.
    write_text(config.work_dir / "m1" / "sections" / "overview.md", published_overview)
    cf2 = check_file_task(config, "m1", inst)
    assert cf2["failed"] == [], cf2
    assert cf2["raw_offline_notes"], cf2
    section_text = (config.work_dir / "m1" / "sections" / "overview.md").read_text(encoding="utf-8")
    assert "[FILE:a.md#p1]" in section_text

    # The published state's raw_snapshot is untouched — the ACTUAL value,
    # not merely "still a dict" (Important #1's third failure mode).
    assert read_state(config, inst)["raw_snapshot"] == snapshot_before


def test_journal_crash_after_write_state_before_mark_then_publishes_next_version(tmp_path, monkeypatch):
    """Review fix round 1, Important #2: a crash AFTER `_write_state`
    succeeds (state.json already says vN) but BEFORE the journal records
    `write_state` must never corrupt a later version's publish. The retry
    must discard the (already-applied) journal rather than complete it with
    whatever `next.md` currently holds, and a subsequent real vN+1 publish
    must succeed normally, from vN+1's own content."""
    config, data, inst, tpl, out = _ready(tmp_path, monkeypatch)
    real_mark_step = publish_mod._mark_step

    def crash_on_write_state_mark(pending_dir, journal, step):
        if step == "write_state":
            raise RuntimeError("crash after write_state, before journal mark")
        return real_mark_step(pending_dir, journal, step)

    monkeypatch.setattr(publish_mod, "_mark_step", crash_on_write_state_mark)
    with pytest.raises(RuntimeError):
        publish_mod.publish_task(config, "m1", inst, tpl, data["instances"], data["edges"], no_render=False)
    monkeypatch.setattr(publish_mod, "_mark_step", real_mark_step)

    # state.json already advanced to v2 (the real `_write_state` ran); the
    # journal is still on disk, missing only the `write_state` mark.
    assert read_state(config, inst)["version"] == 2
    assert (out / "_src" / ".pending" / "journal.json").is_file()
    v2_src_before = (out / "_src" / "v002.md").read_text(encoding="utf-8")

    # A stray retry against the STILL-v2 next.md must refuse (stale next.md)
    # and discard the now-redundant journal, never re-run write_state against
    # some other content.
    stale_retry = publish_mod.publish_task(config, "m1", inst, tpl, data["instances"], data["edges"], no_render=False)
    assert stale_retry["status"] == "error"
    assert not (out / "_src" / ".pending").exists()
    assert read_state(config, inst)["version"] == 2
    assert (out / "_src" / "v002.md").read_text(encoding="utf-8") == v2_src_before

    # A genuinely NEW draft (v3) must publish cleanly from ITS OWN content,
    # not from whatever the crashed v2 journal last staged.
    (out / "Mini m1.docx").write_bytes(b"v2-docx-stable")
    (out / "Mini m1.pdf").write_bytes(b"v2-pdf-stable")
    render = config.work_dir / "m1" / "render"
    (render / "Mini m1.docx").write_bytes(b"v3-docx")
    (render / "Mini m1.pdf").write_bytes(b"v3-pdf")
    (config.work_dir / "m1" / "next.md").write_text(
        doc("m1", 3, "Mini m1", {"overview": "Third draft. [RAG:3] <!-- c:cccc0001 -->", "details": DETAILS})
    )
    r3 = publish_mod.publish_task(config, "m1", inst, tpl, data["instances"], data["edges"], no_render=False)
    assert r3["status"] == "ok" and r3["version"] == 3
    assert read_state(config, inst)["version"] == 3
    assert (out / "Mini m1.docx").read_bytes() == b"v3-docx"
    assert "Third draft" in (out / "_src" / "v003.md").read_text(encoding="utf-8")
    assert "New text" not in (out / "_src" / "v003.md").read_text(encoding="utf-8")


@pytest.mark.skipif(not (shutil.which("pandoc") and shutil.which("soffice")), reason="pandoc/soffice missing")
def test_angle_brackets_do_not_double_escape_across_a_render_base_render_roundtrip(tmp_path, monkeypatch):
    """Review fix round 1, Important #3: `<word>` escaping must not pile up
    backslashes across repeated render -> base (docx recovery) -> render
    cycles. Render twice from the SAME source text and assert the visible
    plain text is identical both times (no growing backslashes)."""
    from scribe_lib.basedoc import base_task
    from scribe_lib.config import sha256_file as _sha

    monkeypatch.setattr(brain_mod, "evidence", fake_evidence)
    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    config, data = load(proj)
    inst, tpl = data["instances"]["m1"], data["templates"]["mini-profile"]
    work_dir = config.work_dir / "m1"
    out_dir = config.out_root / "m1"

    text_v1 = doc("m1", 1, "Mini m1", {
        "overview": "Agents greet <Customer Name> first. [RAG:1] <!-- c:aaaa0001 -->", "details": DETAILS,
    })
    (work_dir).mkdir(parents=True)
    (work_dir / "next.md").write_text(text_v1)
    r1 = render_task(config, "m1", inst, data["instances"], md_path=str(work_dir / "next.md"))
    assert r1["ok"], r1

    import subprocess
    docx1 = work_dir / "render" / r1["docx"]
    plain1 = subprocess.run(["pandoc", str(docx1), "-t", "plain"], capture_output=True, text=True).stdout
    assert "<Customer Name>" in plain1
    assert "\\<" not in plain1 and "\\\\" not in plain1

    # Publish v1 (no_render, so we control the docx placement ourselves) then
    # drop the SAME rendered docx in as the "stable" file, so `base_task`
    # recovers it via the real pandoc docx->gfm round trip (the edited path).
    out_dir.mkdir(parents=True, exist_ok=True)
    publish_seed(config, inst, text_v1, 1, docx_sha="mismatch-so-base-treats-it-as-edited")
    (out_dir / "Mini m1.docx").write_bytes(docx1.read_bytes())

    base_result = base_task(config, "m1", inst, tpl)
    assert base_result.get("status") != "failed", base_result
    base_text = (work_dir / "base.md").read_text(encoding="utf-8")
    assert "<Customer Name>" in base_text
    # The recovered base must hold the UNESCAPED word — no leftover
    # backslash from the docx round trip — or round 2's render would
    # re-escape an already-escaped `<`/`>` and grow backslashes forever.
    assert "\\<Customer Name\\>" not in base_text
    assert "\\\\" not in base_text

    # Render again from the recovered base (simulating v2 carrying the same
    # claim forward unchanged) and confirm the visible text is IDENTICAL,
    # not `\<Customer Name\>` or worse.
    text_v2 = doc("m1", 2, "Mini m1", {
        "overview": base_text.split("{#overview}", 1)[1].split("## Details", 1)[0].strip(),
        "details": DETAILS,
    })
    (work_dir / "next.md").write_text(text_v2)
    r2 = render_task(config, "m1", inst, data["instances"], md_path=str(work_dir / "next.md"))
    assert r2["ok"], r2
    docx2 = work_dir / "render" / r2["docx"]
    plain2 = subprocess.run(["pandoc", str(docx2), "-t", "plain"], capture_output=True, text=True).stdout
    assert plain1.strip() == plain2.strip(), (plain1, plain2)
    assert "\\<" not in plain2 and "\\\\" not in plain2


def test_plan_reports_raw_root_unavailable_note(tmp_path, monkeypatch):
    """Review fix round 1, Important #4: an offline raw root must be visible
    in `plan` output — per task and at the top level — not a quiet night."""
    import scribe

    monkeypatch.setattr(brain_mod, "search", lambda cfg, q, limit, tag: [])
    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    config, data = load(proj)
    inst = data["instances"]["m1"]
    publish_seed(
        config, inst,
        doc("m1", 1, "Mini m1", {"overview": "A. [FILE:a.md#p1] <!-- c:aaaa0001 -->", "details": DETAILS}),
        1, docx_sha=None, cited_raw={"a.md": "sha"}, extra_state={"raw_snapshot": {"a.md": "sha"}},
    )
    shutil.rmtree(config.raw_root)

    plan = scribe.compute_plan(config, data)
    assert "raw_root_unavailable" in plan.get("notes", [])
    m1_entry = next(t for t in plan["tasks"] if t["id"] == "m1")
    assert "raw_root_unavailable" in m1_entry.get("notes", [])
    assert "raw_changed" not in m1_entry["reasons"]


# ----------------------------------------------------------- fix round 2 --

def test_offline_publish_carries_the_prior_cited_raw_sha_instead_of_writing_null(tmp_path, monkeypatch):
    """Review fix round 2, Important #1 (finding 1 was still open after round
    1): round 1 froze `gather-raw` and `fingerprint`'s raw-status checks, but
    `publish`'s own `cited_from_section` still wrote `cited_raw[path] = None`
    for every `[FILE:]` tag whenever the raw root was offline — the mass drop
    just moved one publish later: the NEXT online fingerprint run would read
    that recorded `None` as `raw_file_changed`/gone (fingerprint compares the
    RECORDED sha, not today's raw availability), and check-file's
    `_carried_raw_reason` would fail every carried claim against a `None`
    prior sha.

    True end-to-end, per the coordinator's spec: v1 publish (raw available,
    with a [FILE:] claim) -> raw root removed -> prepare -> draft carrying
    the claim UNCHANGED plus a REAL change in another section (so this is a
    genuine v2 publish, not a noop) -> check-file -> merge -> publish v2
    OFFLINE (no_render, consistent with the rest of this suite) -> assert
    state.cited_raw for that path is still the v1 sha (not None) and
    raw_snapshot is unchanged -> restore the raw root (the identical file) ->
    prepare + check-file again -> the claim is not stale for raw reasons and
    is not rewritten."""
    from scribe_lib.basedoc import split_by_section_id

    monkeypatch.setattr(brain_mod, "evidence", fake_evidence)
    # Round 1: no brain hits at all. Round 2: a NEW hit for the "details"
    # query only — a real, independent reason for v2 to be non-noop, so the
    # overview section (carrying the [FILE:] claim unchanged) is the only
    # thing exercising the offline-raw path.
    def search_round1(cfg, q, limit, tag):
        return []

    def search_round2(cfg, q, limit, tag):
        if "details" in q.lower():
            return [{"chunk_id": "2", "source": "x.md", "section": "S", "score": 1.0, "text": "Detail chunk text."}]
        return []

    monkeypatch.setattr(brain_mod, "search", search_round1)
    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    a_md = proj / "raw-replay" / "a.md"
    a_md_content = "Widget overview: alpha note recorded for the record.\n"
    write_text(a_md, a_md_content)
    config, data = load(proj)
    inst, tpl = data["instances"]["m1"], data["templates"]["mini-profile"]

    # -- v1: raw available --
    prepare_task(config, "m1")
    write_text(
        config.work_dir / "m1" / "sections" / "overview.md",
        "Widget overview note. [FILE:a.md#p1]\n",
    )
    write_json(
        config.work_dir / "m1" / "sections" / "overview.evidence.json",
        [{"claim_ref": 0, "tag": "[FILE:a.md#p1]", "quote": "alpha note recorded"}],
    )
    write_text(config.work_dir / "m1" / "sections" / "details.md", DETAILS + "\n")
    cf1 = check_file_task(config, "m1", inst)
    assert cf1["failed"] == [], cf1
    merged1 = merge_task(config, "m1", data["instances"], data["templates"])
    assert merged1["noop"] is False
    published1 = publish_mod.publish_task(config, "m1", inst, tpl, data["instances"], data["edges"], no_render=True)
    assert published1["status"] == "ok", published1

    v1_sha = read_state(config, inst)["sections"]["overview"]["cited_raw"]["a.md"]
    assert v1_sha, "sanity: v1 must have actually recorded a sha for a.md"
    raw_inputs = resolve_instance_inputs(inst, tpl).get("raw") or {}
    snapshot_before = raw_snapshot(config, raw_inputs)
    assert snapshot_before == read_state(config, inst)["raw_snapshot"]

    published_overview = split_by_section_id(
        (config.out_root / "m1" / "_src" / "v001.md").read_text(encoding="utf-8")
    )["overview"]
    assert "[FILE:a.md#p1]" in published_overview

    # -- raw root removed; a genuine v2 (details changes, overview carries) --
    shutil.rmtree(config.raw_root)
    monkeypatch.setattr(brain_mod, "search", search_round2)
    prepare_task(config, "m1")

    plan_json = json.loads((config.work_dir / "m1" / "pack" / "plan.json").read_text(encoding="utf-8"))
    stale_sids = {e["section"] for e in plan_json["stale"]}
    assert stale_sids == {"details"}, plan_json  # overview carried, not stale

    write_text(config.work_dir / "m1" / "sections" / "details.md", "Fresh detail about the widget. [RAG:2]\n")
    # `overview` is carried (not stale), but `merge_task` reads a carried
    # section straight from `base.md`, not `sections/overview.md` — write it
    # anyway so `check_file_task` (which scans every `sections/*.md`, stale
    # or not) exercises the carried-[FILE:]-claim-while-offline path too.
    write_text(config.work_dir / "m1" / "sections" / "overview.md", published_overview)

    cf2 = check_file_task(config, "m1", inst)
    assert cf2["failed"] == [], cf2
    assert cf2["raw_offline_notes"], cf2

    merged2 = merge_task(config, "m1", data["instances"], data["templates"])
    assert merged2["noop"] is False
    next_sections2 = split_by_section_id((config.work_dir / "m1" / "next.md").read_text(encoding="utf-8"))
    assert "[FILE:a.md#p1]" in next_sections2["overview"]  # carried claim survived merge too

    published2 = publish_mod.publish_task(config, "m1", inst, tpl, data["instances"], data["edges"], no_render=True)
    assert published2["status"] == "ok" and published2["version"] == 2, published2

    state2 = read_state(config, inst)
    # THE fix: recorded as the carried-forward v1 sha, never None.
    assert state2["sections"]["overview"]["cited_raw"]["a.md"] == v1_sha
    assert state2["sections"]["overview"]["cited_raw"]["a.md"] is not None
    assert state2["raw_snapshot"] == snapshot_before  # still frozen, unchanged

    # -- restore the raw root (the identical file) --
    write_text(a_md, a_md_content)
    prepare_task(config, "m1")

    fp3 = json.loads((config.work_dir / "m1" / "fingerprint.json").read_text(encoding="utf-8"))
    overview_fp3 = fp3["sections"]["overview"]
    assert "raw_file_changed" not in overview_fp3["stale_reasons"]
    assert "raw_file_removed" not in overview_fp3["stale_reasons"]
    assert overview_fp3["cited_raw_status"].get("a.md") == "same"

    write_text(config.work_dir / "m1" / "sections" / "overview.md", published_overview)
    cf3 = check_file_task(config, "m1", inst)
    assert cf3["failed"] == [], cf3
    section_text3 = (config.work_dir / "m1" / "sections" / "overview.md").read_text(encoding="utf-8")
    assert "[FILE:a.md#p1]" in section_text3  # still not rewritten


def test_corrupt_journal_version_is_discarded_not_resumed(tmp_path, monkeypatch):
    """Review fix round 2, Minor: a journal whose `version` isn't an int
    (corrupt/malformed) must be discarded like an already-applied one, never
    handed to `_resume_journal` (which formats/compares it as an int)."""
    config, data, inst, tpl, out = _ready(tmp_path, monkeypatch)
    pending_dir = out / "_src" / ".pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    (pending_dir / "journal.json").write_text(
        json.dumps({"version": "not-a-number", "steps_done": ["stage"]}), encoding="utf-8"
    )

    r = publish_mod.publish_task(config, "m1", inst, tpl, data["instances"], data["edges"], no_render=False)
    assert r["status"] == "ok" and r["version"] == 2
    assert not pending_dir.exists()
    assert read_state(config, inst)["version"] == 2

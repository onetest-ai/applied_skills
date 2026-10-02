import baseline_reconcile as br
import source_key as sk

CFG = {
    "corpus": {"narrative_exts": [".pdf", ".pptx", ".docx", ".vtt", ".xlsm"]},
    "scope": {"exclude_patterns": [r"prior .*engagement", r"team_demo"], "keep_patterns": [r"monthly"],
              "junk_patterns": [], "provenance_cutoff": ""},
}

SP = [
    {"filename": "Monthly Ops January 2026.xlsm", "ext": ".xlsm", "match_key": "monthlyopsjanuary2026xlsm",
     "folder": "Ops Reporting", "modified": "2026-02-07"},
    {"filename": "Vision.pdf", "ext": ".pdf", "match_key": "visionpdf", "folder": "Inputs", "modified": "2026-08-18"},
]


def _brain(*sources):
    return {"sources": {s: {"folder": sk.brain_source_folder(s), "filename": sk.brain_source_to_filename(s)}
                        for s in sources}}


BRAIN = _brain("Prior engagement 2021 - 2023__Team_Demo_03.14.2022.pptx.md",
               "Ops Reporting__Monthly Ops January 2026.xlsm.md")


def test_gate_fails_when_out_of_scope_present():
    rep = br.reconcile(SP, BRAIN, CFG)
    assert rep["gate"] == "FAIL"
    assert any("Team_Demo" in s["source"] for s in rep["fail_sources"])


def test_gate_fails_on_a_prior_engagement_folder_nested_below_the_root():
    rep = br.reconcile(SP, _brain("Client Docs__Prior engagement 2021 - 2023__Archive__Old plan.pdf.md"), CFG)
    assert rep["gate"] == "FAIL"


def test_missing_lists_in_scope_narrative_uncovered():
    rep = br.reconcile(SP, BRAIN, CFG)
    assert any(m["filename"] == "Vision.pdf" for m in rep["missing"])
    assert any(c["filename"] == "Monthly Ops January 2026.xlsm" for c in rep["covered"])


def test_gate_passes_when_clean():
    rep = br.reconcile(SP, _brain("Ops Reporting__Monthly Ops January 2026.xlsm.md"), CFG)
    assert rep["gate"] == "PASS"

import coverage_report as cov

CFG = {
    "corpus": {"narrative_exts": [".pdf", ".pptx", ".docx", ".vtt"]},
    "scope": {"exclude_patterns": [r"prior .*engagement"], "keep_patterns": [], "junk_patterns": [],
              "provenance_cutoff": ""},
}

CSV = (
    "eval_id,category,scope,question,query_suffix,expected_answer_must_contain,"
    "expected_answer_must_not_contain,ground_truth_source,notes,min_items\n"
    "E001,MetricOrKPI,single-session,q,,fact,none,Vision.pdf,n,1\n"
    "N001,no-hallucination,cross-session,q,,not established,none,none,n,0\n"
    "E009,X,single-session,q,,fact,none,Nonexistent File.pptx,n,1\n"
)

BRAIN = {"sources": {
    "Docs__Vision.pdf.md": {"folder": "Docs", "filename": "Vision.pdf"},
    "Docs__Roadmap.pdf.md": {"folder": "Docs", "filename": "Roadmap.pdf"},
}}

# Inventory: authoritative narrative corpus (Vision present+tested, Roadmap present+untested,
# Charter absent-from-brain, a prior-engagement deck that must be excluded, a tabular file ignored).
SP = [
    {"filename": "Vision.pdf", "ext": ".pdf", "match_key": cov.normalize("Vision.pdf"), "folder": "Docs", "modified": ""},
    {"filename": "Roadmap.pdf", "ext": ".pdf", "match_key": cov.normalize("Roadmap.pdf"), "folder": "Docs", "modified": ""},
    {"filename": "Charter.docx", "ext": ".docx", "match_key": cov.normalize("Charter.docx"), "folder": "Docs", "modified": ""},
    {"filename": "OldDeck.pptx", "ext": ".pptx", "match_key": cov.normalize("OldDeck.pptx"),
     "folder": "Prior ACME engagement", "modified": ""},
    {"filename": "Numbers.xlsx", "ext": ".xlsx", "match_key": cov.normalize("Numbers.xlsx"), "folder": "Docs", "modified": ""},
]


def _evals(tmp_path):
    p = tmp_path / "e.csv"
    p.write_text(CSV)
    return str(p)


def test_narrative_corpus_is_authoritative_denominator(tmp_path):
    rows = cov.read_eval_sources(_evals(tmp_path))
    rep = cov.map_coverage(rows, BRAIN["sources"], CFG, sp_rows=SP)
    cc = rep["narrative_corpus_coverage"]
    # corpus = Vision, Roadmap, Charter (OldDeck excluded by policy; Numbers.xlsx not narrative)
    assert cc["corpus_total"] == 3
    assert cc["tested"] == 1               # Vision has an eval
    assert cc["present_untested"] == 1     # Roadmap in brain, no eval
    assert cc["not_retrievable"] == 1      # Charter in corpus, not in brain sweep
    assert "Roadmap.pdf" in cc["untested_present_sources"]
    assert "Charter.docx" in cc["not_retrievable_sources"]


def test_orphan_and_no_hallucination(tmp_path):
    rows = cov.read_eval_sources(_evals(tmp_path))
    rep = cov.map_coverage(rows, BRAIN["sources"], CFG, sp_rows=SP)
    assert any(o["eval_id"] == "E009" for o in rep["orphan_eval_sources"])
    assert not any(o["eval_id"] == "N001" for o in rep["orphan_eval_sources"])
    assert rep["eval_totals"]["no_hallucination"] == 1


def test_metric_coverage(tmp_path):
    rows = cov.read_eval_sources(_evals(tmp_path))
    rep = cov.map_coverage(rows, BRAIN["sources"], CFG, metrics=[{"name": "handle_seconds"}, {"name": "survey_score"}])
    assert rep["metric_coverage"]["pct"] == 0.0


def _row(eid, sources, question=""):
    return {"eval_id": eid, "category": "", "scope": "", "sources": [cov.normalize(x) for x in sources],
            "raw_source": ",".join(sources), "question": question, "notes": "", "no_halluc": False}


def test_same_filename_in_two_folders_is_two_sources():
    brain = {"A__notes.pdf.md": {"folder": "A", "filename": "notes.pdf"},
             "B__notes.pdf.md": {"folder": "B", "filename": "notes.pdf"}}
    rep = cov.map_coverage([], brain, CFG)
    assert rep["retrievable_source_floor"]["discovered_sources"] == 2


def test_an_eval_covers_its_file_and_its_family_not_unrelated_files_containing_its_name():
    brain = {"x__Business plan.pdf.md": {"folder": "x", "filename": "Business plan.pdf"},
             "x__Old plan.pdf.md": {"folder": "x", "filename": "Old plan.pdf"},
             "x__plan.pdf.md": {"folder": "x", "filename": "plan.pdf"},
             "x__Survey Summary_MAY_2026.pdf.md": {"folder": "x", "filename": "Survey Summary_MAY_2026.pdf"}}
    rep = cov.map_coverage([_row("E1", ["plan.pdf"]), _row("E2", ["Survey Summary"])], brain, CFG)
    fl = rep["retrievable_source_floor"]
    assert fl["tested"] == 2      # plan.pdf exactly, and the Survey Summary family by stem
    assert "x__Business plan.pdf.md" in fl["untested_sources"]
    assert "x__Old plan.pdf.md" in fl["untested_sources"]


def test_metric_names_match_whole_words_only():
    rep = cov.map_coverage([_row("E1", [], "What changed in the translation backlog?")], {}, CFG,
                           metrics=[{"name": "sla"}, {"name": "translation_backlog"}])
    assert rep["metric_coverage"]["tested"] == ["translation_backlog"]


def test_warns_when_inventory_has_rows_but_none_match_narrative_exts(tmp_path, capsys):
    rows = cov.read_eval_sources(_evals(tmp_path))
    sp = [dict(r, ext="") for r in SP]  # e.g. an inventory export with no usable extension
    rep = cov.map_coverage(rows, BRAIN["sources"], CFG, sp_rows=sp)
    cc = rep["narrative_corpus_coverage"]
    assert cc["corpus_total"] == 0
    assert "narrative_exts" in cc["warning"]
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "narrative_exts" in err


def test_no_warning_when_inventory_rows_match_narrative_exts(tmp_path, capsys):
    rows = cov.read_eval_sources(_evals(tmp_path))
    rep = cov.map_coverage(rows, BRAIN["sources"], CFG, sp_rows=SP)
    assert "warning" not in rep["narrative_corpus_coverage"]
    assert capsys.readouterr().err == ""

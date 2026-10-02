import brain_inventory as bi
from brain_mcp_client import BrainClientError


def test_sweep_dedupes_and_saturates():
    calls = {"n": 0}
    universe = ["A__f1.pptx.md", "B__f2.pdf.md"]

    def fake_call(name, args):
        calls["n"] += 1
        return {"hits": [{"source": s} for s in universe]}  # same set every time

    res = bi.harvest_sources(["q1", "q2", "q3", "q4"], call=fake_call, patience=2)
    assert set(res["sources"]) == set(universe)
    assert res["saturated"] is True
    # stops early: after 2 no-new queries following the first
    assert calls["n"] <= 3


def test_first_query_failure_raises_not_empty():
    def boom(name, args):
        raise BrainClientError("401")

    try:
        bi.harvest_sources(["q1"], call=boom)
        assert False, "should raise"
    except BrainClientError:
        pass


def test_sweep_always_states_latest_only_false_so_hidden_documents_are_seen():
    sent = []

    def fake_call(name, args):
        sent.append(args)
        return {"hits": [{"source": "Old__plan.pdf.md", "status": "SUPERSEDED"}]}

    res = bi.harvest_sources(["q1"], call=fake_call, patience=1)
    assert all(a.get("latest_only") is False for a in sent)
    assert res["sources"]["Old__plan.pdf.md"]["status"] == "SUPERSEDED"


def _server(catalog_pages, hits, has_catalog=True):
    """Fake MCP: list_sources pages through `catalog_pages`; search returns `hits`."""
    log = []

    def call(name, args):
        log.append((name, dict(args)))
        if name == "list_sources":
            if not has_catalog:
                raise BrainClientError("Unknown tool: list_sources")
            i = args.get("offset", 0) // 2
            page = catalog_pages[i]
            nxt = (i + 1) * 2 if i + 1 < len(catalog_pages) else None
            return {"sources": [{"source": s} for s in page], "next_offset": nxt}
        if name == "search_knowledge":
            return {"hits": hits}
        return {}
    return call, log


def test_inventory_uses_the_exact_catalog_and_pages_through_it():
    call, log = _server([["a__x.pdf.md", "b__y.pdf.md"], ["c__z.pdf.md"]], hits=[])
    inv = bi.build_inventory(["seed"], call=call, patience=1)
    assert set(inv["sources"]) == {"a__x.pdf.md", "b__y.pdf.md", "c__z.pdf.md"}
    assert inv["catalog"] == "list_sources"
    assert [a["offset"] for n, a in log if n == "list_sources"] == [0, 2]
    assert inv["sources"]["c__z.pdf.md"]["folder"] == "c"


def test_inventory_adds_superseded_documents_the_catalog_hides():
    call, _ = _server([["a__x.pdf.md"]], hits=[{"source": "Prior engagement__old.pdf.md", "status": "SUPERSEDED"}])
    inv = bi.build_inventory(["seed"], call=call, patience=1)
    assert inv["sources"]["Prior engagement__old.pdf.md"]["status"] == "SUPERSEDED"
    assert inv["superseded_found"] == 1


def test_inventory_falls_back_to_the_sweep_on_a_server_without_list_sources():
    call, _ = _server([], hits=[{"source": "a__x.pdf.md", "status": "ACTIVE"}], has_catalog=False)
    inv = bi.build_inventory(["seed"], call=call, patience=1)
    assert inv["catalog"] == "sweep"
    assert "a__x.pdf.md" in inv["sources"]


def test_no_domain_vocabulary_is_hardcoded():
    assert not hasattr(bi, "GENERIC_SEED")


def test_seed_terms_come_from_config_then_taxonomy_and_metrics():
    def call(name, args):
        if name == "get_taxonomy":
            return {"nodes": [{"label": "Billing", "kind": "intent_l1"}, {"label": "x", "kind": "entity"}]}
        if name == "list_metrics":
            return {"metrics": [{"name": "handle_time"}]}
        return {}
    terms = bi.default_terms(call=call, seeds=["onboarding"])
    assert terms == ["onboarding", "Billing", "handle_time"]


def test_with_no_seed_terms_the_catalog_document_names_drive_the_superseded_sweep():
    # A brain without taxonomy or metrics and no configured seeds must still be swept,
    # or a date-hidden out-of-scope document would pass the gate unseen.
    queries = []

    def call(name, args):
        if name == "list_sources":
            return {"sources": [{"source": "Docs__Release_Plan-2026.pdf.md"}], "next_offset": None}
        if name == "search_knowledge":
            queries.append(args["query"])
            return {"hits": [{"source": "Old__release plan v1.pdf.md", "status": "SUPERSEDED"}]}
        return {}

    inv = bi.build_inventory([], call=call, patience=2)
    assert queries and queries[0] == "Release Plan 2026"
    assert inv["superseded_found"] == 1
    assert inv["queries_run"] >= 1


def test_an_inventory_whose_sweep_could_not_run_says_so():
    def call(name, args):
        if name == "list_sources":
            return {"sources": [], "next_offset": None}
        return {}

    inv = bi.build_inventory([], call=call, patience=2)
    assert inv["superseded_sweep"] == "not_run"


def test_catalog_and_sweep_merge_without_duplicates_and_keep_the_catalog_entry():
    call, _ = _server([["a__x.pdf.md", "b__y.pdf.md"]], hits=[
        {"source": "a__x.pdf.md", "status": "ACTIVE"},
        {"source": "Prior engagement__old.pdf.md", "status": "SUPERSEDED"},
        {"source": "Prior engagement__old.pdf.md", "status": "SUPERSEDED"}])
    inv = bi.build_inventory(["seed"], call=call, patience=1)
    assert sorted(inv["sources"]) == ["Prior engagement__old.pdf.md", "a__x.pdf.md", "b__y.pdf.md"]
    assert inv["sources"]["a__x.pdf.md"]["via"] == "list_sources"
    assert inv["sources"]["Prior engagement__old.pdf.md"]["via"] == "sweep"
    assert inv["sources"]["Prior engagement__old.pdf.md"]["status"] == "SUPERSEDED"
    assert inv["superseded_found"] == 1


def test_a_server_without_superseded_results_adds_nothing_to_the_catalog():
    call, _ = _server([["a__x.pdf.md", "b__y.pdf.md"]], hits=[{"source": "a__x.pdf.md", "status": "ACTIVE"}])
    inv = bi.build_inventory(["seed"], call=call, patience=1)
    assert set(inv["sources"]) == {"a__x.pdf.md", "b__y.pdf.md"}
    assert inv["superseded_found"] == 0
    assert all(m["status"] == "ACTIVE" for m in inv["sources"].values())

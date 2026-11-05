"""Tests for pool.py — candidate pooling for meeting extraction evals.
Invented data only (Harbor Logistics world).
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

TASKA_ROOT = Path(__file__).resolve().parents[1]
EVALS_DIR = TASKA_ROOT / "evals"
RUN_DIR = TASKA_ROOT / "skills" / "run"
if str(EVALS_DIR) not in sys.path:
    sys.path.insert(0, str(EVALS_DIR))
if str(RUN_DIR) not in sys.path:
    sys.path.insert(0, str(RUN_DIR))

from taska_lib import store as S, config as C


# ── shared transcript with three verbatim turns ────────────────────────────────
# Each quote is verbatim in its own turn. Turns are separated by > 400 chars
# of other text so the old "offset within 400" rule would NOT apply here.

TURN1 = "nora will prepare the onboarding checklist and share it with the client team before friday"
TURN2 = "ravi needs to book the warehouse site visit and confirm the date with opal by wednesday"
TURN3 = "opal mentioned the data migration must be completed before the system goes live next month"

# Quotes that appear verbatim in the corresponding turn
QUOTE_A = TURN1
QUOTE_B = TURN2
QUOTE_C = TURN3

# A near-identical variant with one extra word ("this") — NOT verbatim in any turn
QUOTE_A_NEAR = "nora will prepare the onboarding checklist and share it with the client team before this friday"

# A short variant of QUOTE_A that IS verbatim in TURN1 (12 words; subset of QUOTE_A)
QUOTE_A_SHORT = "nora will prepare the onboarding checklist and share it with the client"

EV_ID = "2026-03-02_meetings_ab12cd34"
EV_AT = "2026-03-02T10:00:00"
EV_TITLE = "Harbor Logistics Daily Sync"

import textwrap


def _setup_store(tmp_path, turns=None):
    """Create a minimal taska project + store with one evidence item.

    When *turns* is None a single turn containing TURN1, TURN2 and TURN3
    (concatenated) is used so that all three quotes are verbatim in one turn.
    Pass a list of turn dicts to use multiple separate turns.
    """
    import textwrap as _tw
    (tmp_path / "taska.toml").write_text(_tw.dedent("""
        [project]
        name = "Harbor Logistics"
        timezone = "UTC"
        [[people]]
        name = "Nora Quill"
        [[people]]
        name = "Ravi Tamsin"
        [[people]]
        name = "Opal Brennick"
        side = "client"
    """))
    cfg = C.load(tmp_path)
    S.ensure(cfg.store)

    if turns is None:
        # Single big turn so all three quotes are verbatim
        transcript = " ".join([TURN1, TURN2, TURN3])
        turns = [{"speaker": "Nora Quill", "ts": "00:00:01", "text": transcript}]

    S.write_evidence(cfg.store, {
        "id": EV_ID, "kind": "meeting", "at": EV_AT, "title": EV_TITLE,
        "source": "meetings", "text": "", "turns": turns,
    })
    return cfg


def _add_taska_event(cfg, node_id, quote, ts="00:00:01", speaker="Nora Quill"):
    """Add an agent create event to the store."""
    S.append(cfg.store, [{
        "type": "create",
        "mid": f"m_{node_id}",
        "id": node_id,
        "kind": "task",
        "by": "agent",
        "evidence": EV_ID,
        "date": EV_ID[:10],
        "ts": ts,
        "speaker": speaker,
        "quote": quote,
        "quote_norm": quote,
        "fields": {
            "title": f"Task {node_id}",
            "parent": None,
            "type": "action",
            "internal_owner": None,
            "client_owner": None,
            "client_owed": False,
            "ac": [],
            "due": None,
            "due_text": None,
            "plan": None,
        },
    }])


def _add_propose_event(cfg, node_id, quote, ts="00:05:00", speaker="Ravi Tamsin"):
    """Add an agent propose event to the store."""
    S.append(cfg.store, [{
        "type": "agent",
        "op": "propose",
        "mid": f"p_{node_id}",
        "id": node_id,
        "kind": "task",
        "by": "agent",
        "evidence": EV_ID,
        "date": EV_ID[:10],
        "ts": ts,
        "speaker": speaker,
        "quote": quote,
        "fields": {
            "title": f"Proposed {node_id}",
            "parent": None,
        },
    }])


def _reader_source(ev_id, items):
    """Build a reader-format source entry."""
    return [{"evidence": ev_id, "items": items}]


def _reader_item(quote, ts="00:00:01", speaker="Nora Quill"):
    return {
        "quote": quote,
        "ts": ts,
        "speaker": speaker,
        "title": "Some task",
        "kind": "task",
        "commitment": "committed",
        "owner": speaker,
        "due": None,
        "status": "open",
        "client_owed": False,
        "covered_by": None,
        "note": "Inventory work needs to be completed before the system goes live next week.",
    }


# ── fix-1 tests ───────────────────────────────────────────────────────────────

def test_adjacent_distinct_items_stay_separate(tmp_path):
    """Two items in distinct turns (~200 chars apart in norm space) stay separate.

    With the old offset-within-400 rule they would have merged.
    With the new rule (span overlap OR shingle ≥ 0.5) they must not.
    """
    from pool import build_pool

    # Two separate turns, each with its own distinct quote.
    turns = [
        {"speaker": "Nora Quill", "ts": "00:02:10", "text": TURN1},
        {"speaker": "Ravi Tamsin", "ts": "00:05:22", "text": TURN2},
    ]
    cfg = _setup_store(tmp_path, turns=turns)

    sources = {
        "reader_a": _reader_source(EV_ID, [
            _reader_item(QUOTE_A, ts="00:02:10", speaker="Nora Quill"),
            _reader_item(QUOTE_B, ts="00:05:22", speaker="Ravi Tamsin"),
        ]),
    }

    result = build_pool(cfg.store, sources, [EV_ID])
    candidates = result[EV_ID]
    # Different turns, different text — must stay as two separate candidates
    assert len(candidates) == 2, (
        f"expected 2 separate candidates, got {len(candidates)}: "
        + str([c["quote"][:40] for c in candidates])
    )


def test_no_transitive_merging(tmp_path):
    """A≈B and B≈C chains do not create one cluster when A≉C.

    Greedy clustering: each new candidate is compared only to the cluster
    representative (first member), not to other cluster members.
    """
    from pool import build_pool

    cfg = _setup_store(tmp_path)

    # A and B are very similar (high shingle containment ≥ 0.5)
    # B and C are very similar (high shingle containment ≥ 0.5)
    # A and C are not similar (shingle containment < 0.5)
    A = "nora will prepare the onboarding checklist and share it with the team by friday noon"
    B = "nora will prepare the onboarding checklist and share it with the team before friday noon"
    C = "ravi needs to book the warehouse site visit and confirm the date with opal by wednesday"

    # Verify A and C are not similar enough to merge
    from taska_lib.evalscore import quote_shingles, containment
    assert containment(quote_shingles(A), quote_shingles(C)) < 0.5, "A and C must be dissimilar"
    assert containment(quote_shingles(A), quote_shingles(B)) >= 0.5, "A and B must be similar"

    # Use three different sources to allow inter-source merging
    sources = {
        "reader_a": _reader_source(EV_ID, [_reader_item(A)]),
        "reader_b": _reader_source(EV_ID, [_reader_item(B)]),
        "reader_c": _reader_source(EV_ID, [_reader_item(C)]),
    }

    result = build_pool(cfg.store, sources, [EV_ID])
    candidates = result[EV_ID]

    # Greedy: A → cluster1(rep=A); B≈A → cluster1; C compared to A(rep) → C≉A → cluster2
    # So {A,B} and {C} — even though B≈C, C is never compared to B
    assert len(candidates) == 2, (
        f"expected 2 clusters (no transitive merge), got {len(candidates)}: "
        + str([c["quote"][:40] for c in candidates])
    )


def test_same_source_items_never_merge(tmp_path):
    """Two items from the same reader source never merge, even with high shingle overlap."""
    from pool import build_pool

    cfg = _setup_store(tmp_path)

    # QUOTE_A and QUOTE_A_SHORT are very similar and both verbatim, but from reader_a
    # QUOTE_A_SHORT is the first 12 words of TURN1 (a strict prefix)
    from taska_lib.evalscore import quote_shingles, containment
    assert containment(quote_shingles(QUOTE_A), quote_shingles(QUOTE_A_SHORT)) >= 0.5

    sources = {
        "reader_a": _reader_source(EV_ID, [
            _reader_item(QUOTE_A),
            _reader_item(QUOTE_A_SHORT),
        ]),
    }

    result = build_pool(cfg.store, sources, [EV_ID])
    verifiable = [c for c in result[EV_ID] if c.get("verifiable", True)]
    # Both are from reader_a → must not merge → two verifiable candidates
    assert len(verifiable) == 2, (
        f"expected 2 candidates (same-source never merge), got {len(verifiable)}"
    )


# ── fix-2 tests ───────────────────────────────────────────────────────────────

def test_echo_span_spanning_two_turns_is_unverifiable(tmp_path):
    """An Echo span that covers text from two turns → goes to pool_unverifiable, not batches."""
    from pool import build_pool, write_outputs

    # Two turns with distinct content
    turns = [
        {"speaker": "Nora Quill", "ts": "00:02:10",
         "text": "nora will prepare the onboarding checklist and share it with the team"},
        {"speaker": "Ravi Tamsin", "ts": "00:05:22",
         "text": "ravi needs to book the warehouse site visit and confirm with opal"},
    ]
    cfg = _setup_store(tmp_path, turns=turns)

    # Echo span that crosses the turn boundary — verbatim in full_text but NOT in any single turn
    # end of turn1 + beginning of turn2
    CROSS_SPAN = "share it with the team ravi needs to book the warehouse site"
    # Verify it is verbatim in full_text but not in either single turn
    from taska_lib.text import norm
    turn1_norm = norm(turns[0]["text"])
    turn2_norm = norm(turns[1]["text"])
    assert norm(CROSS_SPAN) not in turn1_norm, "CROSS_SPAN must not be in turn1 alone"
    assert norm(CROSS_SPAN) not in turn2_norm, "CROSS_SPAN must not be in turn2 alone"

    external = {
        "meetings": [{
            "taska_id": EV_ID,
            "actions": [{
                "task": "cross-span action",
                "owner": None,
                "t": 300,
                "span": CROSS_SPAN,
                "status": "gap",
                "node": None,
                "aligned_by": "quote",
            }],
        }],
    }

    result = build_pool(cfg.store, {}, [EV_ID], external=external)
    all_candidates = result[EV_ID]

    # The cross-span candidate should be present but unverifiable
    assert len(all_candidates) == 1
    assert all_candidates[0]["verifiable"] is False

    # write_outputs: unverifiable → pool_unverifiable.json, not in blind batches
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    write_outputs(result, out_dir)

    # pool_unverifiable.json must exist
    assert (out_dir / "pool_unverifiable.json").exists(), "pool_unverifiable.json not written"

    # No judge batch files should be written
    batches = list(out_dir.glob("judge_*.json"))
    assert batches == [], f"expected no judge batches, found: {[b.name for b in batches]}"


def test_shortest_verbatim_turn_quote_wins_as_representative(tmp_path):
    """When two members of a cluster are verbatim in a single turn, the shortest ≥ 8 wins."""
    from pool import build_pool

    cfg = _setup_store(tmp_path)

    # QUOTE_A (15 words) and QUOTE_A_SHORT (12 words) are both verbatim in TURN1
    # They have high shingle containment → eligible to merge (from different sources)
    # Representative should be QUOTE_A_SHORT (shorter, still ≥ 8 words)
    sources = {
        "reader_a": _reader_source(EV_ID, [_reader_item(QUOTE_A)]),
        "reader_b": _reader_source(EV_ID, [_reader_item(QUOTE_A_SHORT)]),
    }

    result = build_pool(cfg.store, sources, [EV_ID])
    verifiable = [c for c in result[EV_ID] if c.get("verifiable", True)]
    # They should merge into one cluster (different sources, high shingle containment)
    assert len(verifiable) == 1, (
        f"expected 1 merged candidate, got {len(verifiable)}"
    )
    # Representative is the shorter one
    from taska_lib.text import norm
    assert len(norm(verifiable[0]["quote"]).split()) <= len(norm(QUOTE_A).split()), (
        f"representative should be ≤ QUOTE_A in length: {verifiable[0]['quote']!r}"
    )


# ── fix-3 tests ───────────────────────────────────────────────────────────────

def test_batch_uses_transcript_casing_and_turn_metadata(tmp_path):
    """Batch quote has original transcript casing; ts/speaker come from the containing turn."""
    from pool import build_pool, write_outputs

    # Transcript with Mixed Case
    MIXED_TURN = "Nora will Prepare the Onboarding Checklist and share it with the Client team"
    turns = [{"speaker": "Nora Quill", "ts": "00:02:10", "text": MIXED_TURN}]
    cfg = _setup_store(tmp_path, turns=turns)

    # Reader submits the quote in lowercase
    SUBMITTED_LOWER = "nora will prepare the onboarding checklist and share it with the client team"
    # Verify norm() of both is the same → verbatim match
    from taska_lib.text import norm
    assert norm(SUBMITTED_LOWER) == norm(MIXED_TURN)

    sources = {
        "reader_a": _reader_source(EV_ID, [
            {
                "quote": SUBMITTED_LOWER,
                "ts": "00:99:99",       # wrong ts from reader
                "speaker": "Wrong Speaker",  # wrong speaker from reader
                "title": "Prepare checklist",
                "kind": "task",
                "commitment": "committed",
                "owner": None,
                "due": None,
                "status": "open",
                "client_owed": False,
                "covered_by": None,
                "note": "Nora commits to preparing the onboarding checklist before the client review on Thursday",
            },
        ]),
    }

    result = build_pool(cfg.store, sources, [EV_ID])
    candidates = result[EV_ID]
    assert len(candidates) == 1

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    write_outputs(result, out_dir)

    batch_file = out_dir / "judge_01.json"
    assert batch_file.exists()
    batch = json.loads(batch_file.read_text(encoding="utf-8"))
    assert len(batch) == 1
    item = batch[0]

    # Batch quote must have original casing (starts with capital N, has capitals)
    assert item["quote"] != SUBMITTED_LOWER, "batch quote must use transcript casing, not reader's lowercase"
    assert "Nora" in item["quote"] or "Prepare" in item["quote"] or "Onboarding" in item["quote"], (
        f"expected original-casing text, got: {item['quote']!r}"
    )
    # ts and speaker come from the turn, not from the reader
    assert item["ts"] == "00:02:10", f"ts should come from turn, got {item['ts']!r}"
    assert item["speaker"] == "Nora Quill", f"speaker should come from turn, got {item['speaker']!r}"


def test_non_verbatim_candidate_excluded_from_batches(tmp_path):
    """A candidate whose quote is not verbatim in any single turn → excluded from judge batches."""
    from pool import build_pool, write_outputs

    turns = [
        {"speaker": "Nora Quill", "ts": "00:02:10", "text": TURN1},
        {"speaker": "Ravi Tamsin", "ts": "00:05:22", "text": TURN2},
    ]
    cfg = _setup_store(tmp_path, turns=turns)

    # QUOTE_A_NEAR has "this" which is not in any turn → not verbatim
    sources = {
        "reader_a": _reader_source(EV_ID, [_reader_item(QUOTE_A_NEAR)]),
    }

    result = build_pool(cfg.store, sources, [EV_ID])
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    write_outputs(result, out_dir)

    # pool_unverifiable.json must exist with this candidate
    assert (out_dir / "pool_unverifiable.json").exists()

    # No judge batch files
    batches = list(out_dir.glob("judge_*.json"))
    assert batches == [], f"expected no judge batches for unverifiable candidate, found: {[b.name for b in batches]}"


# ── existing dedup tests (updated for new rules) ──────────────────────────────

def test_dedupe_merges_two_sources_near_identical_quotes(tmp_path):
    """Two reader sources with near-identical quotes (different sources) → one merged candidate.
    The representative is the one verbatim in a turn; QUOTE_A wins over QUOTE_A_NEAR.
    """
    from pool import build_pool

    cfg = _setup_store(tmp_path)

    sources = {
        "reader_a": _reader_source(EV_ID, [_reader_item(QUOTE_A)]),
        "reader_b": _reader_source(EV_ID, [_reader_item(QUOTE_A_NEAR)]),
    }

    result = build_pool(cfg.store, sources, [EV_ID])

    # Find the verifiable candidate (QUOTE_A is verbatim, QUOTE_A_NEAR is not)
    verifiable = [c for c in result[EV_ID] if c.get("verifiable", True)]
    assert len(verifiable) == 1, f"expected 1 verifiable merged candidate, got {len(verifiable)}"
    c = verifiable[0]
    # Both sources should appear
    assert "reader_a" in c["sources"]
    assert "reader_b" in c["sources"]
    # The verbatim quote (QUOTE_A) should be the representative
    from taska_lib.text import norm
    assert norm(c["quote"]) == norm(QUOTE_A)


def test_candidates_from_taska_events_included(tmp_path):
    """Taska store events with quotes are included as 'taska' source candidates."""
    from pool import build_pool

    cfg = _setup_store(tmp_path)
    _add_taska_event(cfg, "T001", QUOTE_B, ts="00:05:00", speaker="Ravi Tamsin")

    result = build_pool(cfg.store, {}, [EV_ID])

    verifiable = [c for c in result[EV_ID] if c.get("verifiable", True)]
    assert len(verifiable) == 1
    assert "taska" in verifiable[0]["sources"]
    from taska_lib.text import norm
    assert norm(verifiable[0]["quote"]) == norm(QUOTE_B)


def test_propose_events_included_as_taska_source(tmp_path):
    """Propose events (type=agent, op=propose) are included in the 'taska' source."""
    from pool import build_pool

    cfg = _setup_store(tmp_path)
    _add_propose_event(cfg, "P001", QUOTE_C)

    result = build_pool(cfg.store, {}, [EV_ID])

    verifiable = [c for c in result[EV_ID] if c.get("verifiable", True)]
    assert len(verifiable) == 1
    assert "taska" in verifiable[0]["sources"]


def test_echo_spans_included(tmp_path):
    """Echo actions with aligned spans are included as 'echo' source candidates."""
    from pool import build_pool

    cfg = _setup_store(tmp_path)

    external = {
        "meetings": [{
            "taska_id": EV_ID,
            "actions": [
                {"task": "book visit", "owner": "Ravi", "t": 300,
                 "span": QUOTE_B, "status": "gap", "node": None, "aligned_by": "quote"},
                {"task": "irrelevant task", "owner": None, "t": 600,
                 "span": None, "status": "unaligned", "node": None, "aligned_by": None},
            ],
        }],
    }

    result = build_pool(cfg.store, {}, [EV_ID], external=external)

    verifiable = [c for c in result[EV_ID] if c.get("verifiable", True)]
    assert len(verifiable) == 1
    assert "echo" in verifiable[0]["sources"]
    from taska_lib.text import norm
    assert norm(verifiable[0]["quote"]) == norm(QUOTE_B)


def test_judge_batch_files_carry_no_sources(tmp_path):
    """Judge batch files (judge_NN.json) do not include the 'sources' field."""
    from pool import build_pool, write_outputs

    cfg = _setup_store(tmp_path)
    _add_taska_event(cfg, "T001", QUOTE_A)

    result = build_pool(cfg.store, {}, [EV_ID])
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    write_outputs(result, out_dir, batch_size=40)

    # pool_full.json has sources
    full = json.loads((out_dir / "pool_full.json").read_text(encoding="utf-8"))
    assert full[EV_ID][0].get("sources") is not None

    # judge_01.json has no sources
    judge_file = out_dir / "judge_01.json"
    assert judge_file.exists(), "judge_01.json not written"
    batch = json.loads(judge_file.read_text(encoding="utf-8"))
    assert isinstance(batch, list)
    assert len(batch) == 1
    assert "sources" not in batch[0]
    assert "verifiable" not in batch[0]
    for key in ("cid", "evidence", "quote", "ts", "speaker"):
        assert key in batch[0], f"missing key {key!r} in judge batch item"


def test_stable_cids_sorted_by_offset(tmp_path):
    """Cids are assigned in transcript offset order and remain stable on re-run."""
    from pool import build_pool

    cfg = _setup_store(tmp_path)

    # QUOTE_A appears before QUOTE_B in the single-turn transcript
    sources = {
        "reader_a": _reader_source(EV_ID, [
            _reader_item(QUOTE_B, ts="00:05:00"),
            _reader_item(QUOTE_A, ts="00:00:01"),
        ]),
    }

    result1 = build_pool(cfg.store, sources, [EV_ID])
    result2 = build_pool(cfg.store, sources, [EV_ID])

    # Filter to just verifiable
    cids_1 = [c["cid"] for c in result1[EV_ID] if c.get("verifiable", True)]
    cids_2 = [c["cid"] for c in result2[EV_ID] if c.get("verifiable", True)]

    # Stable across runs
    assert cids_1 == cids_2

    # QUOTE_A (offset ~0) should get a lower cid index than QUOTE_B
    verifiable = [c for c in result1[EV_ID] if c.get("verifiable", True)]
    from taska_lib.text import norm
    quotes = [norm(c["quote"]) for c in verifiable]
    assert quotes.index(norm(QUOTE_A)) < quotes.index(norm(QUOTE_B))


def test_only_requested_evidence_ids_returned(tmp_path):
    """build_pool only returns candidates for the requested evidence ids."""
    from pool import build_pool

    cfg = _setup_store(tmp_path)

    OTHER_ID = "2026-03-03_meetings_ee99aa11"
    S.write_evidence(cfg.store, {
        "id": OTHER_ID, "kind": "meeting",
        "at": "2026-03-03T10:00:00", "title": "Another meeting",
        "source": "meetings", "text": "some text", "turns": [],
    })

    result = build_pool(cfg.store, {}, [EV_ID])
    assert EV_ID in result
    assert OTHER_ID not in result


def test_three_distinct_quotes_produce_three_candidates(tmp_path):
    """Three well-separated quotes produce three distinct candidates."""
    from pool import build_pool

    cfg = _setup_store(tmp_path)

    sources = {
        "reader_a": _reader_source(EV_ID, [
            _reader_item(QUOTE_A),
            _reader_item(QUOTE_B),
            _reader_item(QUOTE_C),
        ]),
    }

    result = build_pool(cfg.store, sources, [EV_ID])

    verifiable = [c for c in result[EV_ID] if c.get("verifiable", True)]
    assert len(verifiable) == 3
    cids = {c["cid"] for c in verifiable}
    assert len(cids) == 3


def test_write_outputs_clears_stale_batches(tmp_path):
    """write_outputs removes old judge_*.json files before writing new ones."""
    from pool import build_pool, write_outputs

    cfg = _setup_store(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    # Write a stale batch file
    stale = out_dir / "judge_99.json"
    stale.write_text("[]", encoding="utf-8")

    result = build_pool(cfg.store, {}, [EV_ID])  # empty pool
    write_outputs(result, out_dir)

    assert not stale.exists(), "stale judge_99.json should have been cleared"


def test_cli_help_exits_zero():
    """pool.py --help exits 0 (tests that imports work in the subprocess)."""
    r = subprocess.run(
        [sys.executable, str(EVALS_DIR / "pool.py"), "--help"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, f"--help failed:\n{r.stderr}"

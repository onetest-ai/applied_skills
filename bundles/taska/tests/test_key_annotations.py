"""Key annotations: field modes, validation status and rule references kept beside a frozen key, never inside it.
Invented data only."""
import json
import sys

import pytest

from conftest import TASKA_ROOT
from taska_lib import evalscore as ES

sys.path.insert(0, str(TASKA_ROOT / "evals"))
import key_tools as KT  # noqa: E402

ITEMS = [{"cid": "k1", "evidence": "m1", "kind": "task", "commitment": "committed", "owner": "Nora Quill", "due": None},
         {"cid": "k2", "evidence": "m1", "kind": "task", "commitment": "committed", "owner": None, "due": None}]


def _key(tmp_path):
    return KT.write_version(tmp_path, ITEMS, "v1", "2026-11-04", "unit")


def test_annotations_sit_beside_the_key_and_never_change_its_bytes(tmp_path):
    key = _key(tmp_path)
    before = key.read_bytes()
    ann = KT.write_annotations(key, [{"cid": "k2", "fields": {"owner": "must_be_unknown"}, "validation_status": "agent_adjudicated",
                                      "rule_ids": ["OWNER-NO-INVENTION"], "provenance": {"reader_runs": ["a", "b"]}}],
                               "ann-1", "2026-11-04", "pilot")
    assert key.read_bytes() == before and ann.name == "items.v1.ann-1.jsonl"
    head, items = ES.load_gold(key)
    assert head["annotations"]["version"] == "ann-1" and len(head["annotations"]["sha256"]) == 64
    k1, k2 = items
    assert k1["field_modes"] == {"owner": "exact", "due": "not_annotated", "status": "not_annotated", "client_owed": "not_annotated"}
    assert k2["field_modes"]["owner"] == "must_be_unknown" and k2["validation_status"] == "agent_adjudicated"
    assert k2["rule_ids"] == ["OWNER-NO-INVENTION"] and k1["validation_status"] == "unrecorded"


def test_a_key_without_annotations_reads_as_before_with_implied_modes(tmp_path):
    head, items = ES.load_gold(_key(tmp_path))
    assert "annotations" not in head
    assert items[1]["field_modes"]["owner"] == "not_annotated" and items[0]["field_modes"]["owner"] == "exact"


@pytest.mark.parametrize("record, says", [
    ({"cid": "k9", "fields": {}}, "unknown key item k9"),
    ({"cid": "k1", "fields": {"owner": "must_be_unknown"}}, "k1: owner has a value"),
    ({"cid": "k2", "fields": {"owner": "exact"}}, "k2: owner is exact but empty"),
    ({"cid": "k1", "fields": {"colour": "exact"}}, "k1: no field colour"),
    ({"cid": "k1", "fields": {"owner": "maybe"}}, "k1: owner mode maybe"),
    ({"cid": "k1", "validation_status": "gold"}, "k1: validation status gold"),
    ({"cid": "k1", "rule_ids": ["owner rule"]}, "k1: rule id 'owner rule'"),
])
def test_invalid_annotations_are_refused_before_anything_is_written(tmp_path, record, says):
    key = _key(tmp_path)
    with pytest.raises(ValueError, match=says):
        KT.write_annotations(key, [record], "ann-1", "2026-11-04", "bad")
    assert not list(tmp_path.glob("*.ann-*.jsonl"))


def test_annotations_are_versions_never_overwritten_and_the_newest_is_read(tmp_path):
    key = _key(tmp_path)
    KT.write_annotations(key, [{"cid": "k2", "fields": {"owner": "must_be_unknown"}}], "ann-1", "2026-11-04", "one")
    with pytest.raises(FileExistsError):
        KT.write_annotations(key, [], "ann-1", "2026-11-04", "again")
    KT.write_annotations(key, [{"cid": "k2", "validation_status": "human_validated"}], "ann-2", "2026-11-05", "two")
    head, items = ES.load_gold(key)
    assert head["annotations"]["version"] == "ann-2" and items[1]["validation_status"] == "human_validated"
    assert items[1]["field_modes"]["owner"] == "not_annotated"          # ann-2 is a whole version, not a patch


def test_an_edited_or_foreign_annotations_file_is_refused(tmp_path):
    key = _key(tmp_path)
    ann = KT.write_annotations(key, [{"cid": "k2", "fields": {"owner": "must_be_unknown"}}], "ann-1", "2026-11-04", "x")
    ann.write_text(ann.read_text().replace("must_be_unknown", "not_annotated"))
    with pytest.raises(ValueError, match="edited in place"):
        ES.load_gold(key)
    other = KT.write_version(tmp_path, ITEMS[:1], "v2", "2026-11-04", "other key")
    ann.rename(tmp_path / "items.v2.ann-1.jsonl")
    with pytest.raises(ValueError):
        ES.load_gold(other)


def test_a_new_key_version_written_from_loaded_items_carries_no_annotation_fields(tmp_path):
    key = _key(tmp_path)
    KT.write_annotations(key, [{"cid": "k2", "fields": {"owner": "must_be_unknown"}}], "ann-1", "2026-11-04", "x")
    _, items = ES.load_gold(key)
    new = KT.write_version(tmp_path, items, "v2", "2026-11-05", "copy")
    assert all(set(json.loads(l)) == set(ITEMS[0]) for l in new.read_text().splitlines()[1:])


def test_an_owner_the_key_marks_unscored_is_not_annotated(tmp_path):
    key = KT.write_version(tmp_path, [dict(ITEMS[0], owner_unscored=True)], "v1", "2026-11-04", "unit")
    assert ES.load_gold(key)[1][0]["field_modes"]["owner"] == "not_annotated"

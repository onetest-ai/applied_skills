import json
import subprocess
import sys
from pathlib import Path

EVALS = Path(__file__).resolve().parents[1] / "evals"
sys.path.insert(0, str(EVALS))

import marker_recall as MR

QUOTES = ["Please check the slow screens before Friday afternoon",
          "The weather was lovely in the harbor district all week long",
          "Nora will call the carrier about the delayed pallets",
          "Everything else on the agenda was simply read out loud again"]


def _key(path: Path, quotes: list[str]) -> Path:
    path.write_text("# gold v1 2026-11-03 n=4 sha256=abc\n" + "".join(json.dumps({"quote": q}) + "\n" for q in quotes))
    return path


def test_recall_is_the_share_of_key_quotes_the_markers_match(tmp_path):
    key = _key(tmp_path / "items.jsonl", QUOTES)
    r = MR.recall(key)
    assert r == {"key": str(key), "quotes": 4, "matched": 2, "recall": 0.5}


def test_the_cli_prints_json_per_key_and_the_store_median(tmp_path):
    key = _key(tmp_path / "items.jsonl", QUOTES)
    ev = tmp_path / "proj" / ".taska" / "evidence"
    ev.mkdir(parents=True)
    turns = [{"ts": "00:00:00", "speaker": "A", "text": "the harbor was calm and the day went on without a single event today. " * 40},
             {"ts": "00:10:00", "speaker": "B", "text": "Nora will send the forklift certification list to the client by Thursday"}]
    (ev / "m1.json").write_text(json.dumps({"id": "m1", "kind": "meeting", "at": "2026-03-02T10:00:00", "title": "t",
                                            "source": "meetings", "turns": turns}))
    (ev / "n1.json").write_text(json.dumps({"id": "n1", "kind": "note", "at": "2026-03-02T10:00:00", "title": "t",
                                            "source": "x", "text": "Nora will send the list to the client by Thursday"}))
    out = subprocess.run([sys.executable, str(EVALS / "marker_recall.py"), "--key", str(key), "--store", str(tmp_path / "proj")],
                         capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    assert data["keys"][0]["recall"] == 0.5
    assert data["store"]["meetings"] == 1 and data["store"]["median_per_3000_words"] > 0

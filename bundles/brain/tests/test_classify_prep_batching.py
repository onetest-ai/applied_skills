import json
import sqlite3
from pathlib import Path

import classify_prep as C


def items(n: int, preview: str = "x") -> list[dict[str, int | str]]:
    return [{"id": i, "source": "d.vtt.md", "title": f"t{i}", "preview": preview} for i in range(n)]


def size(batch: list[dict[str, int | str]]) -> int:
    return len(json.dumps(batch, indent=1).encode())


def test_no_batch_exceeds_max_chunks_and_all_ids_kept_in_order():
    out = C.split_batches(items(1000), max_chunks=150, max_bytes=10**9, min_batches=1)
    assert max(len(b) for b in out) <= 150
    assert [x["id"] for b in out for x in b] == list(range(1000))


def test_no_batch_exceeds_max_bytes():
    out = C.split_batches(items(300, "y" * 400), max_chunks=150, max_bytes=20000, min_batches=1)
    assert all(size(b) <= 20000 for b in out)
    assert sum(len(b) for b in out) == 300


def test_min_batches_is_a_floor_and_never_empty():
    out = C.split_batches(items(10), max_chunks=150, max_bytes=10**9, min_batches=25)
    assert len(out) == 10 and all(out)


def test_single_oversized_item_is_its_own_batch():
    out = C.split_batches(items(1, "z" * 50000), max_chunks=150, max_bytes=1000, min_batches=1)
    assert out == [items(1, "z" * 50000)]


def test_cli_writes_capped_batches(tmp_path: Path):
    db = tmp_path / "k.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE chunks(id INTEGER, source TEXT, title TEXT, text TEXT)")
    con.executemany("INSERT INTO chunks VALUES(?,?,?,?)", [(i, "d.vtt.md", f"t{i}", "word " * 20) for i in range(400)])
    con.commit()
    con.close()
    tax = tmp_path / "t.json"
    tax.write_text(json.dumps({"intent_taxonomy": {"l1": ["A"], "tree": {"A": []}}}))
    C.main(["--db", str(db), "--taxonomy", str(tax), "--out", str(tmp_path / "o"), "--batches", "2"])
    batches = sorted((tmp_path / "o").glob("batch_*.json"))
    assert len(batches) == 3 and all(len(json.loads(b.read_text())) <= 150 for b in batches)


def test_byte_cap_sizes_the_batch_count_up_front():
    # 300 x 400-char previews: 150 of them overflow 60 KB but 100 fit, so 3 even
    # batches suffice. Splitting by chunk count first and halving each overflowing
    # half afterwards gives 4 — one extra agent start-up per such run.
    its = items(300, "y" * 400)
    assert size(its[:100]) <= 60000 < size(its[:150])
    out = C.split_batches(its, max_chunks=150, max_bytes=60000, min_batches=1)
    assert len(out) == 3
    assert all(size(b) <= 60000 for b in out)
    assert [x["id"] for b in out for x in b] == list(range(300))

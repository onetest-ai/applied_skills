import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "skills" / "knowledge-index"))
import fact_write as W  # noqa: E402


def _fake_embed(pairs):
    # deterministic stub: identical strings -> vector [1,0]; contains "deploy"/"go-live" -> near
    def emb(model, texts):
        out = []
        for t in texts:
            if "go-live" in t or "deploy" in t:
                out.append([1.0, 0.05])
            elif "budget" in t:
                out.append([0.0, 1.0])
            else:
                out.append([0.3, 0.3])
        return out
    return emb


def test_exact_normalized_match_is_exact_band():
    key, band = W.resolve_key("Go-Live", "date", [("go-live", "date")], {}, _fake_embed(None), 0.90, 0.75)
    assert band == "exact" and key == ("go-live", "date")


def test_alias_maps_to_existing_key():
    key, band = W.resolve_key("Deployment", "date", [("go-live", "date")],
                              {"deployment|date": "go-live|date"}, _fake_embed(None), 0.90, 0.75)
    assert band == "exact" and key == ("go-live", "date")


def test_high_cosine_auto_merges_to_existing():
    key, band = W.resolve_key("deploy window", "date", [("go-live", "date")], {}, _fake_embed(None), 0.90, 0.75)
    assert band == "auto" and key == ("go-live", "date")


def test_low_cosine_is_distinct():
    key, band = W.resolve_key("budget", "amount", [("go-live", "date")], {}, _fake_embed(None), 0.90, 0.75)
    assert band == "distinct" and key == ("budget", "amount")

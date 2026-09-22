import json, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "skills" / "knowledge-index"))
import fact_write as W  # noqa: E402


def test_default_judge_model_is_sonnet():
    """DEFAULT_JUDGE_MODEL_ID is a Bedrock Sonnet inference profile id, not Haiku."""
    assert "sonnet" in W.DEFAULT_JUDGE_MODEL_ID.lower()
    assert "haiku" not in W.DEFAULT_JUDGE_MODEL_ID.lower()


def test_bedrock_judge_default_uses_default_model_id():
    import inspect
    sig = inspect.signature(W._bedrock_judge)
    assert sig.parameters["model_id"].default == W.DEFAULT_JUDGE_MODEL_ID


def test_bedrock_judge_model_override(monkeypatch):
    """--judge-model overrides the model id used in the Bedrock invoke_model call."""
    captured = {}

    class _FakeBody:
        def read(self):
            return json.dumps({"content": [{"text": '{"relation": "keep_both"}'}]}).encode("utf-8")

    class _FakeClient:
        def invoke_model(self, modelId, body):
            captured["modelId"] = modelId
            return {"body": _FakeBody()}

    class _FakeBoto3:
        @staticmethod
        def client(*a, **k):
            return _FakeClient()

    monkeypatch.setitem(sys.modules, "boto3", _FakeBoto3())
    judge = W._bedrock_judge(model_id="us.anthropic.claude-custom-1:0")
    dis = {"new_id": "n", "prior_id": "o", "new_value": "Q3", "prior_value": "Q2"}
    judge(dis)
    assert captured["modelId"] == "us.anthropic.claude-custom-1:0"


def test_judge_supersedes_becomes_link():
    dis = [{"new_id": "n", "prior_id": "o", "new_value": "Q3", "prior_value": "Q2"}]
    links, unresolved = W.judge_disagreements(dis, lambda d: {"relation": "supersedes"})
    assert links == [("n", "supersedes", "o")] and unresolved == []


def test_judge_contradicts_becomes_link():
    dis = [{"new_id": "n", "prior_id": "o", "new_value": "Q3", "prior_value": "Q2"}]
    links, _ = W.judge_disagreements(dis, lambda d: {"relation": "contradicts"})
    assert links == [("n", "contradicts", "o")]


def test_judge_keep_both_no_link():
    dis = [{"new_id": "n", "prior_id": "o", "new_value": "Q3", "prior_value": "Q2"}]
    links, unresolved = W.judge_disagreements(dis, lambda d: {"relation": "keep_both"})
    assert links == [] and unresolved == []


def test_judge_error_is_unresolved():
    dis = [{"new_id": "n", "prior_id": "o", "new_value": "Q3", "prior_value": "Q2"}]
    def boom(d): raise RuntimeError("llm down")
    links, unresolved = W.judge_disagreements(dis, boom)
    assert links == [] and unresolved == dis


def test_judge_unknown_relation_is_unresolved():
    dis = [{"new_id": "n", "prior_id": "o", "new_value": "Q3", "prior_value": "Q2"}]
    links, unresolved = W.judge_disagreements(dis, lambda d: {"relation": "bogus"})
    assert links == [] and unresolved == dis


def test_judge_missing_relation_key_is_unresolved():
    dis = [{"new_id": "n", "prior_id": "o", "new_value": "Q3", "prior_value": "Q2"}]
    links, unresolved = W.judge_disagreements(dis, lambda d: {})
    assert links == [] and unresolved == dis

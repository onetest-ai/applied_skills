import brain_mcp_client as c


class _FakeResult:
    def __init__(self, data):
        self.data = data
        self.structured_content = data


def test_call_tool_returns_structured_dict(monkeypatch):
    captured = {}

    class _FakeClient:
        def __init__(self, transport):
            captured['transport'] = transport

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def call_tool(self, name, args):
            captured['name'] = name
            captured['args'] = args
            return _FakeResult({"hits": [{"source": "x.md"}]})

    monkeypatch.setattr(c, "Client", _FakeClient)
    monkeypatch.setattr(c, "StreamableHttpTransport", lambda url, headers=None: {"url": url, "headers": headers})
    out = c.call_tool("search_knowledge", {"query": "q", "limit": 5},
                      url="http://ex/mcp", key="SEKRET", key_header="x-api-key")
    assert out == {"hits": [{"source": "x.md"}]}
    assert captured['name'] == "search_knowledge"
    assert captured['transport']["headers"] == {"x-api-key": "SEKRET"}


def test_call_tool_keyless_sends_no_auth_header(monkeypatch):
    # A local keyless brain (http://127.0.0.1:8001/mcp) is called with an empty header set.
    monkeypatch.delenv("BRAIN_API_KEY", raising=False)
    captured = {}

    class _FakeClient:
        def __init__(self, transport):
            captured['transport'] = transport

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def call_tool(self, name, args):
            return _FakeResult({"status": "healthy"})

    monkeypatch.setattr(c, "Client", _FakeClient)
    monkeypatch.setattr(c, "StreamableHttpTransport", lambda url, headers=None: {"url": url, "headers": headers})
    out = c.call_tool("health", {}, url="http://127.0.0.1:8001/mcp", key=None)
    assert out == {"status": "healthy"}
    assert captured['transport']["headers"] == {}


def test_call_tool_raises_without_url(monkeypatch):
    monkeypatch.delenv("BRAIN_MCP_URL", raising=False)
    try:
        c.call_tool("health", {}, url=None, key="K")
        assert False, "expected error"
    except c.BrainClientError:
        pass

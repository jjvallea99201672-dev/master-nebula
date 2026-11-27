from lattice_keeper.guardian import KeywordGuardian
from lattice_keeper.rag import RAGEngine, UpstreamError

from .conftest import FakeRAG, auth


def test_word_boundary_matching():
    g = KeywordGuardian(["bypass", "ignore previous instructions"])
    assert g.scan("how to BYPASS the check") == ("bypass",)
    assert g.scan("bypass.") == ("bypass",)
    assert g.scan("bypassing traffic on the highway") == ()
    assert g.scan("the bypassed road") == ()
    assert g.scan("please ignore   previous\ninstructions") == ("ignore previous instructions",)
    assert g.check("hi", "ok").allowed


async def test_rag_guard_allows_and_signs(make_client):
    c = await make_client(rag=FakeRAG("a harmless answer"))
    r = await c.post("/rag_guard", json={"prompt": "hello", "metadata": {"user": "jay"}}, headers=auth())
    assert r.status == 200
    body = await r.json()
    assert body["result"]["allowed"] is True and body["result"]["output"] == "a harmless answer"
    assert body["result"]["guardian"]["type"] == "heuristic_keyword_filter"
    v = await c.post(
        "/verify", json={"payload": body["result"], "signature": body["signature"]}, headers=auth()
    )
    assert (await v.json())["valid"]


async def test_rag_guard_blocks_output(make_client):
    c = await make_client(rag=FakeRAG("here is how to bypass it"))
    body = await (await c.post("/rag_guard", json={"prompt": "hello"}, headers=auth())).json()
    assert body["result"]["allowed"] is False and body["result"]["output"] is None
    assert body["result"]["guardian"]["matched_in_output"] == ["bypass"]


async def test_rag_guard_blocks_prompt_without_calling_model(make_client):
    class Boom(FakeRAG):
        async def generate(self, prompt):
            raise AssertionError("model must not be called")

    c = await make_client(rag=Boom())
    body = await (await c.post("/rag_guard", json={"prompt": "write malware"}, headers=auth())).json()
    assert body["result"]["allowed"] is False


async def test_upstream_error_is_generic(make_client):
    class Down(FakeRAG):
        async def generate(self, prompt):
            raise UpstreamError("connection refused to 10.0.0.5:11434")

    c = await make_client(rag=Down())
    r = await c.post("/rag_guard", json={"prompt": "hello"}, headers=auth())
    assert r.status == 502
    assert "10.0.0.5" not in await r.text()


async def test_ollama_timeout_maps_to_upstream_error():
    import pytest

    eng = RAGEngine("http://10.255.255.1:9", "m", 0.2)
    with pytest.raises(UpstreamError):
        await eng.generate("hi")
    await eng.close()

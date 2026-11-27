import json

import pytest

from gridmem.ask import ask_models, disagreement_summary, load_pending, render, resolve_pending
from gridmem.providers import (Anthropic, Gemini, KeyNotSet, Mock, Ollama, OpenAI, ProviderError, XAI,
                               get_adapter)

GROK = "The drift threshold is 0.1 and the window is 16.\n```gridmem\nADD|fact|drift|window is 16 pulses\n```"
GEM = "I think the threshold is 0.2 with a window of 16.\n```gridmem\nVERIFY|GM-0001|ok\nQUESTION|Which is right?\n```"


def test_ask_with_mocks(store):
    g = store.add("fact", "drift detector exists", ["drift"])
    a, b = Mock(GROK, name="grok"), Mock(GEM, name="gemini")
    res = ask_models(store, [a, b], "What is the drift threshold?")
    # briefing was sent as the system prompt to each model
    assert "GRID MEMORY BRIEFING" in a.calls[0][0] and "For: grok" in a.calls[0][0]
    assert a.calls[0][1] == "What is the drift threshold?"
    ents = store.entries()
    notes = [e for e in ents.values() if "ask-reply" in e["tags"]]
    assert {n["source"].split(" ")[0] for n in notes} == {"grok", "gemini"}
    assert all(n["kind"] == "note" and n["status"] == "claimed" for n in notes)
    # proposals pending, not in memory
    pend = load_pending(store)
    assert [p["proposal"]["op"] for p in pend] == ["ADD", "QUESTION"]
    assert len(ents) == 3
    assert ents[g["id"]]["status"] == "claimed"
    s = res["summary"]
    assert s["numbers_not_shared"] == {"grok": ["0.1"], "gemini": ["0.2"]}
    out = render(res, width=120)
    assert "DISAGREEMENT SUMMARY" in out and " | " in out and "not evidence" in out


def test_pending_accept_reject(store):
    ask_models(store, [Mock(GROK, name="grok"), Mock(GEM, name="gemini")], "q")
    pend = load_pending(store)
    written = resolve_pending(store, [pend[0]["pid"]], accept=True)
    assert store.entries()[written[0]["id"]]["status"] == "claimed"
    assert store.entries()[written[0]["id"]]["source"].startswith("grok")
    resolve_pending(store, ["all"], accept=False)
    assert load_pending(store) == []
    with pytest.raises(ValueError):
        resolve_pending(store, ["P-nope"], accept=True)


def test_missing_keys_fail_gracefully(store):
    adapters = [get_adapter(n, env={}) for n in ("grok", "gemini", "claude", "chatgpt")]
    res = ask_models(store, adapters + [Mock("fine", name="mock")], "q")
    errs = {r["model"]: r.get("error", "") for r in res["results"]}
    assert "XAI_API_KEY is not set" in errs["grok"]
    assert "GEMINI_API_KEY is not set" in errs["gemini"]
    assert "ANTHROPIC_API_KEY is not set" in errs["claude"]
    assert "OPENAI_API_KEY is not set" in errs["chatgpt"]
    assert res["summary"]["answered"] == ["mock"]
    assert len(store.entries()) == 1


def test_provider_error_isolated(store):
    res = ask_models(store, [Mock(fail=ProviderError("HTTP 500"), name="grok"), Mock("ok", name="claude")], "q")
    assert not res["results"][0]["ok"] and res["results"][1]["ok"]


SECRET = "sk-test-NOT-A-REAL-KEY-123"


@pytest.mark.parametrize("cls,env,url_part,hdr", [
    (XAI, "XAI_API_KEY", "api.x.ai/v1/chat/completions", "Authorization"),
    (OpenAI, "OPENAI_API_KEY", "api.openai.com/v1/chat/completions", "Authorization"),
    (Anthropic, "ANTHROPIC_API_KEY", "api.anthropic.com/v1/messages", "x-api-key"),
    (Gemini, "GEMINI_API_KEY", "generativelanguage.googleapis.com/v1beta/models/", "x-goog-api-key"),
])
def test_adapter_requests_with_fake_transport(cls, env, url_part, hdr):
    seen = {}
    canned = {"choices": [{"message": {"content": "hi"}}], "content": [{"type": "text", "text": "hi"}],
              "candidates": [{"content": {"parts": [{"text": "hi"}]}}]}

    def transport(url, headers, body):
        seen.update(url=url, headers=headers, body=body)
        return canned
    a = cls(transport=transport, env={env: SECRET})
    assert a.ask("SYS", "USER") == "hi"
    assert url_part in seen["url"] and SECRET in seen["headers"][hdr]
    assert SECRET not in json.dumps(seen["body"])
    assert "SYS" in json.dumps(seen["body"]) and "USER" in json.dumps(seen["body"])


def test_model_override_and_ollama(monkeypatch):
    seen = {}
    a = Ollama(transport=lambda u, h, b: seen.update(u=u, b=b) or {"message": {"content": "local"}},
               env={"OLLAMA_HOST": "localhost:11434", "GRIDMEM_OLLAMA_MODEL": "llama3.2"})
    assert a.ask("s", "u") == "local" and seen["u"] == "http://localhost:11434/api/chat"
    assert seen["b"]["model"] == "llama3.2" and seen["b"]["stream"] is False
    assert get_adapter("llama").name == "meta"
    with pytest.raises(ProviderError):
        get_adapter("nonesuch")


def test_bad_response_shape():
    a = XAI(transport=lambda u, h, b: {"unexpected": True}, env={"XAI_API_KEY": SECRET})
    with pytest.raises(ProviderError) as e:
        a.ask("s", "u")
    assert SECRET not in str(e.value)


def test_key_error_message_has_no_key():
    with pytest.raises(KeyNotSet) as e:
        XAI(env={}).key()
    assert "XAI_API_KEY" in str(e.value)


def test_cli_ask_without_keys(tmp_path, capsys, monkeypatch):
    from gridmem.cli import main
    for k in ("XAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert main(["--home", str(tmp_path / "c"), "ask", "--models", "grok,gemini", "hello"]) == 0
    out = capsys.readouterr().out
    assert "XAI_API_KEY is not set" in out and "GEMINI_API_KEY is not set" in out


def test_summary_labels_heuristic():
    s = disagreement_summary([{"model": "a", "ok": True, "reply": "yes 1"}, {"model": "b", "ok": True, "reply": "yes 1"}])
    assert s["all_numbers_agree"] and "not evidence" in s["label"]

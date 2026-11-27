import pytest

from gridmem.store import GridMemError


def test_correction_keeps_original(store):
    a = store.add("fact", "N_eff = N/(1+0.3*beta)", ["physics"])
    c = store.correct(a["id"], "N_eff = N/(1 + c*kappa), c fitted")
    ents = store.entries()
    assert ents[a["id"]]["status"] == "superseded"
    assert ents[a["id"]]["text"] == "N_eff = N/(1+0.3*beta)"
    assert ents[a["id"]]["superseded_by"] == [c["id"]]
    assert ents[c["id"]]["supersedes"] == a["id"] and ents[c["id"]]["status"] == "claimed"
    assert ents[c["id"]]["tags"] == ["physics"]


def test_retraction_visible(store):
    a = store.add("fact", "approved for mainnet")
    r = store.retract(a["id"], "no auditor; chat-generated certificate")
    ents = store.entries()
    assert ents[a["id"]]["status"] == "retracted" and ents[a["id"]]["retracted_by"] == r["id"]
    assert a["id"] in [e["id"] for e in store.search("mainnet")]


def test_history_follows_chain(store):
    a = store.add("fact", "v1")
    b = store.correct(a["id"], "v2")
    c = store.correct(b["id"], "v3")
    store.record_agreement(b["id"], "grok")
    ids = [r["id"] for r in store.history(a["id"])]
    assert ids[:3] == [a["id"], b["id"], c["id"]] and len(ids) == 4
    assert [r["id"] for r in store.history(c["id"])][:3] == ids[:3]


def test_no_delete_api(store):
    assert not hasattr(store, "delete")
    with pytest.raises(GridMemError):
        store.correct("GM-9999", "x")
    with pytest.raises(GridMemError):
        store.add("correction", "missing target")


def test_search_filters(store):
    store.add("fact", "alpha", ["a"]); store.add("note", "beta", ["b"])
    assert [e["text"] for e in store.search(tag="b")] == ["beta"]
    assert [e["text"] for e in store.search(kind="fact")] == ["alpha"]
    assert [e["text"] for e in store.search("ALP")] == ["alpha"]

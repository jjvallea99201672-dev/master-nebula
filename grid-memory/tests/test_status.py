import hashlib

import pytest

from gridmem import evidence as ev
from gridmem.store import GridMemError


def test_default_claimed(store):
    e = store.add("fact", "x")
    assert store.entries()[e["id"]]["status"] == "claimed"


def test_agreement_never_verifies(store):
    e = store.add("fact", "genesis hash is verified")
    for m in ("grok", "gemini", "claude", "chatgpt", "meta"):
        store.record_agreement(e["id"], m, "I confirm this is verified")
    ent = store.entries()[e["id"]]
    assert ent["status"] == "claimed"
    assert len(ent["agreed_by"]) == 5
    assert all(a["label"] == "agreement is not evidence" for a in ent["agreed_by"])


def test_hash_evidence_verifies(store, tmp_path):
    f = tmp_path / "genesis.txt"; f.write_bytes(b"hello\n")
    good = hashlib.sha256(b"hello\n").hexdigest()
    e = store.add("fact", "genesis.txt hashes to " + good)
    r = ev.hash_check(str(f), good)
    assert r["result"] == "pass" and r["computed_sha256"] == good
    store.attach_evidence(e["id"], r)
    assert store.entries()[e["id"]]["status"] == "verified"


def test_bad_hash_fails(store, tmp_path):
    f = tmp_path / "g.txt"; f.write_bytes(b"not the preimage")
    anchor = "fd0adf9005622e103f02115a50314d91f83dc705f1d685b16d45f52ad6b940b2"
    e = store.add("fact", "g.txt is the genesis preimage")
    r = ev.hash_check(str(f), anchor)
    assert r["result"] == "fail"
    store.attach_evidence(e["id"], r)
    assert store.entries()[e["id"]]["status"] == "check-failed"


def test_hash_must_be_hex64(tmp_path):
    f = tmp_path / "a"; f.write_text("a")
    with pytest.raises(GridMemError):
        ev.hash_check(str(f), "verified")


def test_evidence_without_result_refused(store):
    e = store.add("fact", "x")
    with pytest.raises(GridMemError):
        store.attach_evidence(e["id"], {"type": "hash", "computed_sha256": "trust me"})


def test_operator_confirm_distinct(store):
    e = store.add("decision", "build order")
    store.operator_confirm(e["id"], "yes")
    assert store.entries()[e["id"]]["status"] == "operator-confirmed"


def test_url_needs_operator_confirmation():
    with pytest.raises(GridMemError):
        ev.url_reference("https://example.org/doc", operator_confirmed=False)
    assert ev.url_reference("https://example.org/doc", True)["result"] == "pass"


def test_output_and_command_evidence(store, tmp_path):
    out = tmp_path / "out.txt"; out.write_text("...\n34/34 checks passed (final)\n")
    assert ev.output_check(str(out), "34/34 checks passed (final)")["result"] == "pass"
    assert ev.output_check(str(out), "35/35")["result"] == "fail"
    assert ev.command_check(["true"])["result"] == "pass"
    assert ev.command_check(["false"])["result"] == "fail"
    assert ev.command_check(["echo", "ok 3"], expect="ok 3")["result"] == "pass"
    assert ev.command_check(["no-such-binary-xyz"])["exit_code"] == 127


def test_recheck_detects_changed_file(store, tmp_path):
    out = tmp_path / "out.txt"; out.write_text("PASS\n")
    r = ev.output_check(str(out), "PASS")
    out.write_text("PASS\nedited\n")
    assert ev.recheck(r)["result"] == "changed"
    out.unlink()
    assert ev.recheck(r)["result"] == "fail"


def test_later_failed_check_overrides(store, tmp_path):
    f = tmp_path / "f"; f.write_text("a")
    h = hashlib.sha256(b"a").hexdigest()
    e = store.add("fact", "f")
    store.attach_evidence(e["id"], ev.hash_check(str(f), h))
    f.write_text("b")
    store.attach_evidence(e["id"], ev.hash_check(str(f), h))
    assert store.entries()[e["id"]]["status"] == "check-failed"


def test_seed_is_honest(tmp_path):
    from gridmem.seed import seed
    from gridmem.store import Store
    s = Store(tmp_path / "seed")
    seed(s)
    ents = list(s.entries().values())
    g = [e for e in ents if "genesis" in e["tags"]][0]
    assert g["status"] == "claimed" and "unverified" in g["status_note"]
    assert "fd0adf9005622e103f02115a50314d91f83dc705f1d685b16d45f52ad6b940b2" in g["text"]
    with pytest.raises(GridMemError):
        seed(s)
    s.close()

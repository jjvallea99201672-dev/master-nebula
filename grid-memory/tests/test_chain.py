import json
import sqlite3

import pytest

from gridmem.store import GridMemError, Store


def _rows(st):
    return st.conn.execute("SELECT seq, body FROM events ORDER BY seq").fetchall()


def test_fresh_chain_ok_and_linked(store):
    a = store.add("fact", "one", ["x"])
    b = store.add("note", "two")
    assert b["prev_hash"] == a["entry_hash"]
    r = store.check()
    assert r["ok"] and r["rows_checked"] == 2 and r["head"] == b["entry_hash"]
    assert r["merkle_root_rfc6962"]


def test_edit_detected(store):
    store.add("fact", "the sky is green")
    store.add("note", "x")
    body = json.loads(_rows(store)[0][1]); body["text"] = "the sky is blue"
    store.conn.execute("UPDATE events SET body=? WHERE seq=1", (json.dumps(body, sort_keys=True),))
    r = store.check()
    assert not r["ok"] and r["bad_seq"] == 1 and "edited" in r["reason"]


def test_status_edit_in_db_detected(store):
    store.add("fact", "claim")
    body = json.loads(_rows(store)[0][1]); body["status"] = "verified"
    store.conn.execute("UPDATE events SET body=? WHERE seq=1", (json.dumps(body, sort_keys=True),))
    assert not store.check()["ok"]


def test_deletion_detected(store):
    for i in range(3):
        store.add("note", f"n{i}")
    store.conn.execute("DELETE FROM events WHERE seq=2")
    r = store.check()
    assert not r["ok"] and "gap" in r["reason"]


def test_rehash_without_key_detected(store):
    from gridmem.store import event_hash
    store.add("fact", "a")
    ev = store.events()[0]
    ev["body"]["text"] = "forged"
    h = event_hash(ev["prev_hash"], ev)
    store.conn.execute("UPDATE events SET body=?, entry_hash=? WHERE seq=1",
                       (json.dumps(ev["body"], sort_keys=True), h))
    r = store.check()
    assert not r["ok"] and "signature" in r["reason"]


def test_foreign_key_signature_rejected(tmp_path, store):
    store.add("fact", "a")
    other = Store(tmp_path / "other")
    other.add("fact", "a")
    sig = other.events()[0]["signature"]
    other.close()
    store.conn.execute("UPDATE events SET signature=? WHERE seq=1", (sig,))
    assert not store.check()["ok"]


def test_replaced_key_refused(tmp_path):
    home = tmp_path / "h"
    s = Store(home); s.add("note", "x"); s.close()
    for f in (home / "keys").iterdir():
        f.unlink()
    with pytest.raises(GridMemError):
        Store(home)


def test_hybrid_pqc_signing(tmp_path):
    from gridmem.keeper import pqc_available
    if not pqc_available():
        pytest.skip("liboqs not available")
    s = Store(tmp_path / "pq", sig_alg="ed25519+ml-dsa-44")
    s.add("fact", "pq")
    r = s.check()
    assert r["ok"] and r["signature_algorithm"] == "Ed25519+ML-DSA-44"
    s.close()


def test_secret_files_permissions(store):
    import stat
    for f in (store.home / "keys").iterdir():
        if f.suffix == ".key":
            assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.home.stat().st_mode) == 0o700

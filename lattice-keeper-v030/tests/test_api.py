import copy

from .conftest import GOOD_BODY, auth, requires_pqc


async def _tokenize(c):
    r = await c.post("/rwa/tokenize", json=GOOD_BODY, headers=auth())
    assert r.status == 201, await r.text()
    return await r.json()


async def test_tokenize_classical_marks_pqc_unavailable(make_client):
    c = await make_client()
    body = await _tokenize(c)
    assert body["pqc"] == "unavailable"
    assert body["encryption"]["status"] == "unavailable"
    assert body["reference_check"]["status"] == "passed"
    assert body["payload"]["valuation_cad"] == "3100000000.00"
    assert "bitcoin" not in str(body).lower()


async def test_tokenize_then_verify_roundtrip(make_client):
    c = await make_client()
    body = await _tokenize(c)
    r = await c.post(
        "/verify", json={"payload": body["payload"], "signature": body["signature"]}, headers=auth()
    )
    assert (await r.json())["valid"] is True
    tampered = copy.deepcopy(body["payload"])
    tampered["valuation_cad"] = "9999999999.00"
    r = await c.post("/verify", json={"payload": tampered, "signature": body["signature"]}, headers=auth())
    assert (await r.json())["valid"] is False


async def test_get_record_and_audit(make_client):
    c = await make_client()
    body = await _tokenize(c)
    r = await c.get(f"/rwa/{body['token_id']}", headers=auth())
    assert r.status == 200
    rec = await r.json()
    assert rec["payload"] == body["payload"]
    r = await c.get("/audit/verify", headers=auth())
    # entry 0 is the genesis policy (v0.30), entry 1 the tokenize
    assert (await r.json()) == {"valid": True, "entries": 2, "head_hash": (await r.json())["head_hash"]}


async def test_pubkeys_shape(make_client):
    c = await make_client()
    body = await (await c.get("/pubkeys")).json()
    assert body["keys"]["ed25519"]["alg"] == "Ed25519"
    assert body["keys"]["ml_dsa_65"] is None and body["pqc"] == "unavailable"
    assert body["reference_anchor_hash"].startswith("fd0adf90")


@requires_pqc
async def test_tokenize_pqc_end_to_end(make_client, pqc_ring):
    c = await make_client(ring=pqc_ring)
    body = await _tokenize(c)
    assert body["pqc"] == "ml-dsa-65"
    assert body["encryption"]["status"] == "encrypted"
    from lattice_keeper.crypto import canonical_json

    assert pqc_ring.kem_decrypt(body["encryption"]) == canonical_json(body["payload"])
    r = await c.post(
        "/verify", json={"payload": body["payload"], "signature": body["signature"]}, headers=auth()
    )
    assert (await r.json())["ml_dsa_65"] is True
    h = await (await c.get("/health")).json()
    assert h["pqc"] == "ml-dsa-65+ml-kem-768"
    pk = await (await c.get("/pubkeys")).json()
    assert pk["keys"]["ml_kem_768"]["alg"] == "ML-KEM-768"


@requires_pqc
async def test_tokenize_to_recipient_key(make_client, pqc_ring, tmp_path):
    from lattice_keeper.crypto import KeyRing, b64e

    from .conftest import PQC_BACKEND

    recipient = KeyRing.load_or_create(tmp_path / "rcpt", None, require_pqc=True, pqc=PQC_BACKEND)
    c = await make_client(ring=pqc_ring)
    r = await c.post(
        "/rwa/tokenize",
        json={**GOOD_BODY, "recipient_kem_public_key": b64e(recipient.public.ml_kem_768)},
        headers=auth(),
    )
    body = await r.json()
    assert recipient.kem_decrypt(body["encryption"])  # recipient can decrypt
    r = await c.post("/rwa/tokenize", json={**GOOD_BODY, "recipient_kem_public_key": "AAAA"}, headers=auth())
    assert r.status == 422

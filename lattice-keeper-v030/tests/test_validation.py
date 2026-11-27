from lattice_keeper import server

from .conftest import GOOD_BODY, auth


async def test_wrong_content_type(make_client):
    c = await make_client()
    r = await c.post("/rwa/tokenize", data="x", headers={**auth(), "Content-Type": "text/plain"})
    assert r.status == 415


async def test_invalid_json(make_client):
    c = await make_client()
    r = await c.post("/rwa/tokenize", data="{nope", headers={**auth(), "Content-Type": "application/json"})
    assert r.status == 400
    assert (await r.json())["error"] == "invalid_json"


async def test_non_object_json(make_client):
    c = await make_client()
    r = await c.post("/rwa/tokenize", json=[1, 2], headers=auth())
    assert r.status == 400


async def test_unknown_field_and_bad_types(make_client):
    c = await make_client()
    r = await c.post("/rwa/tokenize", json={**GOOD_BODY, "evil": 1}, headers=auth())
    assert r.status == 422
    body = await r.json()
    assert body["error"] == "validation_error" and body["details"]
    r = await c.post("/rwa/tokenize", json={**GOOD_BODY, "valuation_cad": -5}, headers=auth())
    assert r.status == 422
    r = await c.post("/rag_guard", json={"prompt": ""}, headers=auth())
    assert r.status == 422


async def test_body_too_large(make_client):
    c = await make_client()
    big = {"prompt": "a" * 100_000}
    r = await c.post("/rag_guard", json=big, headers=auth())
    assert r.status == 413
    assert (await r.json())["error"] == "payload_too_large"


async def test_reference_mismatch(make_client):
    c = await make_client()
    r = await c.post("/rwa/tokenize", json={**GOOD_BODY, "valuation_cad": 1.0}, headers=auth())
    assert r.status == 403
    body = await r.json()
    assert body["error"] == "reference_check_failed"
    assert "not an audit" in body["reference_check"]["note"]


async def test_internal_error_not_leaked(make_client, monkeypatch):
    c = await make_client()

    async def explode(*a, **k):
        raise RuntimeError("SECRET internal detail /etc/passwd")

    monkeypatch.setattr(server.AuditLog, "verify", explode)
    r = await c.get("/audit/verify", headers={**auth(), "X-Request-ID": "req-12345678"})
    assert r.status == 500
    text = await r.text()
    assert "SECRET" not in text and "passwd" not in text
    body = await r.json()
    assert body == {
        "error": "internal_error",
        "message": "Internal server error",
        "request_id": "req-12345678",
    }


async def test_not_found_is_json(make_client):
    c = await make_client()
    r = await c.get("/rwa/RWA-0000000000000000", headers=auth())
    assert r.status == 404
    assert (await r.json())["error"] == "not_found"

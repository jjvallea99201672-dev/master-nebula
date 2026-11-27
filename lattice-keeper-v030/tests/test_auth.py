from .conftest import API_KEY, auth, make_settings


async def test_health_and_pubkeys_exempt(make_client):
    c = await make_client()
    r = await c.get("/health")
    assert r.status == 200
    body = await r.json()
    assert body["redis"] == "ok" and body["pqc"] == "unavailable"
    assert (await c.get("/pubkeys")).status == 200


async def test_missing_and_wrong_key(make_client):
    c = await make_client()
    assert (await c.post("/verify", json={})).status == 401
    assert (await c.post("/verify", json={}, headers=auth("z" * 40))).status == 401
    # prefix of the real key must not pass
    assert (await c.post("/verify", json={}, headers=auth(API_KEY[:-1]))).status == 401
    r = await c.get("/audit/verify", headers={"Authorization": f"Basic {API_KEY}"})
    assert r.status == 401
    assert r.headers["WWW-Authenticate"].startswith("Bearer")


async def test_bearer_variants_and_x_api_key(make_client):
    c = await make_client()
    for h in (
        {"Authorization": f"Bearer {API_KEY}"},
        {"Authorization": f"bearer   {API_KEY}  "},
        {"X-API-Key": API_KEY},
    ):
        r = await c.get("/audit/verify", headers=h)
        assert r.status == 200, h


async def test_rate_limit(make_client, tmp_path):
    s = make_settings(KEYS_DIR=str(tmp_path / "k2"), RATE_LIMIT_PER_MINUTE=3)
    c = await make_client(settings_=s)
    statuses = [(await c.get("/audit/verify", headers=auth())).status for _ in range(5)]
    assert statuses[:3] == [200, 200, 200]
    assert statuses[3:] == [429, 429]
    r = await c.get("/audit/verify", headers=auth())
    assert "Retry-After" in r.headers
    # health stays reachable
    assert (await c.get("/health")).status == 200


async def test_health_reports_redis_down(make_client, redis_client):
    c = await make_client()

    async def boom():
        raise ConnectionError("down")

    redis_client.ping = boom
    r = await c.get("/health")
    assert r.status == 503
    assert (await r.json())["redis"] == "unavailable"

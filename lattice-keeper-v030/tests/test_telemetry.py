import json

import pytest

from .conftest import API_KEY, auth, make_settings

DEVICE_KEY = "d" * 40


def sample(**over):
    base = {
        "device_id": "lk-24DCC3A1B2C4",
        "fw": "0.29.0",
        "seq": 1,
        "uptime_ms": 5000,
        "sensor_ok": True,
        "temp_c": 23.5,
        "radiant_raw": 2048,
        "radiant_norm": 0.5,
        "pump_duty": 0.4,
        "pump_interlock": "ok",
        "tau": 3.14,
        "beta": 1.0,
        "guardian": {"state": "nominal", "reason": ""},
        "rssi": -60,
    }
    base.update(over)
    return base


@pytest.fixture
async def client(make_client, tmp_path):
    s = make_settings(KEYS_DIR=str(tmp_path / "tk"), DEVICE_API_KEYS=DEVICE_KEY)
    return await make_client(settings_=s)


async def test_device_key_can_post_telemetry(client):
    r = await client.post("/telemetry", json=sample(), headers=auth(DEVICE_KEY))
    assert r.status == 200
    body = await r.json()
    assert body["status"] == "accepted" and body["seq"] == 1 and body["beta_global"] is None


async def test_device_key_is_scoped(client):
    for method, path in (("GET", "/audit/verify"), ("POST", "/rwa/tokenize"), ("GET", "/telemetry/lk-1")):
        r = await client.request(method, path, json={}, headers=auth(DEVICE_KEY))
        assert r.status == 403, path
    r = await client.put("/telemetry/beta_global", json={"beta_global": 2.0}, headers=auth(DEVICE_KEY))
    assert r.status == 403


async def test_telemetry_requires_auth(client):
    assert (await client.post("/telemetry", json=sample())).status == 401
    assert (await client.post("/telemetry", json=sample(), headers=auth("x" * 40))).status == 401


@pytest.mark.parametrize(
    "bad",
    [
        {"temp_c": 200.0},
        {"radiant_raw": 5000},
        {"pump_duty": 1.5},
        {"tau": 7.0},
        {"beta": 0.0},
        {"pump_interlock": "maybe"},
        {"guardian": {"state": "panic"}},
        {"device_id": "bad id!"},
        {"extra_field": 1},
        {"rssi": 10},
    ],
)
async def test_telemetry_validation(client, bad):
    r = await client.post("/telemetry", json=sample(**bad), headers=auth(DEVICE_KEY))
    assert r.status == 422, bad


async def test_nan_rejected(client):
    raw = json.dumps(sample()).replace("23.5", "NaN")
    r = await client.post(
        "/telemetry", data=raw, headers={**auth(DEVICE_KEY), "Content-Type": "application/json"}
    )
    assert r.status == 422


async def test_temp_null_when_sensor_fault(client):
    s = sample(
        sensor_ok=False,
        temp_c=None,
        pump_interlock="sensor_fault",
        pump_duty=0.0,
        guardian={"state": "quarantine", "reason": "sensor_fault"},
    )
    assert (await client.post("/telemetry", json=s, headers=auth(DEVICE_KEY))).status == 200


async def test_legacy_beta_endpoint_gone_and_redis_overrides_ignored(client, redis_client):
    r = await client.put("/telemetry/beta_global", json={"beta_global": 2.5}, headers=auth(API_KEY))
    assert r.status == 410 and (await r.json())["error"] == "gone"
    await redis_client.set("telemetry:beta_global", "2.5")  # a v0.29 override left in Redis
    body = await (await client.post("/telemetry", json=sample(), headers=auth(DEVICE_KEY))).json()
    assert body["beta_global"] is None and body["policy_version"] == 1
    audit = [json.loads(e)["event"] for e in await redis_client.lrange("audit:log", 0, -1)]
    assert any(e["action"] == "policy_rejected" and e["reason"] == "legacy_endpoint" for e in audit)


async def test_env_default_beta(make_client, tmp_path):
    s = make_settings(KEYS_DIR=str(tmp_path / "tb"), DEVICE_API_KEYS=DEVICE_KEY, TELEMETRY_BETA_GLOBAL="1.5")
    c = await make_client(settings_=s)
    body = await (await c.post("/telemetry", json=sample(), headers=auth(DEVICE_KEY))).json()
    assert body["beta_global"] == 1.5


async def test_latest_and_history_and_audit_transitions(client, redis_client):
    for i, st in enumerate(["nominal", "nominal", "quarantine", "quarantine", "nominal"]):
        g = {"state": st, "reason": "overtemp" if st == "quarantine" else ""}
        await client.post("/telemetry", json=sample(seq=i, guardian=g), headers=auth(DEVICE_KEY))
    r = await client.get("/telemetry/lk-24DCC3A1B2C4", headers=auth(API_KEY))
    body = await r.json()
    assert body["latest"]["seq"] == 4
    assert await redis_client.llen("telemetry:log:lk-24DCC3A1B2C4") == 5
    audit = [json.loads(e)["event"] for e in await redis_client.lrange("audit:log", 0, -1)]
    transitions = [(e["from"], e["to"]) for e in audit if e["action"] == "guardian_state"]
    assert transitions == [(None, "nominal"), ("nominal", "quarantine"), ("quarantine", "nominal")]
    v = await (await client.get("/audit/verify", headers=auth(API_KEY))).json()
    assert v["valid"]


def test_device_key_config_rules():
    from lattice_keeper.config import ConfigError

    with pytest.raises(ConfigError):
        make_settings(DEVICE_API_KEYS="short")
    with pytest.raises(ConfigError):
        make_settings(DEVICE_API_KEYS=API_KEY)
    with pytest.raises(ConfigError):
        make_settings(TELEMETRY_BETA_GLOBAL="9")


GENESIS = "fd0adf9005622e103f02115a50314d91f83dc705f1d685b16d45f52ad6b940b2"


def legacy_fields(**over):
    d = {
        "node_id": 1,
        "beta_eff": 1.0345,
        "N_eff": 68.97,
        "deltaS": 12.4,
        "r_h_proxy": 0.0,
        "coherence": 210.0,
        "oil_temp": 28.0,
        "genesis": GENESIS,
    }
    d.update(over)
    return d


async def test_v0284_fields_accepted_and_genesis_match(client):
    r = await client.post(
        "/telemetry",
        json=sample(**legacy_fields()),
        headers={"X-API-Key": DEVICE_KEY, "X-Request-ID": "esp32-1-123456"},
    )
    assert r.status == 200
    assert r.headers["X-Request-ID"] == "esp32-1-123456"
    body = await r.json()
    assert body["genesis_match"] is True
    r = await client.post(
        "/telemetry", json=sample(**legacy_fields(genesis="0" * 64)), headers=auth(DEVICE_KEY)
    )
    assert (await r.json())["genesis_match"] is False


@pytest.mark.parametrize(
    "bad",
    [
        {"deltaS": 101},
        {"coherence": 39},
        {"coherence": 256},
        {"N_eff": -1},
        {"r_h_proxy": 1.5},
        {"genesis": "xyz"},
        {"oil_temp": -127},
        {"node_id": -1},
    ],
)
async def test_v0284_field_validation(client, bad):
    r = await client.post("/telemetry", json=sample(**legacy_fields(**bad)), headers=auth(DEVICE_KEY))
    assert r.status == 422, bad

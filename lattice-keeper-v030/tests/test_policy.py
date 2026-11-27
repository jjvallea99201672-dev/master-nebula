"""v0.30 operator-signed policy: signing, PUT/GET /policy, audit, runtime application."""

import copy
import json

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from lattice_keeper.audit import AuditLog
from lattice_keeper.config import ConfigError
from lattice_keeper.crypto import b64d, b64e, canonical_json, sha256_hex
from lattice_keeper.operator import POLICY_SIG_CONTEXT, OperatorKey
from lattice_keeper.policy import next_proposal
from lattice_keeper.server import create_app

from .conftest import API_KEY, ENV, PQC_BACKEND, FakeRAG, auth, make_settings, requires_pqc

DEVICE_KEY = "d" * 40
DEV = "lk-24DCC3A1B2C4"


def telemetry_sample(**over):
    base = {
        "device_id": DEV,
        "fw": "0.30.0",
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
    }
    base.update(over)
    return base


@pytest.fixture
def operator(tmp_path):
    return OperatorKey.generate(tmp_path / "opkeys", None, pqc=None)


@pytest.fixture
def op_settings(tmp_path, operator):
    return make_settings(
        KEYS_DIR=str(tmp_path / "srvkeys"),
        DEVICE_API_KEYS=DEVICE_KEY,
        OPERATOR_ED25519_PUBKEY=b64e(operator.public.ed25519),
    )


@pytest.fixture
async def client(make_client, op_settings):
    return await make_client(settings_=op_settings)


async def current(c):
    r = await c.get("/policy", headers=auth())
    assert r.status == 200
    return await r.json()


def signed_next(cur_policy, op, mutate=None, **over):
    doc = next_proposal(cur_policy)
    doc.update(over)
    if mutate:
        mutate(doc["values"])
    return {"policy": doc, "signature": op.sign(canonical_json(doc))}


async def put(c, body, key=API_KEY):
    return await c.put("/policy", json=body, headers=auth(key) if key else {})


async def audit_events(redis_client):
    return [json.loads(e)["event"] for e in await redis_client.lrange("audit:log", 0, -1)]


# ---------------------------------------------------------------- genesis / read
async def test_genesis_from_env_and_device_can_read(client, redis_client):
    body = await current(client)
    assert body["version"] == 1 and body["status"] == "genesis_unsigned" and body["signature"] is None
    p = body["policy"]
    assert p["prev_hash"] == "0" * 64
    assert p["values"]["guardian"]["q_deltas_above"] == 75.0
    assert "jailbreak" in p["values"]["rag_guard"]["blocklist"]
    assert sha256_hex(body["canonical"].encode()) == body["policy_sha256"]
    r = await client.get("/policy", headers=auth(DEVICE_KEY))
    assert r.status == 200
    r = await client.get("/policy?view=device", headers=auth(DEVICE_KEY))
    dv = await r.json()
    assert dv["version"] == 1 and dv["ed25519_sig"] is None
    assert (await client.get("/policy")).status == 401
    ev = await audit_events(redis_client)
    assert ev[0]["action"] == "policy_genesis" and ev[0]["policy_sha256"] == body["policy_sha256"]


# ---------------------------------------------------------------- accept
async def test_signed_change_accepted_applied_and_audited(client, operator, redis_client):
    cur = await current(client)

    def mutate(v):
        v["guardian"]["q_deltas_above"] = 55.0
        v["telemetry"]["beta_global"] = 2.0
        v["telemetry"]["beta_global_devices"] = {DEV: 0.7}
        v["rag_guard"]["blocklist"] = [*v["rag_guard"]["blocklist"], "forbidden phrase"]

    body = signed_next(cur["policy"], operator, mutate)
    r = await put(client, body)
    assert r.status == 200, await r.text()
    res = await r.json()
    assert res["version"] == 2 and res["prev_hash"] == cur["policy_sha256"]

    new = await current(client)
    assert new["version"] == 2 and new["status"] == "operator_signed"
    assert new["policy"]["values"]["guardian"]["q_deltas_above"] == 55.0
    assert new["operator_key"]["ed25519_key_id"] == body["signature"]["ed25519"]["key_id"]

    # applied at runtime: beta_global (per-device > global) and the rag_guard blocklist
    t = await (await client.post("/telemetry", json=telemetry_sample(), headers=auth(DEVICE_KEY))).json()
    assert t["beta_global"] == 0.7 and t["policy_version"] == 2 and t["policy_sha256"] == res["policy_sha256"]
    other = telemetry_sample(device_id="lk-OTHER0000001")
    t = await (await client.post("/telemetry", json=other, headers=auth(DEVICE_KEY))).json()
    assert t["beta_global"] == 2.0
    t = await (
        await client.post(
            "/telemetry",
            json=telemetry_sample(seq=2, policy_sha256=res["policy_sha256"], policy_version=2),
            headers=auth(DEVICE_KEY),
        )
    ).json()
    assert t["policy_in_sync"] is True
    g = await (
        await client.post("/rag_guard", json={"prompt": "a Forbidden  Phrase here"}, headers=auth())
    ).json()
    assert g["result"]["allowed"] is False and g["result"]["policy"]["version"] == 2

    ev = await audit_events(redis_client)
    acc = [e for e in ev if e["action"] == "policy_accepted"]
    assert len(acc) == 1 and acc[0]["version"] == 2 and acc[0]["policy_sha256"] == res["policy_sha256"]
    v = await (await client.get("/audit/verify", headers=auth())).json()
    assert v["valid"] is True


async def test_policy_survives_restart(make_client, op_settings, operator, redis_client, classical_ring):
    c = await make_client(settings_=op_settings)
    cur = await current(c)
    assert (await put(c, signed_next(cur["policy"], operator))).status == 200
    app2 = create_app(op_settings, keyring=classical_ring, redis_client=redis_client, rag=FakeRAG())
    from aiohttp.test_utils import TestClient, TestServer

    async with TestClient(TestServer(app2)) as c2:
        assert (await current(c2))["version"] == 2
    assert sum(e["action"] == "policy_genesis" for e in await audit_events(redis_client)) == 1


# ---------------------------------------------------------------- reject
async def test_bad_signatures_rejected(client, operator, tmp_path, classical_ring, redis_client):
    cur = await current(client)
    good = signed_next(cur["policy"], operator)

    tampered = copy.deepcopy(good)
    tampered["policy"]["values"]["guardian"]["temp_max_c"] = 79.0
    r = await put(client, tampered)
    assert r.status == 403 and (await r.json())["error"] == "signature_invalid"

    impostor = OperatorKey.generate(tmp_path / "impostor", None, pqc=None)
    r = await put(client, signed_next(cur["policy"], impostor))
    assert r.status == 403

    # the server's own key (different context, different key) can't sign policy
    doc = next_proposal(cur["policy"])
    r = await put(client, {"policy": doc, "signature": classical_ring.sign(canonical_json(doc))})
    assert r.status == 403

    # an operator signature made under the server context is not accepted either
    from lattice_keeper.crypto import SIG_CONTEXT

    wrong_ctx = copy.deepcopy(good)
    msg = SIG_CONTEXT + sha256_hex(canonical_json(good["policy"])).encode()
    wrong_ctx["signature"]["ed25519"]["sig"] = b64e(operator._ed.sign(msg))
    assert (await put(client, wrong_ctx)).status == 403

    assert (await current(client))["version"] == 1
    ev = await audit_events(redis_client)
    assert sum(e["action"] == "policy_rejected" and e["reason"] == "signature_invalid" for e in ev) == 4
    assert (await (await client.get("/audit/verify", headers=auth())).json())["valid"]


async def test_replay_and_old_version_rejected(client, operator, redis_client):
    cur = await current(client)
    v2 = signed_next(cur["policy"], operator)
    assert (await put(client, v2)).status == 200
    r = await put(client, v2)  # exact replay
    assert r.status == 409 and (await r.json())["error"] == "stale_version"
    # a validly signed v1 (rollback attempt)
    v1 = copy.deepcopy(cur["policy"])
    r = await put(client, {"policy": v1, "signature": operator.sign(canonical_json(v1))})
    assert r.status == 409 and (await r.json())["error"] == "stale_version"
    # skipping ahead is rejected too
    cur2 = (await current(client))["policy"]
    gap = signed_next(cur2, operator, version=cur2["version"] + 2)
    r = await put(client, gap)
    assert r.status == 409 and (await r.json())["error"] == "version_gap"
    reasons = [e["reason"] for e in await audit_events(redis_client) if e["action"] == "policy_rejected"]
    assert reasons == ["stale_version", "stale_version", "version_gap"]


async def test_wrong_prev_hash_rejected(client, operator):
    cur = await current(client)
    body = signed_next(cur["policy"], operator, prev_hash="ab" * 32)
    r = await put(client, body)
    assert r.status == 409 and (await r.json())["error"] == "prev_hash_mismatch"


async def test_unauthenticated_and_device_key_rejected_and_audited(client, operator, redis_client):
    cur = await current(client)
    body = signed_next(cur["policy"], operator)
    assert (await put(client, body, key=None)).status == 401
    assert (await put(client, body, key="x" * 40)).status == 401
    assert (await put(client, body, key=DEVICE_KEY)).status == 403
    ev = [e for e in await audit_events(redis_client) if e["action"] == "policy_rejected"]
    assert [e["reason"] for e in ev] == ["unauthenticated", "unauthenticated", "forbidden_device_key"]
    assert (await current(client))["version"] == 1


async def test_unauthenticated_audit_is_capped(client, operator, redis_client):
    for _ in range(15):
        await put(client, {"policy": {}, "signature": {}}, key=None)
    ev = [e for e in await audit_events(redis_client) if e["action"] == "policy_rejected"]
    assert len(ev) == 10 and ev[-1]["attempts_this_minute"] == 10


async def test_invalid_values_future_date_and_malformed(client, operator, redis_client):
    cur = await current(client)

    def too_hot(v):
        v["guardian"]["temp_max_c"] = 200.0

    r = await put(client, signed_next(cur["policy"], operator, too_hot))
    assert r.status == 422 and "temp_max_c" in (await r.json())["message"]

    def beta_out(v):
        v["telemetry"]["beta_max"] = 2.0
        v["telemetry"]["beta_global"] = 3.0

    assert (await put(client, signed_next(cur["policy"], operator, beta_out))).status == 422
    r = await put(client, signed_next(cur["policy"], operator, effective_at="2099-01-01T00:00:00Z"))
    assert r.status == 422 and (await r.json())["error"] == "effective_at_in_future"
    r = await put(client, signed_next(cur["policy"], operator, effective_at="2000-01-01T00:00:00Z"))
    assert r.status == 422 and (await r.json())["error"] == "effective_at_regressed"
    r = await client.put("/policy", data="nope", headers={**auth(), "Content-Type": "application/json"})
    assert r.status == 400
    reasons = [e["reason"] for e in await audit_events(redis_client) if e["action"] == "policy_rejected"]
    assert reasons == [
        "validation_error",
        "validation_error",
        "effective_at_in_future",
        "effective_at_regressed",
        "malformed_body",
    ]


async def test_no_operator_key_rejects_everything(make_client, operator):
    c = await make_client()  # default settings: no operator key pinned
    cur = await current(c)
    r = await put(c, signed_next(cur["policy"], operator))
    assert r.status == 409 and (await r.json())["error"] == "operator_key_not_configured"


# ---------------------------------------------------------------- firmware-equivalent check
async def test_device_view_verifies_like_the_firmware(client, operator):
    cur = await current(client)
    assert (await put(client, signed_next(cur["policy"], operator))).status == 200
    dv = await (await client.get("/policy?view=device", headers=auth(DEVICE_KEY))).json()
    # Exactly what guardian_v030 does: sha256 over the canonical bytes, Ed25519 over context+hex.
    msg = POLICY_SIG_CONTEXT + sha256_hex(dv["canonical"].encode()).encode()
    Ed25519PublicKey.from_public_bytes(operator.public.ed25519).verify(b64d(dv["ed25519_sig"]), msg)
    with pytest.raises(InvalidSignature):
        Ed25519PublicKey.from_public_bytes(operator.public.ed25519).verify(
            b64d(dv["ed25519_sig"]), msg.replace(b"policy", b"sig/v1")
        )
    assert len(json.dumps(dv)) < 3000  # fits the firmware's policy buffer


# ---------------------------------------------------------------- config / startup guards
def test_config_rules(tmp_path, operator):
    with pytest.raises(ConfigError, match="REQUIRE_PQC"):
        make_settings(REQUIRE_PQC="true", OPERATOR_ED25519_PUBKEY=b64e(operator.public.ed25519))
    with pytest.raises(ConfigError):
        make_settings(OPERATOR_ED25519_PUBKEY="bm90LWEta2V5")
    with pytest.raises(ConfigError, match="not both"):
        make_settings(
            OPERATOR_ED25519_PUBKEY=b64e(operator.public.ed25519),
            OPERATOR_PUBKEY_FILE=str(tmp_path / "opkeys" / "operator_pubkey.json"),
        )
    s = make_settings(OPERATOR_PUBKEY_FILE=str(tmp_path / "opkeys" / "operator_pubkey.json"))
    assert s.operator_pubkey.ed25519 == operator.public.ed25519


def test_server_refuses_operator_private_key_or_shared_key(tmp_path, classical_ring, redis_client, operator):
    keys = tmp_path / "srv"
    OperatorKey.generate(keys, None, pqc=None)  # operator private key placed in KEYS_DIR
    s = make_settings(KEYS_DIR=str(keys), OPERATOR_ED25519_PUBKEY=b64e(operator.public.ed25519))
    with pytest.raises(RuntimeError, match="never hold"):
        create_app(s, keyring=classical_ring, redis_client=redis_client, rag=FakeRAG())
    s = make_settings(
        KEYS_DIR=str(tmp_path / "k2"), OPERATOR_ED25519_PUBKEY=b64e(classical_ring.public.ed25519)
    )
    with pytest.raises(RuntimeError, match="different"):
        create_app(s, keyring=classical_ring, redis_client=redis_client, rag=FakeRAG())


# ---------------------------------------------------------------- CLI round trip
async def test_cli_keygen_next_sign_verify_then_accept(make_client, tmp_path, monkeypatch, capsys):
    from lattice_keeper.cli import main

    monkeypatch.setenv("OPERATOR_KEY_PASSPHRASE", "correct horse battery staple")
    kd = tmp_path / "cli-op"
    assert main(["operator-keygen", "--out", str(kd), "--no-pqc"]) == 0
    key_doc = json.loads((kd / "operator_ed25519.key").read_text())
    assert key_doc["encrypted"] is True
    assert main(["keygen", "--operator", "--out", str(kd), "--no-pqc"]) == 3  # refuses to overwrite

    s = make_settings(KEYS_DIR=str(tmp_path / "srv"), OPERATOR_PUBKEY_FILE=str(kd / "operator_pubkey.json"))
    c = await make_client(settings_=s)
    cur_file = tmp_path / "current.json"
    cur_file.write_text(json.dumps(await current(c)))
    prop = tmp_path / "proposal.json"
    assert main(["policy-next", "--current", str(cur_file), "--out", str(prop), "--note", "lower dS"]) == 0
    doc = json.loads(prop.read_text())
    doc["values"]["guardian"]["q_deltas_above"] = 50.0
    prop.write_text(json.dumps(doc))
    signed = tmp_path / "signed.json"
    assert main(["policy-sign", str(prop), "--key-dir", str(kd), "--out", str(signed), "--no-pqc"]) == 0
    assert main(["policy-verify", str(signed), "--pubkey", str(kd / "operator_pubkey.json")]) == 0

    r = await c.put(
        "/policy", data=signed.read_text(), headers={**auth(), "Content-Type": "application/json"}
    )
    assert r.status == 200, await r.text()
    assert (await current(c))["policy"]["values"]["guardian"]["q_deltas_above"] == 50.0

    # invalid proposal: refuses to sign
    doc["values"]["guardian"]["pump_level_base"] = 999
    prop.write_text(json.dumps(doc))
    assert main(["policy-sign", str(prop), "--key-dir", str(kd), "--out", str(signed), "--no-pqc"]) == 2
    monkeypatch.setenv("OPERATOR_KEY_PASSPHRASE", "wrong")
    doc["values"]["guardian"]["pump_level_base"] = 80
    prop.write_text(json.dumps(doc))
    assert main(["policy-sign", str(prop), "--key-dir", str(kd), "--out", str(signed), "--no-pqc"]) == 3


# ---------------------------------------------------------------- hybrid (liboqs)
@requires_pqc
async def test_hybrid_operator_signature(make_client, tmp_path, pqc_ring, redis_client):
    op = OperatorKey.generate(tmp_path / "hyb", None, pqc=PQC_BACKEND)
    assert op.public.hybrid
    s = make_settings(
        KEYS_DIR=str(tmp_path / "srv"),
        REQUIRE_PQC="true",
        OPERATOR_PUBKEY_FILE=str(tmp_path / "hyb" / "operator_pubkey.json"),
    )
    c = await make_client(ring=pqc_ring, settings_=s)
    cur = await current(c)
    good = signed_next(cur["policy"], op)
    assert good["signature"]["alg"] == "hybrid:Ed25519+ML-DSA-65"

    stripped = copy.deepcopy(good)
    stripped["signature"]["ml_dsa_65"] = None
    stripped["signature"]["alg"] = "Ed25519"
    assert (await put(c, stripped)).status == 403  # downgrade by stripping ML-DSA is detected

    bad_pq = copy.deepcopy(good)
    sig = bytearray(b64d(bad_pq["signature"]["ml_dsa_65"]["sig"]))
    sig[10] ^= 1
    bad_pq["signature"]["ml_dsa_65"]["sig"] = b64e(bytes(sig))
    assert (await put(c, bad_pq)).status == 403

    r = await put(c, good)
    assert r.status == 200, await r.text()
    body = await current(c)
    assert body["operator_key"]["alg"] == "hybrid:Ed25519+ML-DSA-65"
    log = AuditLog(redis_client, pqc_ring)
    assert (await log.verify())["valid"]
    # device view stays small: only the Ed25519 half goes to the ESP32
    dv = await (await c.get("/policy?view=device", headers=auth())).json()
    assert "ml_dsa_65" not in dv and len(json.dumps(dv)) < 3000


@requires_pqc
async def test_hybrid_operator_key_needs_liboqs_on_server(tmp_path, classical_ring, redis_client):
    OperatorKey.generate(tmp_path / "hyb2", None, pqc=PQC_BACKEND)
    s = make_settings(
        KEYS_DIR=str(tmp_path / "srv"), OPERATOR_PUBKEY_FILE=str(tmp_path / "hyb2" / "operator_pubkey.json")
    )
    with pytest.raises(RuntimeError, match="liboqs"):
        create_app(s, keyring=classical_ring, redis_client=redis_client, rag=FakeRAG())


_ = ENV  # keep the shared env import explicit


async def test_strict_types_so_server_and_device_agree(client, operator):
    cur = await current(client)

    def as_str(v):
        v["guardian"]["q_deltas_above"] = "60"

    def float_level(v):
        v["guardian"]["pump_level_base"] = 80.0

    def padded(v):
        v["rag_guard"]["blocklist"] = [" padded "]

    for m in (as_str, float_level, padded):
        assert (await put(client, signed_next(cur["policy"], operator, m))).status == 422, m.__name__

    def int_for_float(v):  # an integer where a float is expected is fine (JSON has one number type)
        v["guardian"]["q_deltas_above"] = 60

    r = await put(client, signed_next(cur["policy"], operator, int_for_float))
    assert r.status == 200 and (await r.json())["fits_device_buffer"] is True

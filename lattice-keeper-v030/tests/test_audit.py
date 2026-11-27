import json

from lattice_keeper.audit import AuditLog
from lattice_keeper.crypto import canonical_json, sha256_hex


async def _log(redis_client, ring, n=4):
    log = AuditLog(redis_client, ring)
    for i in range(n):
        await log.append({"action": "test", "i": i})
    return log


async def test_chain_valid(redis_client, classical_ring):
    log = await _log(redis_client, classical_ring)
    res = await log.verify()
    assert res["valid"] and res["entries"] == 4
    first = json.loads(await redis_client.lindex("audit:log", 0))
    second = json.loads(await redis_client.lindex("audit:log", 1))
    assert first["prev_hash"] == "0" * 64 and second["prev_hash"] == first["hash"]


async def test_modified_entry_detected(redis_client, classical_ring):
    log = await _log(redis_client, classical_ring)
    e = json.loads(await redis_client.lindex("audit:log", 1))
    e["event"]["i"] = 99
    await redis_client.lset("audit:log", 1, json.dumps(e))
    res = await log.verify()
    assert not res["valid"] and res["first_bad_seq"] == 1


async def test_rehashed_forgery_detected_by_signature(redis_client, classical_ring):
    log = await _log(redis_client, classical_ring, n=1)
    e = json.loads(await redis_client.lindex("audit:log", 0))
    e["event"]["i"] = 42
    body = {k: e[k] for k in ("seq", "ts", "event", "prev_hash")}
    e["hash"] = sha256_hex(canonical_json(body))  # attacker recomputes hash but can't sign
    await redis_client.lset("audit:log", 0, json.dumps(e))
    await redis_client.set("audit:head", json.dumps({"seq": 0, "hash": e["hash"]}))
    res = await log.verify()
    assert not res["valid"] and res["reason"] == "signature invalid"


async def test_deleted_entry_detected(redis_client, classical_ring):
    log = await _log(redis_client, classical_ring)
    raw = await redis_client.lindex("audit:log", 2)
    await redis_client.lrem("audit:log", 1, raw)
    assert not (await log.verify())["valid"]


async def test_truncated_tail_detected(redis_client, classical_ring):
    log = await _log(redis_client, classical_ring)
    await redis_client.rpop("audit:log")
    res = await log.verify()
    assert not res["valid"] and "head" in res["reason"]


async def test_concurrent_appends_stay_linked(redis_client, classical_ring):
    import asyncio

    log = AuditLog(redis_client, classical_ring)
    await asyncio.gather(*(log.append({"i": i}) for i in range(10)))
    assert (await log.verify())["valid"]

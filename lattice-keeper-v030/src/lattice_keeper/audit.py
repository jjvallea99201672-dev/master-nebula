"""Hash-chained, signed audit log stored in Redis.

Copyright (c) 2026 Jay Vallea. All Rights Reserved.

Each entry is ``{"seq", "ts", "event", "prev_hash"}``. Its ``hash`` is the
SHA-256 of the canonical JSON of those fields, and ``signature`` is a hybrid
signature envelope over that canonical JSON. Changing, deleting, inserting or
reordering an entry breaks the chain or a signature. The current head
(seq + hash) is kept separately so truncation of the tail is detected too,
unless the attacker can also rewrite the head *and* forge a signature.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as redis
from redis.exceptions import WatchError

from .crypto import KeyRing, canonical_json, sha256_hex

GENESIS_PREV = "0" * 64
LOG_KEY = "audit:log"
HEAD_KEY = "audit:head"


class AuditLog:
    def __init__(self, client: redis.Redis, keyring: KeyRing, prefix: str = "") -> None:
        self.redis = client
        self.keys = keyring
        self.log_key = prefix + LOG_KEY
        self.head_key = prefix + HEAD_KEY
        self._lock = asyncio.Lock()

    async def append(self, event: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            for _ in range(20):
                try:
                    async with self.redis.pipeline(transaction=True) as pipe:
                        await pipe.watch(self.head_key)
                        raw_head = await pipe.get(self.head_key)
                        head = json.loads(raw_head) if raw_head else {"seq": -1, "hash": GENESIS_PREV}
                        body = {
                            "seq": head["seq"] + 1,
                            "ts": datetime.now(UTC).isoformat(),
                            "event": event,
                            "prev_hash": head["hash"],
                        }
                        blob = canonical_json(body)
                        entry = {**body, "hash": sha256_hex(blob), "signature": self.keys.sign(blob)}
                        pipe.multi()
                        pipe.rpush(self.log_key, json.dumps(entry, sort_keys=True))
                        pipe.set(self.head_key, json.dumps({"seq": body["seq"], "hash": entry["hash"]}))
                        await pipe.execute()
                        return entry
                except WatchError:
                    continue
            raise RuntimeError("audit append contention: gave up after retries")

    async def verify(self) -> dict[str, Any]:
        raw_entries = await self.redis.lrange(self.log_key, 0, -1)
        raw_head = await self.redis.get(self.head_key)
        prev = GENESIS_PREV
        for i, raw in enumerate(raw_entries):
            try:
                entry = json.loads(raw)
                body = {k: entry[k] for k in ("seq", "ts", "event", "prev_hash")}
            except (ValueError, KeyError, TypeError):
                return {
                    "valid": False,
                    "entries": len(raw_entries),
                    "first_bad_seq": i,
                    "reason": "malformed entry",
                }
            blob = canonical_json(body)
            if body["seq"] != i:
                return self._bad(raw_entries, i, "sequence gap or reorder")
            if body["prev_hash"] != prev:
                return self._bad(raw_entries, i, "prev_hash does not link to previous entry")
            if entry.get("hash") != sha256_hex(blob):
                return self._bad(raw_entries, i, "entry hash mismatch (content modified)")
            if not self.keys.verify(blob, entry.get("signature") or {})["valid"]:
                return self._bad(raw_entries, i, "signature invalid")
            prev = entry["hash"]
        if raw_entries or raw_head:
            head = json.loads(raw_head) if raw_head else None
            if head is None or head.get("seq") != len(raw_entries) - 1 or head.get("hash") != prev:
                return self._bad(
                    raw_entries, len(raw_entries), "head does not match last entry (truncation?)"
                )
        return {"valid": True, "entries": len(raw_entries), "head_hash": prev}

    @staticmethod
    def _bad(entries: list, seq: int, reason: str) -> dict[str, Any]:
        return {"valid": False, "entries": len(entries), "first_bad_seq": seq, "reason": reason}

"""Device telemetry storage.

Copyright (c) 2026 Jay Vallea. All Rights Reserved.

Redis keys:
  telemetry:last:{device}       hash  latest sample (JSON) + received_at
  telemetry:log:{device}        list  recent samples, capped at TELEMETRY_HISTORY

Since v0.30 ``beta_global`` is part of the operator-signed policy (see policy.py):
precedence is policy.telemetry.beta_global_devices[device] > policy.telemetry.beta_global
> none. The v0.29 Redis keys ``telemetry:beta_global[:{dev}]`` are no longer read.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as redis

from .config import BETA_MAX, BETA_MIN


def clamp_beta(value: float) -> float:
    return max(BETA_MIN, min(BETA_MAX, value))


class TelemetryStore:
    def __init__(self, client: redis.Redis, history: int) -> None:
        self.redis = client
        self.history = history

    async def record(self, sample: dict[str, Any]) -> tuple[str | None, str]:
        """Store a sample. Returns (previous_guardian_state, received_at)."""
        dev = sample["device_id"]
        received_at = datetime.now(UTC).isoformat()
        prev_raw = await self.redis.hget(f"telemetry:last:{dev}", "sample")
        prev_state = json.loads(prev_raw)["guardian"]["state"] if prev_raw else None
        blob = json.dumps({**sample, "received_at": received_at}, sort_keys=True)
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.hset(f"telemetry:last:{dev}", mapping={"sample": blob, "received_at": received_at})
            pipe.lpush(f"telemetry:log:{dev}", blob)
            pipe.ltrim(f"telemetry:log:{dev}", 0, self.history - 1)
            await pipe.execute()
        return prev_state, received_at

    async def latest(self, device_id: str) -> dict[str, Any] | None:
        raw = await self.redis.hget(f"telemetry:last:{device_id}", "sample")
        return json.loads(raw) if raw else None

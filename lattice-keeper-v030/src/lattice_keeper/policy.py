"""Versioned, operator-signed policy (rules) document.

Copyright (c) 2026 Jay Vallea. All Rights Reserved.

A policy is a canonical JSON document::

    {"format": "lattice-keeper-policy/v1", "version": N, "prev_hash": <sha256 of policy N-1>,
     "effective_at": "<ISO-8601 UTC>", "note": "...", "values": {...}}

``policy_sha256 = sha256(canonical_json(policy))``. Policy v1 (the *genesis*
policy) is built from the server environment the first time the server starts
with an empty store; it is unsigned and its ``prev_hash`` is 64 zeros. Every
later version must be signed by the operator key (see ``operator.py``), must
have ``version == current.version + 1`` and ``prev_hash == current policy_sha256``.
That makes the accepted policies a hash chain, like the audit log.

Bounds below are hard limits enforced on the server *and* on the device: a
validly signed policy still cannot set values outside them, and no policy can
switch off the firmware's sensor-fault / stale-sensor interlock.
"""

from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

import redis.asyncio as redis
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator
from redis.exceptions import WatchError

from .config import BETA_MAX, BETA_MIN, Settings
from .crypto import OQSBackend, canonical_json, sha256_hex
from .guardian import KeywordGuardian
from .operator import OperatorPublicKey

POLICY_FORMAT = "lattice-keeper-policy/v1"
GENESIS_PREV_HASH = "0" * 64
CURRENT_KEY = "policy:current"
HISTORY_KEY = "policy:history"
MAX_DEVICE_OVERRIDES = 64
# GET /policy?view=device must fit the firmware's response buffer (guardian_v030 POLICY_MAX_BYTES).
DEVICE_VIEW_MAX_BYTES = 3584

# Firmware v0.29 compiled-in defaults (firmware/guardian_v029/config.h). Used for genesis.
GUARDIAN_DEFAULTS: dict[str, Any] = {
    "temp_max_c": 60.0,
    "temp_hyst_c": 5.0,
    "q_deltas_above": 75.0,
    "q_beta_above": 3.0,
    "q_coherence_below": 90.0,
    "pump_level_base": 80,
    "pump_level_c150": 130,
    "pump_level_c200": 190,
    "pump_level_deltas": 220,
    "pump_deltas_above": 70.0,
    "beta_default": 1.5,
}

DeviceId = Annotated[str, StringConstraints(min_length=3, max_length=48, pattern=r"^[A-Za-z0-9_.:-]+$")]
Hex64 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Beta = Annotated[float, Field(ge=BETA_MIN, le=BETA_MAX, allow_inf_nan=False)]
Level = Annotated[int, Field(ge=0, le=255)]
Pct = Annotated[float, Field(ge=0.0, le=100.0, allow_inf_nan=False)]
Term = Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=r"^\S(.*\S)?$")]


class GuardianPolicy(BaseModel):
    """Device-side guardian thresholds (applied by the ESP32 firmware)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    temp_max_c: Annotated[float, Field(ge=30.0, le=80.0, allow_inf_nan=False)]
    temp_hyst_c: Annotated[float, Field(ge=1.0, le=20.0, allow_inf_nan=False)]
    q_deltas_above: Pct
    q_beta_above: Beta
    q_coherence_below: Annotated[float, Field(ge=40.0, le=255.0, allow_inf_nan=False)]
    pump_level_base: Level
    pump_level_c150: Level
    pump_level_c200: Level
    pump_level_deltas: Level
    pump_deltas_above: Pct
    beta_default: Beta


class TelemetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    beta_min: Beta = BETA_MIN
    beta_max: Beta = BETA_MAX
    beta_global: Beta | None = None
    beta_global_devices: dict[DeviceId, Beta] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _ranges(self) -> TelemetryPolicy:
        if self.beta_min > self.beta_max:
            raise ValueError("beta_min must be <= beta_max")
        if len(self.beta_global_devices) > MAX_DEVICE_OVERRIDES:
            raise ValueError(f"at most {MAX_DEVICE_OVERRIDES} per-device beta overrides")
        vals = [self.beta_global, *self.beta_global_devices.values()]
        if any(v is not None and not self.beta_min <= v <= self.beta_max for v in vals):
            raise ValueError("beta_global values must lie within [beta_min, beta_max]")
        return self


class RagGuardPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    blocklist: Annotated[list[Term], Field(min_length=0, max_length=200)]


class PolicyValues(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    guardian: GuardianPolicy
    telemetry: TelemetryPolicy
    rag_guard: RagGuardPolicy


class PolicyDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    format: Literal["lattice-keeper-policy/v1"]
    version: Annotated[int, Field(ge=1, le=2**31 - 1)]
    prev_hash: Hex64
    effective_at: Annotated[str, StringConstraints(max_length=40)]
    note: Annotated[str, StringConstraints(max_length=200)] = ""
    values: PolicyValues

    @model_validator(mode="after")
    def _ts(self) -> PolicyDocument:
        self.effective_dt()
        return self

    def effective_dt(self) -> datetime:
        try:
            dt = datetime.fromisoformat(self.effective_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("effective_at must be an ISO-8601 timestamp") from exc
        if dt.tzinfo is None:
            raise ValueError("effective_at must include a timezone (use Z for UTC)")
        return dt.astimezone(UTC)


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def policy_hash(doc: dict[str, Any]) -> str:
    return sha256_hex(canonical_json(doc))


def genesis_document(settings: Settings) -> dict[str, Any]:
    """Policy v1 built from the server environment (unsigned)."""
    return {
        "format": POLICY_FORMAT,
        "version": 1,
        "prev_hash": GENESIS_PREV_HASH,
        "effective_at": utc_now_iso(),
        "note": "genesis: derived from server environment and firmware v0.29 defaults (unsigned)",
        "values": {
            "guardian": dict(GUARDIAN_DEFAULTS),
            "telemetry": {
                "beta_min": BETA_MIN,
                "beta_max": BETA_MAX,
                "beta_global": settings.telemetry_beta_global,
                "beta_global_devices": {},
            },
            "rag_guard": {"blocklist": list(settings.guardian_blocklist)},
        },
    }


def next_proposal(current: dict[str, Any], note: str = "") -> dict[str, Any]:
    """A draft of version N+1 linked to ``current`` (edit ``values``, then sign)."""
    return {
        **copy.deepcopy(current),
        "version": int(current["version"]) + 1,
        "prev_hash": policy_hash(current),
        "effective_at": utc_now_iso(),
        "note": note,
    }


@dataclass
class ActivePolicy:
    doc: dict[str, Any]
    model: PolicyDocument
    canonical: str
    sha256: str
    signature: dict[str, Any] | None
    source: Literal["genesis", "operator"]
    accepted_at: str
    _guardian: KeywordGuardian | None = field(default=None, repr=False)

    @property
    def version(self) -> int:
        return self.model.version

    @property
    def guardian(self) -> KeywordGuardian:
        if self._guardian is None:
            self._guardian = KeywordGuardian(self.model.values.rag_guard.blocklist)
        return self._guardian

    def beta_for(self, device_id: str) -> float | None:
        t = self.model.values.telemetry
        v = t.beta_global_devices.get(device_id, t.beta_global)
        return None if v is None else max(t.beta_min, min(t.beta_max, v))

    def to_record(self) -> str:
        return json.dumps(
            {
                "canonical": self.canonical,
                "sha256": self.sha256,
                "signature": self.signature,
                "source": self.source,
                "accepted_at": self.accepted_at,
            },
            sort_keys=True,
        )

    @classmethod
    def from_doc(
        cls, doc: dict[str, Any], signature: dict[str, Any] | None, source: str, accepted_at: str
    ) -> ActivePolicy:
        canonical = canonical_json(doc).decode()
        return cls(
            doc=doc,
            model=PolicyDocument.model_validate(doc),
            canonical=canonical,
            sha256=sha256_hex(canonical.encode()),
            signature=signature,
            source=source,  # type: ignore[arg-type]
            accepted_at=accepted_at,
        )

    @classmethod
    def from_record(cls, raw: str) -> ActivePolicy:
        rec = json.loads(raw)
        doc = json.loads(rec["canonical"])
        ap = cls.from_doc(doc, rec.get("signature"), rec["source"], rec["accepted_at"])
        if ap.sha256 != rec["sha256"]:
            raise ValueError("stored policy hash mismatch")
        return ap

    def public_view(self, operator: OperatorPublicKey | None) -> dict[str, Any]:
        return {
            "status": "operator_signed" if self.source == "operator" else "genesis_unsigned",
            "version": self.version,
            "policy_sha256": self.sha256,
            "policy": self.doc,
            "canonical": self.canonical,
            "signature": self.signature,
            "source": self.source,
            "accepted_at": self.accepted_at,
            "operator_key": None if operator is None else operator.summary(),
        }

    def device_view(self) -> dict[str, Any]:
        """Compact form for the ESP32: the exact signed bytes plus the Ed25519 part only."""
        ed = (self.signature or {}).get("ed25519") or {}
        return {
            "version": self.version,
            "policy_sha256": self.sha256,
            "source": self.source,
            "canonical": self.canonical,
            "ed25519_key_id": ed.get("key_id"),
            "ed25519_sig": ed.get("sig"),
        }


class PolicyRejectedError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


Rejected = PolicyRejectedError  # short alias used below


class PolicyStore:
    def __init__(self, client: redis.Redis, settings: Settings) -> None:
        self.redis = client
        self.settings = settings
        self._cache: ActivePolicy | None = None
        self._cache_raw: str | None = None
        self._lock = asyncio.Lock()

    async def ensure_genesis(self) -> tuple[ActivePolicy, bool]:
        """Create policy v1 from the environment if no policy exists. Returns (policy, created)."""
        raw = await self.redis.get(CURRENT_KEY)
        if raw is not None:
            return self._load(raw), False
        ap = ActivePolicy.from_doc(genesis_document(self.settings), None, "genesis", utc_now_iso())
        rec = ap.to_record()
        if await self.redis.set(CURRENT_KEY, rec, nx=True):
            await self.redis.rpush(HISTORY_KEY, rec)
            self._cache, self._cache_raw = ap, rec
            return ap, True
        return self._load(await self.redis.get(CURRENT_KEY)), False  # lost a race; use the winner

    def _load(self, raw: str) -> ActivePolicy:
        if self._cache is not None and self._cache_raw == raw:
            return self._cache
        self._cache, self._cache_raw = ActivePolicy.from_record(raw), raw
        return self._cache

    async def current(self) -> ActivePolicy:
        raw = await self.redis.get(CURRENT_KEY)
        if raw is None:
            ap, _ = await self.ensure_genesis()
            return ap
        return self._load(raw)

    async def history_length(self) -> int:
        return int(await self.redis.llen(HISTORY_KEY))

    async def propose(
        self,
        doc: Any,
        signature: Any,
        operator: OperatorPublicKey | None,
        pqc: OQSBackend | None,
    ) -> tuple[ActivePolicy, ActivePolicy]:
        """Validate and atomically install a signed policy. Returns (previous, new).

        Raises PolicyRejectedError with an HTTP status for every failure."""
        if operator is None:
            raise Rejected(
                409, "operator_key_not_configured", "No operator public key is pinned on this server"
            )
        if not isinstance(doc, dict) or not isinstance(signature, dict):
            raise Rejected(422, "validation_error", "Body must be {policy: {...}, signature: {...}}")
        try:
            model = PolicyDocument.model_validate(doc)
        except ValidationError as exc:
            first = exc.errors(include_url=False, include_input=False, include_context=False)[0]
            loc = ".".join(str(p) for p in first["loc"])
            raise Rejected(422, "validation_error", f"policy invalid at {loc}: {first['msg']}") from exc
        payload = canonical_json(doc)
        check = operator.verify(payload, signature, pqc)
        if not check["valid"]:
            raise Rejected(403, "signature_invalid", "Operator signature does not verify")

        async with self._lock:
            for _ in range(10):
                try:
                    async with self.redis.pipeline(transaction=True) as pipe:
                        await pipe.watch(CURRENT_KEY)
                        raw = await pipe.get(CURRENT_KEY)
                        if raw is None:
                            await pipe.reset()
                            await self.ensure_genesis()
                            continue
                        cur = self._load(raw)
                        if model.version <= cur.version:
                            raise Rejected(
                                409,
                                "stale_version",
                                f"version {model.version} is not newer than current {cur.version} "
                                "(replay or rollback)",
                            )
                        if model.version != cur.version + 1:
                            raise Rejected(409, "version_gap", f"version must be exactly {cur.version + 1}")
                        if model.prev_hash != cur.sha256:
                            raise Rejected(
                                409, "prev_hash_mismatch", "prev_hash does not match the current policy hash"
                            )
                        eff = model.effective_dt()
                        now = datetime.now(UTC)
                        skew = timedelta(seconds=self.settings.policy_clock_skew_seconds)
                        if eff > now + skew:
                            raise Rejected(
                                422,
                                "effective_at_in_future",
                                "future-dated policies are not supported; effective_at must be <= now",
                            )
                        if eff < cur.model.effective_dt():
                            raise Rejected(
                                422, "effective_at_regressed", "effective_at is earlier than current policy"
                            )
                        new = ActivePolicy.from_doc(doc, signature, "operator", utc_now_iso())
                        rec = new.to_record()
                        pipe.multi()
                        pipe.set(CURRENT_KEY, rec)
                        pipe.rpush(HISTORY_KEY, rec)
                        await pipe.execute()
                        self._cache, self._cache_raw = new, rec
                        return cur, new
                except WatchError:
                    continue
            raise Rejected(503, "contention", "Policy store busy; retry")

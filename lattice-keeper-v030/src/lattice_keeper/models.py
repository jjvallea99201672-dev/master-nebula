"""Request models (pydantic v2).

Copyright (c) 2026 Jay Vallea. All Rights Reserved.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

Scalar = str | int | float | bool | None
ShortKey = Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")]


def _limit_scalars(v: dict[str, Any], max_items: int, max_str: int) -> dict[str, Any]:
    if len(v) > max_items:
        raise ValueError(f"at most {max_items} entries allowed")
    for val in v.values():
        if isinstance(val, str) and len(val) > max_str:
            raise ValueError(f"string values must be at most {max_str} characters")
    return v


class TokenizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    control_variable: Annotated[str, StringConstraints(min_length=1, max_length=256)] | None = None
    report_id: (
        Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._:-]+$")] | None
    ) = None
    valuation_cad: (
        Annotated[Decimal, Field(ge=0, le=Decimal("1e15"), max_digits=20, decimal_places=2)] | None
    ) = None
    attributes: dict[ShortKey, Scalar] = Field(default_factory=dict)
    recipient_kem_public_key: Annotated[str, StringConstraints(max_length=4096)] | None = Field(
        default=None, description="Optional base64 ML-KEM-768 public key to encrypt the record to."
    )

    @field_validator("attributes")
    @classmethod
    def _attrs(cls, v: dict[str, Any]) -> dict[str, Any]:
        return _limit_scalars(v, 50, 1024)


class VerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload: dict[str, Any] = Field(description="The exact signed object returned by the server.")
    signature: dict[str, Any]


class RagGuardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    prompt: Annotated[str, StringConstraints(min_length=1, max_length=8000)]
    metadata: dict[ShortKey, Scalar] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def _meta(cls, v: dict[str, Any]) -> dict[str, Any]:
        return _limit_scalars(v, 20, 512)


# ------------------------------------------------------------- telemetry
DeviceId = Annotated[str, StringConstraints(min_length=3, max_length=48, pattern=r"^[A-Za-z0-9_.:-]+$")]


class GuardianState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["nominal", "quarantine"]
    reason: Annotated[str, StringConstraints(max_length=64, pattern=r"^[a-zA-Z0-9_,]*$")] = ""


class TelemetryRequest(BaseModel):
    """One sample from a Guardian device. Values are sensor readings and
    sensor-derived proxies computed on the device, not physical measurements
    beyond what the sensors report."""

    model_config = ConfigDict(extra="forbid")

    device_id: DeviceId
    fw: Annotated[str, StringConstraints(max_length=32)] = ""
    seq: Annotated[int, Field(ge=0, le=2**32 - 1)]
    uptime_ms: Annotated[int, Field(ge=0, le=2**64 - 1)]
    sensor_ok: bool
    temp_c: Annotated[float, Field(ge=-55.0, le=125.0, allow_inf_nan=False)] | None = None
    radiant_raw: Annotated[int, Field(ge=0, le=4095)]
    radiant_norm: Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
    pump_duty: Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
    pump_interlock: Literal["ok", "sensor_fault", "stale", "overtemp", "quarantine", "rearming"]
    tau: Annotated[float, Field(ge=0.0, le=6.2832, allow_inf_nan=False)]
    beta: Annotated[float, Field(ge=0.1, le=5.0, allow_inf_nan=False)]
    guardian: GuardianState
    rssi: Annotated[int, Field(ge=-127, le=0)] | None = None

    # Fields carried over from the v0.28.4 payload (all sensor-derived proxies).
    node_id: Annotated[int, Field(ge=0, le=65535)] | None = None
    beta_eff: Annotated[float, Field(ge=0.0, le=5.0, allow_inf_nan=False)] | None = None
    N_eff: Annotated[float, Field(ge=0.0, le=100.0, allow_inf_nan=False)] | None = None  # noqa: N815
    deltaS: Annotated[float, Field(ge=0.0, le=100.0, allow_inf_nan=False)] | None = None  # noqa: N815
    r_h_proxy: Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)] | None = None
    coherence: Annotated[float, Field(ge=40.0, le=255.0, allow_inf_nan=False)] | None = None
    oil_temp: Annotated[float, Field(ge=-55.0, le=125.0, allow_inf_nan=False)] | None = None
    genesis: Annotated[str, StringConstraints(pattern=r"^[0-9a-fA-F]{64}$")] | None = None

    # v0.30: the policy the device is currently applying (informational).
    policy_version: Annotated[int, Field(ge=0, le=2**31 - 1)] | None = None
    policy_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")] | None = None


class PolicyUpdateRequest(BaseModel):
    """Body of PUT /policy: the policy document exactly as signed, plus the operator envelope."""

    model_config = ConfigDict(extra="forbid")

    policy: dict[str, Any]
    signature: dict[str, Any]

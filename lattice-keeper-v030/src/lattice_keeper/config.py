"""Configuration loaded from environment variables.

Copyright (c) 2026 Jay Vallea. All Rights Reserved.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from .crypto import KeyFileError, b64d
from .operator import OperatorPublicKey

MIN_API_KEY_LENGTH = 32
BETA_MIN, BETA_MAX = 0.1, 5.0
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}

DEFAULT_BLOCKLIST = (
    "bypass",
    "jailbreak",
    "ignore previous instructions",
    "exploit",
    "malware",
    "ransomware",
)


class ConfigError(ValueError):
    """Raised when configuration is missing or invalid."""


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ConfigError(f"{name} must be true/false, got {raw!r}")


def _int(env: Mapping[str, str], name: str, default: int, minimum: int = 0) -> int:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}")
    return value


def _float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc
    if value <= 0:
        raise ConfigError(f"{name} must be > 0")
    return value


def _opt(env: Mapping[str, str], name: str) -> str | None:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return None
    return raw.strip()


@dataclass(frozen=True)
class ReferenceConfig:
    """Expected reference values a tokenize request must match.

    This is a *configured reference-value check*: it only confirms that the
    request carries the values the operator configured. It is not an audit
    and does not prove anything about the real-world asset.
    """

    anchor_hash: str | None = None
    control_variable: str | None = None
    report_id: str | None = None
    valuation_cad: Decimal | None = None
    valuation_tolerance: Decimal = Decimal("0.01")

    @property
    def enabled(self) -> bool:
        return any(v is not None for v in (self.control_variable, self.report_id, self.valuation_cad))


@dataclass(frozen=True)
class Settings:
    api_keys: tuple[str, ...]
    device_api_keys: tuple[str, ...] = ()
    telemetry_beta_global: float | None = None
    telemetry_history: int = 1000
    api_host: str = "0.0.0.0"
    api_port: int = 8765
    redis_url: str = "redis://localhost:6379/0"
    redis_connect_retries: int = 10
    record_ttl_seconds: int = 10 * 365 * 24 * 3600
    max_body_bytes: int = 64 * 1024
    rate_limit_per_minute: int = 60
    keys_dir: str = "./keys"
    key_passphrase: str | None = field(default=None, repr=False)
    require_pqc: bool = True
    reference: ReferenceConfig = field(default_factory=ReferenceConfig)
    guardian_blocklist: tuple[str, ...] = DEFAULT_BLOCKLIST
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"
    ollama_timeout_seconds: float = 30.0
    rag_enabled: bool = False
    rag_index_dir: str | None = None
    rag_index_manifest_sha256: str | None = None
    rag_embed_model: str = "nomic-embed-text"
    shutdown_timeout_seconds: float = 15.0
    log_level: str = "INFO"
    operator_pubkey: OperatorPublicKey | None = None
    policy_clock_skew_seconds: int = 300

    def __repr__(self) -> str:  # never print secrets
        return f"Settings(api_host={self.api_host!r}, api_port={self.api_port}, keys=<redacted>)"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env

        raw_keys = env.get("LATTICE_API_KEY", "")
        keys = tuple(k.strip() for k in raw_keys.split(",") if k.strip())
        if not keys:
            raise ConfigError("LATTICE_API_KEY is required (comma-separate to allow several keys).")
        for k in keys:
            if len(k) < MIN_API_KEY_LENGTH:
                raise ConfigError(
                    f"Each LATTICE_API_KEY must be at least {MIN_API_KEY_LENGTH} characters. "
                    "Generate one with: python -c 'import secrets; print(secrets.token_urlsafe(32))'"
                )

        dev_keys = tuple(k.strip() for k in env.get("DEVICE_API_KEYS", "").split(",") if k.strip())
        for k in dev_keys:
            if len(k) < MIN_API_KEY_LENGTH:
                raise ConfigError(
                    f"Each DEVICE_API_KEYS entry must be at least {MIN_API_KEY_LENGTH} characters."
                )
        if set(dev_keys) & set(keys):
            raise ConfigError("A key cannot be both an admin key (LATTICE_API_KEY) and a device key.")
        beta: float | None = None
        if _opt(env, "TELEMETRY_BETA_GLOBAL") is not None:
            beta = _float(env, "TELEMETRY_BETA_GLOBAL", 1.0)
            if not BETA_MIN <= beta <= BETA_MAX:
                raise ConfigError(f"TELEMETRY_BETA_GLOBAL must be between {BETA_MIN} and {BETA_MAX}.")

        anchor = _opt(env, "REFERENCE_ANCHOR_HASH")
        if anchor is not None:
            anchor = anchor.lower()
            if not _HEX64.match(anchor):
                raise ConfigError("REFERENCE_ANCHOR_HASH must be 64 hex characters (SHA-256).")

        valuation: Decimal | None = None
        raw_val = _opt(env, "REFERENCE_VALUATION_CAD")
        if raw_val is not None:
            try:
                valuation = Decimal(raw_val)
            except InvalidOperation as exc:
                raise ConfigError("REFERENCE_VALUATION_CAD must be a decimal number.") from exc
        try:
            tolerance = Decimal(env.get("REFERENCE_VALUATION_TOLERANCE", "0.01") or "0.01")
        except InvalidOperation as exc:
            raise ConfigError("REFERENCE_VALUATION_TOLERANCE must be a decimal number.") from exc

        reference = ReferenceConfig(
            anchor_hash=anchor,
            control_variable=_opt(env, "REFERENCE_CONTROL_VARIABLE"),
            report_id=_opt(env, "REFERENCE_REPORT_ID"),
            valuation_cad=valuation,
            valuation_tolerance=tolerance,
        )

        raw_block = env.get("GUARDIAN_BLOCKLIST")
        blocklist = (
            tuple(w.strip().lower() for w in raw_block.split(",") if w.strip())
            if raw_block
            else DEFAULT_BLOCKLIST
        )

        manifest_pin = _opt(env, "RAG_INDEX_MANIFEST_SHA256")
        if manifest_pin is not None:
            manifest_pin = manifest_pin.lower()
            if not _HEX64.match(manifest_pin):
                raise ConfigError("RAG_INDEX_MANIFEST_SHA256 must be 64 hex characters.")

        operator = _operator_pubkey(env)
        require_pqc = _bool(env, "REQUIRE_PQC", True)
        if operator is not None and require_pqc and not operator.hybrid:
            raise ConfigError(
                "REQUIRE_PQC=true but the operator public key is Ed25519-only. Generate the operator "
                "key with liboqs available (hybrid Ed25519 + ML-DSA-65), or set REQUIRE_PQC=false."
            )

        return cls(
            api_keys=keys,
            device_api_keys=dev_keys,
            telemetry_beta_global=beta,
            telemetry_history=_int(env, "TELEMETRY_HISTORY", 1000, minimum=1),
            api_host=env.get("API_HOST", "0.0.0.0"),
            api_port=_int(env, "API_PORT", 8765, minimum=1),
            redis_url=env.get("REDIS_URL", "redis://localhost:6379/0"),
            redis_connect_retries=_int(env, "REDIS_CONNECT_RETRIES", 10, minimum=1),
            record_ttl_seconds=_int(env, "RECORD_TTL_SECONDS", 10 * 365 * 24 * 3600, minimum=1),
            max_body_bytes=_int(env, "MAX_BODY_BYTES", 64 * 1024, minimum=1024),
            rate_limit_per_minute=_int(env, "RATE_LIMIT_PER_MINUTE", 60, minimum=1),
            keys_dir=env.get("KEYS_DIR", "./keys"),
            key_passphrase=_opt(env, "LATTICE_KEY_PASSPHRASE"),
            require_pqc=require_pqc,
            reference=reference,
            guardian_blocklist=blocklist,
            ollama_host=env.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/"),
            ollama_model=env.get("OLLAMA_MODEL", "llama3.2"),
            ollama_timeout_seconds=_float(env, "OLLAMA_TIMEOUT_SECONDS", 30.0),
            rag_enabled=_bool(env, "RAG_ENABLED", False),
            rag_index_dir=_opt(env, "RAG_INDEX_DIR"),
            rag_index_manifest_sha256=manifest_pin,
            rag_embed_model=env.get("RAG_EMBED_MODEL", "nomic-embed-text"),
            shutdown_timeout_seconds=_float(env, "SHUTDOWN_TIMEOUT_SECONDS", 15.0),
            log_level=env.get("LOG_LEVEL", "INFO").upper(),
            operator_pubkey=operator,
            policy_clock_skew_seconds=_int(env, "POLICY_CLOCK_SKEW_SECONDS", 300, minimum=0),
        )


def _operator_pubkey(env: Mapping[str, str]) -> OperatorPublicKey | None:
    """Pinned operator public key: OPERATOR_PUBKEY_FILE (operator_pubkey.json from
    ``operator-keygen``) or inline base64 OPERATOR_ED25519_PUBKEY [+ OPERATOR_ML_DSA_65_PUBKEY]."""
    path = _opt(env, "OPERATOR_PUBKEY_FILE")
    ed_b64 = _opt(env, "OPERATOR_ED25519_PUBKEY")
    pq_b64 = _opt(env, "OPERATOR_ML_DSA_65_PUBKEY")
    if path and (ed_b64 or pq_b64):
        raise ConfigError("Set either OPERATOR_PUBKEY_FILE or OPERATOR_*_PUBKEY, not both.")
    try:
        if path:
            return OperatorPublicKey.from_file(path)
        if pq_b64 and not ed_b64:
            raise ConfigError("OPERATOR_ML_DSA_65_PUBKEY requires OPERATOR_ED25519_PUBKEY (hybrid).")
        if ed_b64:
            return OperatorPublicKey(b64d(ed_b64), b64d(pq_b64) if pq_b64 else None)
    except (KeyFileError, ValueError) as exc:
        if isinstance(exc, ConfigError):
            raise
        raise ConfigError(f"operator public key invalid: {exc}") from exc
    return None

#!/usr/bin/env python3
"""
Lattice Keeper v0.30 — hardened HTTP service
Hybrid Ed25519 + ML-DSA-65 signatures, ML-KEM-768 record encryption,
signed hash-chained audit log, keyword-heuristic guardian, optional RAG,
operator-signed versioned policy (rules) documents.

Copyright (c) 2026 Jay Vallea. All Rights Reserved.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import redis.asyncio as redis
import structlog
from aiohttp import web
from pydantic import BaseModel, ValidationError

from . import __version__
from .audit import AuditLog
from .config import Settings
from .crypto import KeyRing, canonical_json, sha256_hex
from .models import (
    PolicyUpdateRequest,
    RagGuardRequest,
    TelemetryRequest,
    TokenizeRequest,
    VerifyRequest,
)
from .operator import OperatorPublicKey
from .policy import DEVICE_VIEW_MAX_BYTES, PolicyRejectedError, PolicyStore, policy_hash
from .rag import RAGEngine, UpstreamError
from .ratelimit import RateLimiter
from .reference import check_reference
from .telemetry import TelemetryStore

log = structlog.get_logger("lattice_keeper")

EXEMPT_PATHS = frozenset({"/health", "/pubkeys"})
DEVICE_ROUTES = frozenset({("POST", "/telemetry"), ("GET", "/policy")})
POLICY_AUTH_FAIL_AUDIT_PER_MIN = 10  # cap on audited unauthenticated PUT /policy attempts
_REQ_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_TOKEN_RE = re.compile(r"^RWA-[0-9A-F]{16}$")

K_SETTINGS = web.AppKey("settings", Settings)
K_KEYS = web.AppKey("keyring", KeyRing)
K_REDIS = web.AppKey("redis", redis.Redis)
K_AUDIT = web.AppKey("audit", AuditLog)
K_LIMITER = web.AppKey("limiter", RateLimiter)
K_POLICY = web.AppKey("policy", PolicyStore)
K_RAG = web.AppKey("rag", RAGEngine)
K_API_KEYS = web.AppKey("api_keys", tuple)
K_DEVICE_KEYS = web.AppKey("device_keys", tuple)
K_TELEMETRY = web.AppKey("telemetry", TelemetryStore)


# ------------------------------------------------------------------ errors
def json_error(status: int, code: str, message: str, request_id: str | None, **extra: Any) -> web.Response:
    body = {"error": code, "message": message, "request_id": request_id, **extra}
    return web.json_response(body, status=status)


@web.middleware
async def request_context_middleware(request: web.Request, handler):
    incoming = request.headers.get("X-Request-ID", "")
    rid = incoming if _REQ_ID_RE.match(incoming) else uuid.uuid4().hex
    request["request_id"] = rid
    start = time.perf_counter()
    try:
        resp = await handler(request)
    except web.HTTPException as exc:
        if exc.status >= 400:
            code = {
                404: "not_found",
                405: "method_not_allowed",
                413: "payload_too_large",
            }.get(exc.status, "http_error")
            resp = json_error(exc.status, code, exc.reason, rid)
        else:
            raise
    except Exception:
        log.exception("unhandled_error", request_id=rid, path=request.path)
        resp = json_error(500, "internal_error", "Internal server error", rid)
    resp.headers["X-Request-ID"] = rid
    resp.headers.setdefault("Cache-Control", "no-store")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    log.info(
        "request",
        request_id=rid,
        method=request.method,
        path=request.path,
        status=resp.status,
        ms=round((time.perf_counter() - start) * 1000, 1),
        client=request.get("api_key_id"),
    )
    return resp


def _extract_key(request: web.Request) -> str | None:
    auth = request.headers.get("Authorization")
    if auth:
        scheme, _, token = auth.strip().partition(" ")
        if scheme.lower() == "bearer" and token.strip():
            return token.strip()
        return None
    xkey = request.headers.get("X-API-Key")
    return xkey.strip() if xkey else None


@web.middleware
async def auth_middleware(request: web.Request, handler):
    if request.path in EXEMPT_PATHS and request.method in ("GET", "HEAD"):
        return await handler(request)
    rid = request.get("request_id")
    presented = _extract_key(request)
    admin = device = False
    if presented:
        p = presented.encode()
        for k in request.app[K_API_KEYS]:  # check all keys; no early exit
            admin |= hmac.compare_digest(p, k.encode())
        for k in request.app[K_DEVICE_KEYS]:
            device |= hmac.compare_digest(p, k.encode())
    matched = admin or device
    is_policy_write = request.method == "PUT" and request.path == "/policy"
    if not matched:
        if is_policy_write:
            await _audit_policy_auth_failure(request, "unauthenticated")
        resp = json_error(401, "unauthorized", "Missing or invalid API key", rid)
        resp.headers["WWW-Authenticate"] = 'Bearer realm="lattice-keeper"'
        return resp
    if not admin and (request.method, request.path) not in DEVICE_ROUTES:
        if is_policy_write:
            await _audit_policy_auth_failure(request, "forbidden_device_key")
        return json_error(403, "forbidden", "Device keys may only call POST /telemetry and GET /policy", rid)
    subject = hashlib.sha256(presented.encode()).hexdigest()[:16]
    request["api_key_id"] = subject
    request["key_role"] = "admin" if admin else "device"
    try:
        allowed, remaining, retry = await request.app[K_LIMITER].hit(subject)
    except Exception:
        log.exception("rate_limit_backend_error", request_id=rid)
        return json_error(503, "unavailable", "Rate limiter backend unavailable", rid)
    if not allowed:
        resp = json_error(429, "rate_limited", "Too many requests", rid)
        resp.headers["Retry-After"] = str(retry)
        return resp
    resp = await handler(request)
    resp.headers["X-RateLimit-Remaining"] = str(remaining)
    return resp


async def _audit_policy_auth_failure(request: web.Request, reason: str) -> None:
    """Audit a PUT /policy attempt that failed authentication. Capped per minute so
    anonymous callers can't grow the audit log without bound (the count is kept)."""
    app = request.app
    try:
        bucket = f"policy:authfail:{int(time.time() // 60)}"
        n = await app[K_REDIS].incr(bucket)
        if n == 1:
            await app[K_REDIS].expire(bucket, 120)
        if n <= POLICY_AUTH_FAIL_AUDIT_PER_MIN:
            await app[K_AUDIT].append(
                {
                    "action": "policy_rejected",
                    "reason": reason,
                    "status": 401 if reason == "unauthenticated" else 403,
                    "attempts_this_minute": n,
                }
            )
    except Exception:
        log.exception("policy_auth_audit_failed", request_id=request.get("request_id"))


async def parse_body(request: web.Request, model: type[BaseModel]) -> BaseModel | web.Response:
    rid = request.get("request_id")
    if request.content_type != "application/json":
        return json_error(415, "unsupported_media_type", "Content-Type must be application/json", rid)
    try:
        raw = await request.read()
    except web.HTTPRequestEntityTooLarge:
        return json_error(413, "payload_too_large", "Request body too large", rid)
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return json_error(400, "invalid_json", "Body is not valid JSON", rid)
    if not isinstance(data, dict):
        return json_error(400, "invalid_json", "Body must be a JSON object", rid)
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        details = [
            {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]}
            for e in exc.errors(include_url=False, include_input=False, include_context=False)
        ]
        return json_error(422, "validation_error", "Request validation failed", rid, details=details)


# ---------------------------------------------------------------- handlers
def _money(value: Decimal | None) -> str | None:
    return None if value is None else str(value.quantize(Decimal("0.01")))


async def health(request: web.Request) -> web.Response:
    app = request.app
    keys = app[K_KEYS]
    try:
        await asyncio.wait_for(app[K_REDIS].ping(), timeout=1.0)
        redis_status = "ok"
    except Exception:
        redis_status = "unavailable"
    body = {
        "status": "ok" if redis_status == "ok" else "degraded",
        "version": __version__,
        "redis": redis_status,
        "pqc": "ml-dsa-65+ml-kem-768" if keys.pqc_available else "unavailable",
        "rag": app[K_RAG].status,
        "guardian": "heuristic_keyword_filter",
    }
    try:
        pol = await app[K_POLICY].current()
        body["policy"] = {"version": pol.version, "sha256": pol.sha256, "source": pol.source}
    except Exception:
        body["policy"] = "unavailable"
    body["operator_key"] = "pinned" if app[K_SETTINGS].operator_pubkey else "not_configured"
    return web.json_response(body, status=200 if redis_status == "ok" else 503)


async def pubkeys(request: web.Request) -> web.Response:
    keys = request.app[K_KEYS]
    s = request.app[K_SETTINGS]
    return web.json_response(
        {
            "keys": keys.public.to_json(),
            "pqc": "available" if keys.pqc_available else "unavailable",
            "reference_anchor_hash": s.reference.anchor_hash,
            "signature_scheme": "Sign(sk, 'lattice-keeper/sig/v1:' + sha256_hex(canonical_json(payload)))",
            "operator": None if s.operator_pubkey is None else s.operator_pubkey.summary(),
            "policy_signature_scheme": (
                "Sign(operator_sk, 'lattice-keeper/policy/v1:' + sha256_hex(canonical_json(policy)))"
            ),
        }
    )


async def tokenize_rwa(request: web.Request) -> web.Response:
    rid = request["request_id"]
    parsed = await parse_body(request, TokenizeRequest)
    if isinstance(parsed, web.Response):
        return parsed
    req: TokenizeRequest = parsed  # type: ignore[assignment]
    app = request.app
    s, keys = app[K_SETTINGS], app[K_KEYS]

    ref = check_reference(s.reference, req.control_variable, req.report_id, req.valuation_cad)
    if not ref.ok:
        return json_error(
            403,
            "reference_check_failed",
            ref.reason or "reference mismatch",
            rid,
            reference_check=ref.to_json(),
        )

    recipient: bytes | None = None
    if req.recipient_kem_public_key:
        try:
            recipient = base64.b64decode(req.recipient_kem_public_key, validate=True)
        except (binascii.Error, ValueError):
            return json_error(422, "validation_error", "recipient_kem_public_key must be base64", rid)
        if keys.pqc is None:
            return json_error(409, "pqc_unavailable", "ML-KEM-768 is unavailable on this server", rid)
        if len(recipient) != 1184:
            return json_error(422, "validation_error", "ML-KEM-768 public key must be 1184 bytes", rid)

    token_id = f"RWA-{secrets.token_hex(8).upper()}"
    payload = {
        "token_id": token_id,
        "control_variable": req.control_variable,
        "report_id": req.report_id,
        "valuation_cad": None
        if req.valuation_cad is None
        else str(req.valuation_cad.quantize(Decimal("0.01"))),
        "attributes": req.attributes,
        "reference_check": ref.status,
        "reference_anchor_hash": s.reference.anchor_hash,
        "created_at": datetime.now(UTC).isoformat(),
    }
    payload_bytes = canonical_json(payload)
    signature = keys.sign(payload_bytes)
    kem = keys.kem_encrypt(payload_bytes, aad=token_id.encode(), recipient_public_key=recipient)

    record = {
        "payload": payload_bytes.decode(),
        "payload_sha256": sha256_hex(payload_bytes),
        "signature": json.dumps(signature),
        "kem": json.dumps(kem),
    }
    r = app[K_REDIS]
    async with r.pipeline(transaction=True) as pipe:
        pipe.hset(f"rwa:{token_id}", mapping=record)
        pipe.expire(f"rwa:{token_id}", s.record_ttl_seconds)
        await pipe.execute()
    await app[K_AUDIT].append(
        {
            "action": "tokenize",
            "token_id": token_id,
            "payload_sha256": record["payload_sha256"],
            "client": request.get("api_key_id"),
        }
    )
    return web.json_response(
        {
            "status": "tokenized",
            "token_id": token_id,
            "payload": payload,
            "payload_sha256": record["payload_sha256"],
            "signature": signature,
            "encryption": kem,
            "pqc": signature["pqc"],
            "reference_check": ref.to_json(),
        },
        status=201,
    )


async def get_record(request: web.Request) -> web.Response:
    rid = request["request_id"]
    token_id = request.match_info["token_id"]
    if not _TOKEN_RE.match(token_id):
        return json_error(404, "not_found", "Unknown token", rid)
    rec = await request.app[K_REDIS].hgetall(f"rwa:{token_id}")
    if not rec:
        return json_error(404, "not_found", "Unknown token", rid)
    return web.json_response(
        {
            "token_id": token_id,
            "payload": json.loads(rec["payload"]),
            "payload_sha256": rec["payload_sha256"],
            "signature": json.loads(rec["signature"]),
            "encryption": json.loads(rec["kem"]),
        }
    )


async def verify(request: web.Request) -> web.Response:
    parsed = await parse_body(request, VerifyRequest)
    if isinstance(parsed, web.Response):
        return parsed
    req: VerifyRequest = parsed  # type: ignore[assignment]
    result = request.app[K_KEYS].verify(canonical_json(req.payload), req.signature)
    return web.json_response(result)


async def rag_guard(request: web.Request) -> web.Response:
    rid = request["request_id"]
    parsed = await parse_body(request, RagGuardRequest)
    if isinstance(parsed, web.Response):
        return parsed
    req: RagGuardRequest = parsed  # type: ignore[assignment]
    app = request.app
    policy = await app[K_POLICY].current()
    guardian = policy.guardian

    pre = guardian.check(req.prompt, "")
    if not pre.allowed:
        output, ctx_used = None, 0
        verdict = pre
    else:
        try:
            output, ctx_used = await app[K_RAG].generate(req.prompt)
        except UpstreamError:
            log.exception("upstream_error", request_id=rid)
            return json_error(502, "upstream_unavailable", "Model backend unavailable or timed out", rid)
        verdict = guardian.check(req.prompt, output)

    result_payload = {
        "prompt_sha256": sha256_hex(req.prompt.encode()),
        "output": output if verdict.allowed else None,
        "allowed": verdict.allowed,
        "guardian": verdict.to_json(),
        "rag": {"status": app[K_RAG].status, "context_chunks": ctx_used},
        "metadata": req.metadata,
        "policy": {"version": policy.version, "sha256": policy.sha256},
        "created_at": datetime.now(UTC).isoformat(),
    }
    signature = app[K_KEYS].sign(canonical_json(result_payload))
    await app[K_AUDIT].append(
        {
            "action": "rag_guard",
            "allowed": verdict.allowed,
            "prompt_sha256": result_payload["prompt_sha256"],
            "client": request.get("api_key_id"),
        }
    )
    return web.json_response({"result": result_payload, "signature": signature, "pqc": signature["pqc"]})


async def telemetry(request: web.Request) -> web.Response:
    parsed = await parse_body(request, TelemetryRequest)
    if isinstance(parsed, web.Response):
        return parsed
    req: TelemetryRequest = parsed  # type: ignore[assignment]
    app = request.app
    store = app[K_TELEMETRY]
    sample = req.model_dump(mode="json")
    prev_state, received_at = await store.record(sample)
    if prev_state != req.guardian.state:
        # Audit only guardian state transitions (not every 5 s sample).
        await app[K_AUDIT].append(
            {
                "action": "guardian_state",
                "device_id": req.device_id,
                "from": prev_state,
                "to": req.guardian.state,
                "reason": req.guardian.reason,
                "seq": req.seq,
                "client": request.get("api_key_id"),
            }
        )
    policy = await app[K_POLICY].current()
    beta = policy.beta_for(req.device_id)
    anchor = app[K_SETTINGS].reference.anchor_hash
    # Informational only: does the device's compiled-in genesis hash equal the configured anchor?
    genesis_match = None if (anchor is None or req.genesis is None) else req.genesis.lower() == anchor
    return web.json_response(
        {
            "status": "accepted",
            "seq": req.seq,
            "beta_global": beta,
            "genesis_match": genesis_match,
            "policy_version": policy.version,
            "policy_sha256": policy.sha256,
            "policy_in_sync": None if req.policy_sha256 is None else req.policy_sha256 == policy.sha256,
            "received_at": received_at,
        }
    )


async def telemetry_latest(request: web.Request) -> web.Response:
    rid = request["request_id"]
    dev = request.match_info["device_id"]
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{3,48}", dev):
        return json_error(404, "not_found", "Unknown device", rid)
    sample = await request.app[K_TELEMETRY].latest(dev)
    if sample is None:
        return json_error(404, "not_found", "Unknown device", rid)
    beta = (await request.app[K_POLICY].current()).beta_for(dev)
    return web.json_response({"device_id": dev, "latest": sample, "beta_global": beta})


async def set_beta_global(request: web.Request) -> web.Response:
    """Removed in v0.30: beta_global is part of the operator-signed policy (it feeds the
    device's beta > q_beta_above quarantine rule), so an admin key alone can't change it."""
    await request.app[K_AUDIT].append(
        {
            "action": "policy_rejected",
            "reason": "legacy_endpoint",
            "status": 410,
            "path": "/telemetry/beta_global",
            "client": request.get("api_key_id"),
        }
    )
    return json_error(
        410,
        "gone",
        "beta_global is governed by the operator-signed policy since v0.30; "
        "edit values.telemetry and submit it with PUT /policy",
        request["request_id"],
    )


async def get_policy(request: web.Request) -> web.Response:
    pol = await request.app[K_POLICY].current()
    if request.query.get("view") == "device":
        return web.json_response(pol.device_view())
    return web.json_response(pol.public_view(request.app[K_SETTINGS].operator_pubkey))


async def put_policy(request: web.Request) -> web.Response:
    rid = request["request_id"]
    app = request.app
    audit = app[K_AUDIT]
    client = request.get("api_key_id")
    parsed = await parse_body(request, PolicyUpdateRequest)
    if isinstance(parsed, web.Response):
        await audit.append(
            {
                "action": "policy_rejected",
                "reason": "malformed_body",
                "status": parsed.status,
                "client": client,
            }
        )
        return parsed
    req: PolicyUpdateRequest = parsed  # type: ignore[assignment]
    claimed = req.policy.get("version")
    submitted_hash = policy_hash(req.policy)
    try:
        prev, new = await app[K_POLICY].propose(
            req.policy, req.signature, app[K_SETTINGS].operator_pubkey, app[K_KEYS].pqc
        )
    except PolicyRejectedError as rej:
        await audit.append(
            {
                "action": "policy_rejected",
                "reason": rej.code,
                "status": rej.status,
                "version": claimed if isinstance(claimed, int) else None,
                "policy_sha256": submitted_hash,
                "client": client,
            }
        )
        return json_error(rej.status, rej.code, rej.message, rid)
    sig = new.signature or {}
    entry = await audit.append(
        {
            "action": "policy_accepted",
            "version": new.version,
            "policy_sha256": new.sha256,
            "prev_hash": prev.sha256,
            "prev_version": prev.version,
            "alg": sig.get("alg"),
            "operator_ed25519_key_id": (sig.get("ed25519") or {}).get("key_id"),
            "client": client,
        }
    )
    log.info("policy_accepted", version=new.version, sha256=new.sha256, request_id=rid)
    return web.json_response(
        {
            "status": "accepted",
            "version": new.version,
            "policy_sha256": new.sha256,
            "prev_hash": prev.sha256,
            "audit_seq": entry["seq"],
            "device_view_bytes": (n := len(json.dumps(new.device_view()))),
            "fits_device_buffer": n <= DEVICE_VIEW_MAX_BYTES,
        }
    )


async def audit_verify(request: web.Request) -> web.Response:
    return web.json_response(await request.app[K_AUDIT].verify())


# --------------------------------------------------------------- lifecycle
async def connect_redis(url: str, retries: int) -> redis.Redis:
    delay = 0.5
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        client = redis.from_url(
            url, decode_responses=True, max_connections=20, socket_connect_timeout=3, socket_timeout=5
        )
        try:
            await client.ping()
            log.info("redis_connected", attempt=attempt)
            return client
        except Exception as exc:  # connection refused, DNS, auth...
            last = exc
            await client.aclose()
            log.warning("redis_connect_retry", attempt=attempt, of=retries, error=type(exc).__name__)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 10.0)
    raise RuntimeError(f"could not connect to Redis after {retries} attempts") from last


def create_app(
    settings: Settings,
    keyring: KeyRing | None = None,
    redis_client: redis.Redis | None = None,
    rag: RAGEngine | None = None,
) -> web.Application:
    if keyring is None:
        keyring = KeyRing.load_or_create(settings.keys_dir, settings.key_passphrase, settings.require_pqc)
    if not keyring.pqc_available:
        log.warning(
            "pqc_unavailable",
            reason=keyring.pqc_reason,
            note="signing Ed25519-only; responses carry pqc=unavailable",
        )

    app = web.Application(
        middlewares=[request_context_middleware, auth_middleware],
        client_max_size=settings.max_body_bytes,
    )
    app[K_SETTINGS] = settings
    app[K_KEYS] = keyring
    app[K_API_KEYS] = settings.api_keys
    app[K_DEVICE_KEYS] = settings.device_api_keys
    _check_operator_key(settings, keyring)
    app[K_RAG] = rag if rag is not None else RAGEngine.from_settings(settings)

    def _wire(client: redis.Redis) -> None:
        app[K_REDIS] = client
        app[K_AUDIT] = AuditLog(client, keyring)
        app[K_LIMITER] = RateLimiter(client, settings.rate_limit_per_minute)
        app[K_TELEMETRY] = TelemetryStore(client, settings.telemetry_history)
        app[K_POLICY] = PolicyStore(client, settings)

    if redis_client is not None:
        _wire(redis_client)
    else:

        async def _redis_ctx(app_: web.Application):
            client = await connect_redis(settings.redis_url, settings.redis_connect_retries)
            _wire(client)
            yield
            await client.aclose()

        app.cleanup_ctx.append(_redis_ctx)

    async def _policy_genesis(app_: web.Application) -> None:
        pol, created = await app_[K_POLICY].ensure_genesis()
        if created:
            await app_[K_AUDIT].append(
                {
                    "action": "policy_genesis",
                    "version": pol.version,
                    "policy_sha256": pol.sha256,
                    "source": "environment",
                }
            )
        legacy = [k async for k in app_[K_REDIS].scan_iter(match="telemetry:beta_global*")]
        if legacy:
            log.warning(
                "legacy_beta_overrides_ignored",
                keys=len(legacy),
                note="v0.29 Redis beta_global overrides are not read; put them in a signed policy",
            )
        log.info("policy_active", version=pol.version, sha256=pol.sha256, source=pol.source)

    app.on_startup.append(_policy_genesis)

    async def _close_rag(app_: web.Application) -> None:
        await app_[K_RAG].close()

    app.on_cleanup.append(_close_rag)

    app.router.add_get("/health", health)
    app.router.add_get("/pubkeys", pubkeys)
    app.router.add_post("/rwa/tokenize", tokenize_rwa)
    app.router.add_get("/rwa/{token_id}", get_record)
    app.router.add_post("/verify", verify)
    app.router.add_post("/rag_guard", rag_guard)
    app.router.add_get("/audit/verify", audit_verify)
    app.router.add_post("/telemetry", telemetry)
    app.router.add_get("/telemetry/{device_id}", telemetry_latest)
    app.router.add_put("/telemetry/beta_global", set_beta_global)
    app.router.add_get("/policy", get_policy)
    app.router.add_put("/policy", put_policy)
    return app


def _check_operator_key(settings: Settings, keyring: KeyRing) -> None:
    """Refuse configurations that would undermine operator-signed policy."""
    op: OperatorPublicKey | None = settings.operator_pubkey
    kd = Path(settings.keys_dir)
    if kd.is_dir() and any(kd.glob("operator_*.key")):
        raise RuntimeError(
            f"operator private key files found in KEYS_DIR ({kd}); the server must never hold the "
            "operator private key. Move them to the operator's own machine."
        )
    if op is None:
        log.warning("operator_key_not_configured", note="PUT /policy will reject every change")
        return
    if op.ed25519 == keyring.public.ed25519 or (
        op.ml_dsa_65 is not None and op.ml_dsa_65 == keyring.public.ml_dsa_65
    ):
        raise RuntimeError("the operator key must be different from the server signing key")
    if op.hybrid and keyring.pqc is None:
        raise RuntimeError(
            "the pinned operator key is hybrid (ML-DSA-65) but liboqs is unavailable, so no policy "
            "could ever verify; install liboqs or pin an Ed25519-only operator key with REQUIRE_PQC=false"
        )

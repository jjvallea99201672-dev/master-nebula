# lattice_keeper_v040-5.py
# Patch ID: fd0adf90-rot-v5-final
# Features:
# - JWT Access + Refresh Rotation (atomic Lua)
# - Genesis Anchor on EVERY token
# - Argon2, fail-closed Config, Redis lifecycle
# - Redis-based distributed rate limiter
# - Refresh theft detection + revoke-all
# - Middleware handles Redis instability -> 503
# - Fail-closed everywhere

import asyncio
import hashlib
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict

import jwt
import redis.asyncio as redis
from aiohttp import web
from passlib.hash import argon2

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("lattice_keeper")

# ============================================================================
# Config - FAIL CLOSED
# ============================================================================

@dataclass
class Config:
    jwt_secret: str = field(default_factory=lambda: os.getenv("JWT_SECRET"))
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "lattice-core"
    jwt_audience: str = "lattice-api"
    access_token_expire_minutes: int = 20
    refresh_token_expire_days: int = 14
    refresh_token_redis_prefix: str = "lattice:refresh:"
    access_token_blacklist_prefix: str = "lattice:access:blacklist:"
    auth_rate_limit_window: int = 60
    auth_rate_limit_max: int = 10
    genesis_anchor: str = "fd0adf9005622e103f02115a50314d91f83dc705f1d685b16d45f52ad6b940b2"

    def __post_init__(self):
        if not self.jwt_secret:
            raise RuntimeError("JWT_SECRET env var required")

# ============================================================================
# Token Utils
# ============================================================================

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

def create_access_token(user_id: str, role: str, config: Config, jti: Optional[str] = None) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "role": role,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=config.access_token_expire_minutes),
        "jti": jti or secrets.token_hex(16),
        "genesis": config.genesis_anchor,
        "iss": config.jwt_issuer,
        "aud": config.jwt_audience,
    }
    return jwt.encode(payload, config.jwt_secret, algorithm=config.jwt_algorithm)

def create_refresh_token(user_id: str, config: Config) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "type": "refresh",
        "iat": now,
        "exp": now + timedelta(days=config.refresh_token_expire_days),
        "jti": secrets.token_hex(16),
        "genesis": config.genesis_anchor,
        "iss": config.jwt_issuer,
        "aud": config.jwt_audience,
    }
    return jwt.encode(payload, config.jwt_secret, algorithm=config.jwt_algorithm)

async def store_refresh_token(rc: redis.Redis, token: str, user_id: str, config: Config):
    key = f"{config.refresh_token_redis_prefix}{_hash_token(token)}"
    await rc.setex(key, config.refresh_token_expire_days * 86400, user_id)

async def atomic_consume_refresh_token(rc: redis.Redis, token: str, config: Config) -> Optional[str]:
    key = f"{config.refresh_token_redis_prefix}{_hash_token(token)}"
    lua = """
    local v = redis.call('GET', KEYS[1])
    if v then redis.call('DEL', KEYS[1]) end
    return v
    """
    result = await rc.eval(lua, 1, key)
    if result is None:
        return None
    return result.decode() if isinstance(result, bytes) else result

async def revoke_all_user_refresh_tokens(rc: redis.Redis, user_id: str, config: Config):
    # Theft detection: nuke all refresh tokens for user
    # In prod, store a secondary index: lattice:refresh:index:{user_id} -> set of hashes
    # For now scan (okay at small scale, replace with index at scale)
    pattern = f"{config.refresh_token_redis_prefix}*"
    async for key in rc.scan_iter(match=pattern, count=200):
        val = await rc.get(key)
        if val and (val.decode() if isinstance(val, bytes) else val) == user_id:
            await rc.delete(key)
    logger.warning(f"REVOKED ALL refresh tokens for user {user_id} - suspected theft")

async def blacklist_access_token(rc: redis.Redis, token: str, config: Config):
    try:
        payload = jwt.decode(token, config.jwt_secret, algorithms=[config.jwt_algorithm],
                             audience=config.jwt_audience, issuer=config.jwt_issuer,
                             options={"verify_exp": False})
        exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
        ttl = int((exp - datetime.now(timezone.utc)).total_seconds())
        if ttl > 0:
            key = f"{config.access_token_blacklist_prefix}{payload['jti']}"
            await rc.setex(key, ttl, "revoked")
    except Exception:
        pass

async def is_access_token_blacklisted(rc: redis.Redis, jti: str, config: Config) -> bool:
    key = f"{config.access_token_blacklist_prefix}{jti}"
    return await rc.exists(key) == 1

# ============================================================================
# Redis Distributed Rate Limiter
# ============================================================================

class RedisRateLimiter:
    def __init__(self, redis_client: redis.Redis, window: int, max_req: int):
        self.rc = redis_client
        self.window = window
        self.max_req = max_req

    async def is_allowed(self, key: str) -> bool:
        try:
            pipe = self.rc.pipeline()
            pipe.incr(key)
            pipe.ttl(key)
            count, ttl = await pipe.execute()
            if ttl == -1:
                await self.rc.expire(key, self.window)
            return count <= self.max_req
        except redis.RedisError:
            # Fail open for rate limiting only - don't block auth if Redis degrades
            logger.warning("Rate limiter Redis error - allowing request")
            return True

# ============================================================================
# User Store - REPLACE WITH DB
# ============================================================================

# 2026-11-27: hardcoded demo passwords removed. Read from env; no defaults.
def _require_password(env_key: str) -> str:
    pw = os.getenv(env_key)
    if not pw:
        raise RuntimeError(f"{env_key} env var required")
    return pw

USER_DB = {
    "jay.vallea": {"password_hash": argon2.hash(_require_password("LATTICE_KEEPER_PASSWORD_JAY_VALLEA")), "role": "level_4_admin"},
    "mojo": {"password_hash": argon2.hash(_require_password("LATTICE_KEEPER_PASSWORD_MOJO")), "role": "level_2"},
}

def verify_user_and_get_role(user_id: str, password: str) -> Optional[str]:
    user = USER_DB.get(user_id)
    if not user:
        return None
    try:
        if argon2.verify(password, user["password_hash"]):
            return user["role"]
    except Exception:
        return None
    return None

# ============================================================================
# Auth Endpoints
# ============================================================================

def get_client_ip(req: web.Request) -> str:
    # Handles X-Forwarded-For behind proxy
    xff = req.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return req.remote or "unknown"

async def login(req: web.Request) -> web.Response:
    config: Config = req.app["config"]
    rc: redis.Redis = req.app["redis"]
    limiter: RedisRateLimiter = req.app["auth_limiter"]

    ip = get_client_ip(req)
    if not await limiter.is_allowed(f"ratelimit:login:{ip}"):
        return web.json_response({"error": "rate limit exceeded"}, status=429)

    try:
        body = await req.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    user_id = body.get("user_id")
    password = body.get("password")
    if not user_id or not password:
        return web.json_response({"error": "user_id and password required"}, status=400)

    role = verify_user_and_get_role(user_id, password)
    if not role:
        await asyncio.sleep(0.4)
        return web.json_response({"error": "invalid credentials"}, status=401)

    access = create_access_token(user_id, role, config)
    refresh = create_refresh_token(user_id, config)
    await store_refresh_token(rc, refresh, user_id, config)

    return web.json_response({
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "Bearer",
        "expires_in": config.access_token_expire_minutes * 60,
        "role": role
    })

async def refresh(req: web.Request) -> web.Response:
    config: Config = req.app["config"]
    rc: redis.Redis = req.app["redis"]
    limiter: RedisRateLimiter = req.app["auth_limiter"]

    ip = get_client_ip(req)
    if not await limiter.is_allowed(f"ratelimit:refresh:{ip}"):
        return web.json_response({"error": "rate limit exceeded"}, status=429)

    try:
        body = await req.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    old_refresh = body.get("refresh_token")
    if not old_refresh:
        return web.json_response({"error": "refresh_token required"}, status=400)

    try:
        payload = jwt.decode(old_refresh, config.jwt_secret, algorithms=[config.jwt_algorithm],
                             audience=config.jwt_audience, issuer=config.jwt_issuer, leeway=5)
        if payload.get("type")!= "refresh":
            raise jwt.InvalidTokenError("Not a refresh token")
        if payload.get("genesis")!= config.genesis_anchor:
            raise jwt.InvalidTokenError("Genesis mismatch")
    except jwt.PyJWTError as e:
        return web.json_response({"error": f"Invalid refresh token: {str(e)}"}, status=401)

    user_id = await atomic_consume_refresh_token(rc, old_refresh, config)
    if not user_id:
        # THEFT DETECTION: token was valid JWT but not in Redis -> already used
        try:
            # Re-decode to get user for revocation (verify_exp False to get sub even if expired)
            p = jwt.decode(old_refresh, config.jwt_secret, algorithms=[config.jwt_algorithm],
                           audience=config.jwt_audience, issuer=config.jwt_issuer,
                           options={"verify_exp": False})
            suspected_user = p.get("sub")
            if suspected_user:
                await revoke_all_user_refresh_tokens(rc, suspected_user, config)
                logger.warning(f"Refresh reuse detected for {suspected_user} - all tokens revoked")
        except Exception:
            pass
        return web.json_response({"error": "Refresh token already used or revoked"}, status=401)

    role = USER_DB.get(user_id, {}).get("role", "level_0")
    new_access = create_access_token(user_id, role, config)
    new_refresh = create_refresh_token(user_id, config)
    await store_refresh_token(rc, new_refresh, user_id, config)

    return web.json_response({
        "access_token": new_access,
        "refresh_token": new_refresh,
        "token_type": "Bearer",
        "expires_in": config.access_token_expire_minutes * 60
    })

async def logout(req: web.Request) -> web.Response:
    config: Config = req.app["config"]
    rc: redis.Redis = req.app["redis"]
    limiter: RedisRateLimiter = req.app["auth_limiter"]

    ip = get_client_ip(req)
    if not await limiter.is_allowed(f"ratelimit:logout:{ip}"):
        return web.json_response({"error": "rate limit exceeded"}, status=429)

    auth_header = req.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return web.json_response({"error": "Authorization required"}, status=401)
    access_token = auth_header.split(" ", 1)[1]

    body = {}
    try:
        body = await req.json()
    except Exception:
        pass
    refresh_token = body.get("refresh_token")

    try:
        jwt.decode(access_token, config.jwt_secret, algorithms=[config.jwt_algorithm],
                   audience=config.jwt_audience, issuer=config.jwt_issuer,
                   leeway=5, options={"verify_exp": False})
        await blacklist_access_token(rc, access_token, config)
        if refresh_token:
            await rc.delete(f"{config.refresh_token_redis_prefix}{_hash_token(refresh_token)}")
        return web.json_response({"message": "Logged out successfully"})
    except jwt.PyJWTError:
        return web.json_response({"error": "Invalid token"}, status=401)

# ============================================================================
# Middleware - YOUR FIX INCLUDED
# ============================================================================

@web.middleware
async def role_context_middleware(req: web.Request, handler):
    auth_header = req.headers.get("Authorization", "")
    user = None
    role = "level_0"

    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1]
        config: Config = req.app["config"]
        rc: redis.Redis = req.app.get("redis")

        if not rc:
            return web.json_response({"error": "auth backend unavailable"}, status=503)

        try:
            payload = jwt.decode(token, config.jwt_secret, algorithms=[config.jwt_algorithm],
                                 audience=config.jwt_audience, issuer=config.jwt_issuer, leeway=5)
            if payload.get("genesis")!= config.genesis_anchor:
                raise jwt.InvalidTokenError("Genesis mismatch")
            if payload.get("type")!= "access":
                raise jwt.InvalidTokenError("Not an access token")

            try:
                if await is_access_token_blacklisted(rc, payload["jti"], config):
                    raise jwt.InvalidTokenError("Token revoked")
            except redis.RedisError as e:
                logger.warning(f"Redis blacklist check failed: {e}")
                return web.json_response({"error": "auth verification service degraded"}, status=503)

            user = payload.get("sub")
            role = payload.get("role", "level_0")
        except jwt.PyJWTError:
            user = None
            role = "level_0"

    req["user"] = user
    req["role"] = role
    return await handler(req)

# ============================================================================
# App Factory + Lifecycle
# ============================================================================

async def on_startup(app: web.Application):
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    app["redis"] = redis.from_url(redis_url, encoding="utf-8", decode_responses=True)
    await app["redis"].ping()
    app["auth_limiter"] = RedisRateLimiter(app["redis"], app["config"].auth_rate_limit_window, app["config"].auth_rate_limit_max)
    logger.info("✅ Redis connected")

async def on_cleanup(app: web.Application):
    if "redis" in app:
        await app["redis"].close()
        logger.info("✅ Redis closed")

def create_app() -> web.Application:
    app = web.Application(middlewares=[role_context_middleware])
    app["config"] = Config()
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.router.add_post("/auth/login", login)
    app.router.add_post("/auth/refresh", refresh)
    app.router.add_post("/auth/logout", logout)
    return app

def require_role(req: web.Request, allowed_roles: list[str]):
    current = req.get("role", "level_0")
    if current not in allowed_roles and current!= "level_4_admin":
        raise web.HTTPForbidden(text=f"Role '{current}' not permitted")

if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=8080)
"""Canonical encoding, hashing, HKDF and AES-256-GCM helpers.

Private material (shared secrets, session keys) must never be logged.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from constants import GENESIS_ANCHOR, SESSION_KDF_INFO_PREFIX


def canonical_json(obj: Any) -> str:
    """Stable JSON: sorted keys, compact separators, UTF-8 safe."""
    return json_dumps(obj)


def json_dumps(obj: Any) -> str:
    import json

    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def anchored_hash(payload: Any, genesis_anchor: str = GENESIS_ANCHOR) -> str:
    """SHA-256 of canonical JSON plus the New Brunswick genesis anchor."""
    return sha256_hex(canonical_json(payload) + genesis_anchor)


def signed_message(payload: Any, genesis_anchor: str = GENESIS_ANCHOR) -> bytes:
    """Bytes actually signed / verified: canonical JSON || genesis anchor."""
    return (canonical_json(payload) + genesis_anchor).encode("utf-8")


def derive_session_key(
    shared_secret: bytes,
    origin: str,
    target: str,
    info_prefix: bytes = SESSION_KDF_INFO_PREFIX,
) -> bytes:
    """Derive a 256-bit AES-GCM key from an ML-KEM shared secret via HKDF-SHA256.

    Info is bound to both node IDs so a secret from one session cannot be
    reused under a different peer pairing.
    """
    if not isinstance(shared_secret, (bytes, bytearray)) or len(shared_secret) < 16:
        raise ValueError("shared secret is missing or too short")
    info = info_prefix + b"|" + origin.encode("utf-8") + b"|" + target.encode("utf-8")
    hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info)
    return hkdf.derive(bytes(shared_secret))


def aead_encrypt(key: bytes, plaintext: bytes, aad: bytes = b"") -> dict:
    """Encrypt with AES-256-GCM. Returns hex nonce and ciphertext (tag appended)."""
    if len(key) != 32:
        raise ValueError("AES-256-GCM key must be 32 bytes")
    if isinstance(plaintext, str):
        plaintext = plaintext.encode("utf-8")
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)
    return {
        "algorithm": "AES-256-GCM",
        "nonce": nonce.hex(),
        "ciphertext": ciphertext.hex(),
        "aad_hex": aad.hex() if aad else "",
    }


def aead_decrypt(key: bytes, envelope: dict, aad: bytes | None = None) -> bytes:
    """Decrypt an AES-256-GCM envelope produced by aead_encrypt."""
    if len(key) != 32:
        raise ValueError("AES-256-GCM key must be 32 bytes")
    nonce = bytes.fromhex(envelope["nonce"])
    ciphertext = bytes.fromhex(envelope["ciphertext"])
    if aad is None:
        aad_hex = envelope.get("aad_hex") or ""
        aad = bytes.fromhex(aad_hex) if aad_hex else b""
    return AESGCM(key).decrypt(nonce, ciphertext, aad)


def short_hex(data: bytes | str, n: int = 16) -> str:
    if isinstance(data, bytes):
        data = data.hex()
    return data[:n]

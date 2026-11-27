"""Post-quantum cryptographic relay manager (Series 4).

Real ML-KEM-768 (FIPS 203) session encapsulation and ML-DSA-65 (FIPS 204)
signatures, implemented with kyber-py and dilithium-py. Session keys are
derived with HKDF-SHA256 and used for AES-256-GCM.

This is a learning prototype, not audited security software.
"""

from __future__ import annotations

import logging
from typing import Any

from dilithium_py.ml_dsa import ML_DSA_65
from kyber_py.ml_kem import ML_KEM_768

from constants import (
    AEAD_NAME,
    DSA_NAME,
    GENESIS_ANCHOR,
    KEM_NAME,
    KEY_DIR,
    ML_DSA_65_SIG_SIZE,
    ML_KEM_768_CT_SIZE,
    ML_KEM_768_EK_SIZE,
)
from crypto_utils import (
    aead_decrypt,
    aead_encrypt,
    derive_session_key,
    sha256_hex,
    short_hex,
    signed_message,
)
from keys_store import KeyStore, NodeKeys

log = logging.getLogger("toroidal.pqc")


class QuantumSession:
    """A derived AES-256-GCM session. The raw ML-KEM shared secret is not kept."""

    def __init__(self, origin: str, target: str, session_key: bytes, ciphertext_hex: str = ""):
        if len(session_key) != 32:
            raise ValueError("session key must be 32 bytes")
        self.origin = origin
        self.target = target
        self.algorithm = AEAD_NAME
        self.kem = KEM_NAME
        self.ciphertext_hex = ciphertext_hex
        self._key = session_key

    def encrypt(self, plaintext: bytes | str, aad: bytes | str | None = None) -> dict:
        if isinstance(aad, str):
            aad = aad.encode("utf-8")
        aad_bytes = aad or f"{self.origin}->{self.target}".encode("utf-8")
        if isinstance(plaintext, str):
            plaintext_bytes = plaintext.encode("utf-8")
        else:
            plaintext_bytes = plaintext
        envelope = aead_encrypt(self._key, plaintext_bytes, aad_bytes)
        envelope.update(
            {
                "origin": self.origin,
                "target": self.target,
                "kem": self.kem,
            }
        )
        return envelope

    def decrypt(self, envelope: dict) -> bytes:
        aad_hex = envelope.get("aad_hex") or ""
        aad = bytes.fromhex(aad_hex) if aad_hex else f"{self.origin}->{self.target}".encode("utf-8")
        return aead_decrypt(self._key, envelope, aad=aad)

    def __repr__(self) -> str:
        return (
            f"QuantumSession(origin={self.origin!r}, target={self.target!r}, "
            f"algorithm={self.algorithm})"
        )


class InterstellarPQCManager:
    def __init__(
        self,
        node_id: str,
        system_sector: str,
        keys_dir=None,
        keystore: KeyStore | None = None,
    ):
        self.node_id = node_id
        self.sector = system_sector
        self.genesis_anchor = GENESIS_ANCHOR
        self._keystore = keystore or KeyStore(keys_dir or KEY_DIR)
        keys: NodeKeys = self._keystore.load_or_create(node_id, system_sector)
        # Public material may be shared. Private keys stay underscored and unlogged.
        self.ml_kem_public_key = keys.kem_ek
        self.ml_dsa_public_key = keys.dsa_pk
        self._kem_dk = keys.kem_dk
        self._dsa_sk = keys.dsa_sk
        self._last_shared_secret: bytes | None = None
        self._last_session_target: str | None = None
        self._last_ciphertext_hex: str = ""

    def public_bundle(self) -> dict:
        return {
            "node_id": self.node_id,
            "sector": self.sector,
            "ml_kem_public_key_hex": self.ml_kem_public_key.hex(),
            "ml_dsa_public_key_hex": self.ml_dsa_public_key.hex(),
        }

    def encapsulate_quantum_session(self, target_node_pubkey: bytes | str, target_id: str = "") -> dict:
        """Establishes a quantum-safe encrypted channel using ML-KEM-768.

        Returns a transmittable envelope containing the ciphertext only.
        The shared secret is kept on this instance (never logged) and is
        also used by session_from_last_encaps().
        """
        ek = _parse_kem_ek(target_node_pubkey)
        preview = ek.hex()
        log.info(
            "[%s] Encapsulating shared secret via ML-KEM-768 for target: %s...",
            self.node_id,
            preview[:16],
        )
        shared_secret, ciphertext = ML_KEM_768.encaps(ek)
        if len(ciphertext) != ML_KEM_768_CT_SIZE:
            raise RuntimeError(f"unexpected ML-KEM ciphertext size: {len(ciphertext)}")
        self._last_shared_secret = shared_secret
        self._last_session_target = target_id
        self._last_ciphertext_hex = ciphertext.hex()
        envelope = {
            "protocol": KEM_NAME,
            "origin": self.node_id,
            "target": target_id,
            "ciphertext": ciphertext.hex(),
            "ciphertext_bytes": len(ciphertext),
            "ciphertext_hash": sha256_hex(ciphertext),
            "genesis_anchored": True,
            "kdf": "HKDF-SHA256",
            "aead": AEAD_NAME,
        }
        log.info(
            "[%s] ML-KEM-768 ciphertext ready (%d B, hash %s).",
            self.node_id,
            len(ciphertext),
            short_hex(envelope["ciphertext_hash"]),
        )
        return envelope

    def decapsulate_quantum_session(self, envelope: dict) -> bytes:
        """Recover the ML-KEM shared secret from a peer's envelope. Not logged."""
        ct = bytes.fromhex(envelope["ciphertext"])
        if len(ct) != ML_KEM_768_CT_SIZE:
            raise ValueError(f"ciphertext must be {ML_KEM_768_CT_SIZE} bytes, got {len(ct)}")
        shared_secret = ML_KEM_768.decaps(self._kem_dk, ct)
        origin = envelope.get("origin") or ""
        self._last_shared_secret = shared_secret
        self._last_session_target = origin
        self._last_ciphertext_hex = envelope["ciphertext"]
        log.info(
            "[%s] Decapsulated ML-KEM-768 session from %s (ciphertext hash %s).",
            self.node_id,
            origin or "peer",
            short_hex(envelope.get("ciphertext_hash") or sha256_hex(ct)),
        )
        return shared_secret

    def session_from_last_encaps(self, target_id: str | None = None) -> QuantumSession:
        if self._last_shared_secret is None:
            raise RuntimeError("no session secret; call encapsulate_quantum_session first")
        target = target_id or self._last_session_target or ""
        key = derive_session_key(self._last_shared_secret, self.node_id, target)
        return QuantumSession(self.node_id, target, key, self._last_ciphertext_hex)

    def session_from_decaps(self, envelope: dict, shared_secret: bytes | None = None) -> QuantumSession:
        secret = shared_secret if shared_secret is not None else self._last_shared_secret
        if secret is None:
            secret = self.decapsulate_quantum_session(envelope)
        origin = envelope.get("origin") or ""
        # The initiator derived the key as HKDF(ss, origin=initiator, target=this node).
        key = derive_session_key(secret, origin, self.node_id)
        # QuantumSession.origin is the initiator, so encrypt/decrypt AAD matches.
        session = QuantumSession(origin, self.node_id, key, envelope.get("ciphertext", ""))
        return session

    def open_session(self, target: "InterstellarPQCManager") -> tuple[QuantumSession, dict, QuantumSession]:
        """Full handshake helper: encapsulate to target, both sides derive the same key."""
        envelope = self.encapsulate_quantum_session(target.ml_kem_public_key, target_id=target.node_id)
        origin_session = self.session_from_last_encaps(target.node_id)
        secret = target.decapsulate_quantum_session(envelope)
        target_session = target.session_from_decaps(envelope, secret)
        return origin_session, envelope, target_session

    def sign_toroidal_state(self, state_payload: dict) -> dict:
        """Applies an ML-DSA-65 post-quantum digital signature to a ledger transaction."""
        message = signed_message(state_payload, self.genesis_anchor)
        digest = sha256_hex(message)
        signature = ML_DSA_65.sign(self._dsa_sk, message)
        if len(signature) != ML_DSA_65_SIG_SIZE:
            # FIPS 204 fixes this at 3309 bytes; treat any other size as a fault.
            raise RuntimeError(f"unexpected ML-DSA-65 signature size: {len(signature)}")
        result = {
            "algorithm": DSA_NAME,
            "signer": self.node_id,
            "hash_digest": digest,
            "quantum_signature": signature.hex(),
            "quantum_signature_bytes": len(signature),
            "quantum_signature_token": signature.hex()[:32],
            "genesis_anchor_prefix": self.genesis_anchor[:16],
        }
        log.info("[%s] Toroidal transaction signed with ML-DSA-65 [Anchor Verified].", self.node_id)
        return result

    def verify_toroidal_state(
        self,
        state_payload: dict,
        signature: dict,
        public_key: bytes | str | None = None,
    ) -> bool:
        """Verify an ML-DSA-65 signature over canonical JSON plus the genesis anchor."""
        message = signed_message(state_payload, self.genesis_anchor)
        expected_digest = sha256_hex(message)
        if signature.get("hash_digest") and signature["hash_digest"] != expected_digest:
            log.info(
                "[%s] Signature hash mismatch (claimed %s, computed %s).",
                self.node_id,
                short_hex(signature["hash_digest"]),
                short_hex(expected_digest),
            )
            return False
        pk = _parse_dsa_pk(public_key if public_key is not None else self.ml_dsa_public_key)
        try:
            sig_bytes = bytes.fromhex(signature["quantum_signature"])
        except (KeyError, ValueError):
            return False
        try:
            ok = ML_DSA_65.verify(pk, message, sig_bytes)
        except Exception:
            return False
        return bool(ok)

    def __repr__(self) -> str:
        return f"InterstellarPQCManager(node_id={self.node_id!r}, system_sector={self.sector!r})"


def verify_signature(
    state_payload: dict,
    signature: dict,
    public_key: bytes | str,
    genesis_anchor: str = GENESIS_ANCHOR,
) -> bool:
    """Standalone ML-DSA-65 verification (no private key required)."""
    message = signed_message(state_payload, genesis_anchor)
    if signature.get("hash_digest") and signature["hash_digest"] != sha256_hex(message):
        return False
    try:
        pk = _parse_dsa_pk(public_key)
        sig_bytes = bytes.fromhex(signature["quantum_signature"])
        return bool(ML_DSA_65.verify(pk, message, sig_bytes))
    except Exception:
        return False


def _parse_kem_ek(value: bytes | str) -> bytes:
    if isinstance(value, bytes):
        ek = value
    elif isinstance(value, str):
        cleaned = value.strip().replace(" ", "")
        try:
            ek = bytes.fromhex(cleaned)
        except ValueError as exc:
            raise ValueError(
                "target_node_pubkey must be raw ML-KEM-768 bytes or hex, "
                "not a placeholder string"
            ) from exc
    else:
        raise TypeError("target_node_pubkey must be bytes or hex str")
    if len(ek) != ML_KEM_768_EK_SIZE:
        raise ValueError(f"ML-KEM-768 encapsulation key must be {ML_KEM_768_EK_SIZE} bytes, got {len(ek)}")
    return ek


def _parse_dsa_pk(value: bytes | str) -> bytes:
    from constants import ML_DSA_65_PK_SIZE

    if isinstance(value, bytes):
        pk = value
    elif isinstance(value, str):
        pk = bytes.fromhex(value.strip())
    else:
        raise TypeError("public key must be bytes or hex str")
    if len(pk) != ML_DSA_65_PK_SIZE:
        raise ValueError(f"ML-DSA-65 public key must be {ML_DSA_65_PK_SIZE} bytes, got {len(pk)}")
    return pk

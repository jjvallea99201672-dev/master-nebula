"""Key management, hybrid signatures and ML-KEM encryption.

Copyright (c) 2026 Jay Vallea. All Rights Reserved.

Signatures
----------
Every signature envelope covers ``SIG_CONTEXT + sha256(payload).hexdigest()``
where ``payload`` is the canonical JSON encoding (sorted keys, no whitespace,
UTF-8). The envelope always contains an Ed25519 signature and, when liboqs is
available, an ML-DSA-65 signature over the same message (hybrid). A verifier
that holds an ML-DSA-65 public key requires *both* signatures, so stripping
the post-quantum signature is detected.

KEM encryption wire format (``lk-kem-v1``)
------------------------------------------
    ss, kem_ct = ML-KEM-768.Encaps(recipient_public_key)
    key        = HKDF-SHA256(ikm=ss, salt=kem_ct, info=KEM_INFO, length=32)
    nonce      = 12 random bytes
    ct         = AES-256-GCM(key, nonce, plaintext, aad)   # includes 16-byte tag

JSON: {"format":"lk-kem-v1","alg":"ML-KEM-768+HKDF-SHA256+AES-256-GCM",
       "recipient_key_id":hex,"kem_ciphertext":b64,"nonce":b64,
       "ciphertext":b64,"aad":b64}
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

SIG_ALG_PQ = "ML-DSA-65"
KEM_ALG = "ML-KEM-768"
SIG_CONTEXT = b"lattice-keeper/sig/v1:"
KEM_FORMAT = "lk-kem-v1"
KEM_INFO = b"lattice-keeper/kem/v1 ML-KEM-768 AES-256-GCM"
KEY_FILE_FORMAT = "lattice-keeper-secret/v1"
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**15, 8, 1


class KeyFileError(Exception):
    """Raised for key loading / passphrase problems."""


class PQCUnavailableError(RuntimeError):
    """Raised when post-quantum crypto is required but liboqs is missing."""


# ----------------------------------------------------------------- helpers
def b64e(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def b64d(data: str) -> bytes:
    return base64.b64decode(data.encode("ascii"), validate=True)


def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def key_id(public_key: bytes) -> str:
    return sha256_hex(public_key)[:16]


def _signing_message(payload: bytes, context: bytes = SIG_CONTEXT) -> bytes:
    return context + sha256_hex(payload).encode("ascii")


def atomic_write(path: Path, data: bytes, mode: int) -> None:
    """Write ``data`` to ``path`` atomically; the file is created with ``mode``
    from the start (no window where it is world-readable)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _derive_kek(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P).derive(passphrase.encode())


def seal_secret(secret: bytes, alg: str, passphrase: str | None) -> bytes:
    doc: dict[str, Any] = {"format": KEY_FILE_FORMAT, "alg": alg}
    if passphrase:
        salt, nonce = os.urandom(16), os.urandom(12)
        kek = _derive_kek(passphrase, salt)
        doc.update(
            encrypted=True,
            kdf={"name": "scrypt", "n": _SCRYPT_N, "r": _SCRYPT_R, "p": _SCRYPT_P, "salt": b64e(salt)},
            cipher="AES-256-GCM",
            nonce=b64e(nonce),
            ciphertext=b64e(AESGCM(kek).encrypt(nonce, secret, alg.encode())),
        )
    else:
        doc.update(encrypted=False, secret=b64e(secret))
    return json.dumps(doc, indent=2).encode()


def open_secret(blob: bytes, alg: str, passphrase: str | None) -> tuple[bytes, bool]:
    """Return (secret, was_encrypted)."""
    try:
        doc = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise KeyFileError("key file is not valid JSON") from exc
    if doc.get("format") != KEY_FILE_FORMAT or doc.get("alg") != alg:
        raise KeyFileError(f"key file format/alg mismatch (expected {alg})")
    if not doc.get("encrypted"):
        return b64d(doc["secret"]), False
    if not passphrase:
        raise KeyFileError(f"{alg} private key is encrypted; set LATTICE_KEY_PASSPHRASE")
    kdf = doc["kdf"]
    kek = Scrypt(salt=b64d(kdf["salt"]), length=32, n=int(kdf["n"]), r=int(kdf["r"]), p=int(kdf["p"])).derive(
        passphrase.encode()
    )
    try:
        return AESGCM(kek).decrypt(b64d(doc["nonce"]), b64d(doc["ciphertext"]), alg.encode()), True
    except InvalidTag as exc:
        raise KeyFileError(f"wrong passphrase for {alg} private key") from exc


# ------------------------------------------------------------- PQC backend
class OQSBackend:
    """Thin wrapper around liboqs-python."""

    def __init__(self) -> None:
        import oqs  # type: ignore[import-not-found]

        if SIG_ALG_PQ not in oqs.get_enabled_sig_mechanisms():
            raise PQCUnavailableError(f"liboqs build lacks {SIG_ALG_PQ}")
        if KEM_ALG not in oqs.get_enabled_kem_mechanisms():
            raise PQCUnavailableError(f"liboqs build lacks {KEM_ALG}")
        self._oqs = oqs

    def sig_keypair(self) -> tuple[bytes, bytes]:
        with self._oqs.Signature(SIG_ALG_PQ) as s:
            pk = s.generate_keypair()
            return bytes(pk), bytes(s.export_secret_key())

    def sign(self, secret_key: bytes, message: bytes) -> bytes:
        with self._oqs.Signature(SIG_ALG_PQ, secret_key=secret_key) as s:
            return bytes(s.sign(message))

    def verify(self, public_key: bytes, message: bytes, signature: bytes) -> bool:
        with self._oqs.Signature(SIG_ALG_PQ) as s:
            try:
                return bool(s.verify(message, signature, public_key))
            except Exception:  # malformed input
                return False

    def kem_keypair(self) -> tuple[bytes, bytes]:
        with self._oqs.KeyEncapsulation(KEM_ALG) as k:
            pk = k.generate_keypair()
            return bytes(pk), bytes(k.export_secret_key())

    def encaps(self, public_key: bytes) -> tuple[bytes, bytes]:
        with self._oqs.KeyEncapsulation(KEM_ALG) as k:
            ct, ss = k.encap_secret(public_key)
            return bytes(ct), bytes(ss)

    def decaps(self, secret_key: bytes, ciphertext: bytes) -> bytes:
        with self._oqs.KeyEncapsulation(KEM_ALG, secret_key=secret_key) as k:
            return bytes(k.decap_secret(ciphertext))


def _liboqs_present() -> bool:
    """True if the liboqs shared library can be found *without* importing oqs.

    liboqs-python tries to git-clone and compile liboqs at import time when the
    library is missing; we never want that inside a running server.
    """
    import ctypes.util
    import platform

    base = Path(os.environ.get("OQS_INSTALL_PATH", Path.home() / "_oqs"))
    names = ("oqs.dll",) if platform.system() == "Windows" else ("liboqs.so", "liboqs.dylib")
    for sub in ("lib", "lib64", "bin"):
        if any((base / sub / n).exists() for n in names):
            return True
    return ctypes.util.find_library("oqs") is not None


def load_pqc_backend() -> tuple[OQSBackend | None, str | None]:
    """Return (backend, reason_if_unavailable). Never fakes anything."""
    if not _liboqs_present():
        return None, "liboqs shared library not found (set OQS_INSTALL_PATH)"
    try:
        return OQSBackend(), None
    except PQCUnavailableError as exc:
        return None, str(exc)
    except (ImportError, OSError, RuntimeError, SystemExit) as exc:
        # liboqs-python raises/aborts in several ways when the C library is missing.
        return None, f"liboqs not available ({type(exc).__name__})"


# ----------------------------------------------------------------- keyring
@dataclass
class PublicKeys:
    ed25519: bytes
    ml_dsa_65: bytes | None = None
    ml_kem_768: bytes | None = None

    def to_json(self) -> dict[str, Any]:
        def entry(pk: bytes | None, alg: str) -> dict[str, str] | None:
            return None if pk is None else {"alg": alg, "key_id": key_id(pk), "public_key": b64e(pk)}

        return {
            "ed25519": entry(self.ed25519, "Ed25519"),
            "ml_dsa_65": entry(self.ml_dsa_65, SIG_ALG_PQ),
            "ml_kem_768": entry(self.ml_kem_768, KEM_ALG),
        }


class KeyRing:
    def __init__(
        self,
        ed25519: Ed25519PrivateKey,
        pqc: OQSBackend | None,
        mldsa: tuple[bytes, bytes] | None,
        mlkem: tuple[bytes, bytes] | None,
        pqc_reason: str | None = None,
    ) -> None:
        self._ed = ed25519
        self.pqc = pqc
        self._mldsa = mldsa  # (public, secret)
        self._mlkem = mlkem
        self.pqc_reason = pqc_reason
        self.public = PublicKeys(
            ed25519=ed25519.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            ),
            ml_dsa_65=mldsa[0] if mldsa else None,
            ml_kem_768=mlkem[0] if mlkem else None,
        )
        self.encrypted_at_rest = False

    @property
    def pqc_available(self) -> bool:
        return self.pqc is not None and self._mldsa is not None

    # -------------------------------------------------------------- loading
    @classmethod
    def load_or_create(
        cls,
        keys_dir: str | Path,
        passphrase: str | None,
        require_pqc: bool,
        pqc: OQSBackend | None | bool = True,
    ) -> KeyRing:
        """Load keys from ``keys_dir`` (creating any that are missing).

        ``pqc=True`` auto-detects liboqs; pass ``None`` to force classical-only
        or an ``OQSBackend`` instance to inject one.
        """
        reason: str | None = None
        if pqc is True:
            backend, reason = load_pqc_backend()
        elif pqc is False or pqc is None:
            backend, reason = None, "disabled by configuration"
        else:
            backend = pqc
        if backend is None and require_pqc:
            raise PQCUnavailableError(
                f"Post-quantum crypto is required (REQUIRE_PQC=true) but unavailable: {reason}. "
                "Install the [pqc] extra with liboqs, or set REQUIRE_PQC=false to run "
                "classical-only (responses will say pqc: unavailable)."
            )

        d = Path(keys_dir)
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o700)
        encrypted_flags: list[bool] = []

        def load_pair(name: str, alg: str, gen) -> tuple[bytes, bytes]:
            sk_path, pk_path = d / f"{name}.key", d / f"{name}.pub"
            if sk_path.exists() and pk_path.exists():
                sk, enc = open_secret(sk_path.read_bytes(), alg, passphrase)
                encrypted_flags.append(enc)
                return pk_path.read_bytes(), sk
            pk, sk = gen()
            atomic_write(sk_path, seal_secret(sk, alg, passphrase), 0o600)
            atomic_write(pk_path, pk, 0o644)
            encrypted_flags.append(bool(passphrase))
            return pk, sk

        def gen_ed() -> tuple[bytes, bytes]:
            k = Ed25519PrivateKey.generate()
            return (
                k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw),
                k.private_bytes(
                    serialization.Encoding.Raw,
                    serialization.PrivateFormat.Raw,
                    serialization.NoEncryption(),
                ),
            )

        ed_pk, ed_sk = load_pair("ed25519", "Ed25519", gen_ed)
        ed = Ed25519PrivateKey.from_private_bytes(ed_sk)
        if ed.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw) != ed_pk:
            raise KeyFileError("ed25519.pub does not match ed25519.key")

        mldsa = mlkem = None
        if backend is not None:
            mldsa = load_pair("ml_dsa_65", SIG_ALG_PQ, backend.sig_keypair)
            mlkem = load_pair("ml_kem_768", KEM_ALG, backend.kem_keypair)
        ring = cls(ed, backend, mldsa, mlkem, reason)
        ring.encrypted_at_rest = bool(encrypted_flags) and all(encrypted_flags)
        return ring

    # -------------------------------------------------------------- signing
    def sign(self, payload: bytes) -> dict[str, Any]:
        msg = _signing_message(payload)
        env: dict[str, Any] = {
            "v": 1,
            "payload_sha256": sha256_hex(payload),
            "ed25519": {"key_id": key_id(self.public.ed25519), "sig": b64e(self._ed.sign(msg))},
        }
        if self.pqc_available:
            assert self.pqc is not None and self._mldsa is not None
            env["alg"] = "hybrid:Ed25519+ML-DSA-65"
            env["pqc"] = "ml-dsa-65"
            env["ml_dsa_65"] = {
                "key_id": key_id(self._mldsa[0]),
                "sig": b64e(self.pqc.sign(self._mldsa[1], msg)),
            }
        else:
            env["alg"] = "Ed25519"
            env["pqc"] = "unavailable"
            env["ml_dsa_65"] = None
        return env

    def verify(self, payload: bytes, envelope: dict[str, Any]) -> dict[str, Any]:
        return verify_envelope(payload, envelope, self.public, self.pqc)

    # ------------------------------------------------------------------ KEM
    def kem_encrypt(
        self, plaintext: bytes, aad: bytes, recipient_public_key: bytes | None = None
    ) -> dict[str, Any]:
        if self.pqc is None or self.public.ml_kem_768 is None:
            return {"status": "unavailable", "reason": "ML-KEM-768 requires liboqs; no ciphertext produced"}
        pk = recipient_public_key or self.public.ml_kem_768
        kem_ct, ss = self.pqc.encaps(pk)
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=kem_ct, info=KEM_INFO).derive(ss)
        nonce = os.urandom(12)
        return {
            "status": "encrypted",
            "format": KEM_FORMAT,
            "alg": "ML-KEM-768+HKDF-SHA256+AES-256-GCM",
            "recipient_key_id": key_id(pk),
            "kem_ciphertext": b64e(kem_ct),
            "nonce": b64e(nonce),
            "ciphertext": b64e(AESGCM(key).encrypt(nonce, plaintext, aad)),
            "aad": b64e(aad),
        }

    def kem_decrypt(self, envelope: dict[str, Any]) -> bytes:
        if self.pqc is None or self._mlkem is None:
            raise PQCUnavailableError("ML-KEM-768 unavailable")
        if envelope.get("format") != KEM_FORMAT:
            raise ValueError("unsupported KEM envelope format")
        kem_ct = b64d(envelope["kem_ciphertext"])
        ss = self.pqc.decaps(self._mlkem[1], kem_ct)
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=kem_ct, info=KEM_INFO).derive(ss)
        return AESGCM(key).decrypt(
            b64d(envelope["nonce"]), b64d(envelope["ciphertext"]), b64d(envelope["aad"])
        )


def verify_envelope(
    payload: bytes,
    envelope: dict[str, Any],
    public: PublicKeys,
    pqc: OQSBackend | None,
    context: bytes = SIG_CONTEXT,
) -> dict[str, Any]:
    """Verify a signature envelope. ``valid`` is True only if every required
    signature checks out. If the verifier knows an ML-DSA-65 key, the PQC
    signature is required (prevents downgrade by stripping it)."""
    result: dict[str, Any] = {
        "valid": False,
        "payload_hash_match": False,
        "ed25519": False,
        "ml_dsa_65": None,
    }
    if not isinstance(envelope, dict):
        result["reason"] = "envelope must be an object"
        return result
    msg = _signing_message(payload, context)
    result["payload_hash_match"] = envelope.get("payload_sha256") == sha256_hex(payload)

    ed = envelope.get("ed25519") or {}
    try:
        if ed.get("key_id") == key_id(public.ed25519):
            Ed25519PublicKey.from_public_bytes(public.ed25519).verify(b64d(ed["sig"]), msg)
            result["ed25519"] = True
    except (InvalidSignature, KeyError, ValueError, TypeError):
        result["ed25519"] = False

    pq = envelope.get("ml_dsa_65")
    pq_required = public.ml_dsa_65 is not None
    if pq_required:
        ok = False
        if isinstance(pq, dict) and pqc is not None and pq.get("key_id") == key_id(public.ml_dsa_65):
            try:
                ok = pqc.verify(public.ml_dsa_65, msg, b64d(pq["sig"]))
            except (KeyError, ValueError, TypeError):
                ok = False
        result["ml_dsa_65"] = ok
    elif pq:
        result["ml_dsa_65"] = "unverifiable (no ML-DSA-65 key/backend on this verifier)"

    result["valid"] = bool(
        result["payload_hash_match"]
        and result["ed25519"]
        and (result["ml_dsa_65"] is True or not pq_required)
    )
    return result

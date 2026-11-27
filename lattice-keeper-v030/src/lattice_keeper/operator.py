"""Operator signing key (offline) and the operator public key (pinned on the server).

Copyright (c) 2026 Jay Vallea. All Rights Reserved.

The operator key is a *separate* key from the server's signing key. It lives on
the operator's own machine (ideally offline) and is used only to sign policy
documents with ``lattice-keeper policy-sign``. The server is given the operator
*public* key (``OPERATOR_PUBKEY_FILE`` or ``OPERATOR_ED25519_PUBKEY`` /
``OPERATOR_ML_DSA_65_PUBKEY``) and never holds the operator private key.

Signatures use the same envelope format as the server's, but over a different
context string so a server signature can never be replayed as an operator
signature (or vice versa)::

    msg = POLICY_SIG_CONTEXT + sha256_hex(canonical_json(policy))
    Ed25519.sign(msg)  [+ ML-DSA-65.sign(msg) when liboqs is available]
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .crypto import (
    SIG_ALG_PQ,
    KeyFileError,
    OQSBackend,
    PublicKeys,
    _signing_message,
    atomic_write,
    b64d,
    b64e,
    key_id,
    open_secret,
    seal_secret,
    sha256_hex,
    verify_envelope,
)

POLICY_SIG_CONTEXT = b"lattice-keeper/policy/v1:"
OPERATOR_PUB_FORMAT = "lattice-keeper-operator-pubkey/v1"
OPERATOR_PUB_FILE = "operator_pubkey.json"
_ED_NAME, _PQ_NAME = "operator_ed25519", "operator_ml_dsa_65"
ED25519_PUB_LEN, ML_DSA_65_PUB_LEN = 32, 1952


def _raw_pub(k: Ed25519PrivateKey) -> bytes:
    return k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


@dataclass
class OperatorPublicKey:
    """The pinned operator public key(s) the server verifies policies against."""

    ed25519: bytes
    ml_dsa_65: bytes | None = None

    def __post_init__(self) -> None:
        if len(self.ed25519) != ED25519_PUB_LEN:
            raise KeyFileError("operator Ed25519 public key must be 32 bytes")
        if self.ml_dsa_65 is not None and len(self.ml_dsa_65) != ML_DSA_65_PUB_LEN:
            raise KeyFileError(f"operator ML-DSA-65 public key must be {ML_DSA_65_PUB_LEN} bytes")

    @property
    def hybrid(self) -> bool:
        return self.ml_dsa_65 is not None

    def as_public_keys(self) -> PublicKeys:
        return PublicKeys(ed25519=self.ed25519, ml_dsa_65=self.ml_dsa_65)

    def to_json(self) -> dict[str, Any]:
        return {
            "format": OPERATOR_PUB_FORMAT,
            "alg": "hybrid:Ed25519+ML-DSA-65" if self.hybrid else "Ed25519",
            "ed25519": {"key_id": key_id(self.ed25519), "public_key": b64e(self.ed25519)},
            "ml_dsa_65": None
            if self.ml_dsa_65 is None
            else {"key_id": key_id(self.ml_dsa_65), "public_key": b64e(self.ml_dsa_65)},
        }

    def summary(self) -> dict[str, Any]:
        """Short form (no long ML-DSA key) for API responses."""
        return {
            "alg": "hybrid:Ed25519+ML-DSA-65" if self.hybrid else "Ed25519",
            "ed25519_key_id": key_id(self.ed25519),
            "ed25519_public_key": b64e(self.ed25519),
            "ml_dsa_65_key_id": None if self.ml_dsa_65 is None else key_id(self.ml_dsa_65),
        }

    @classmethod
    def from_json(cls, doc: dict[str, Any]) -> OperatorPublicKey:
        if not isinstance(doc, dict) or doc.get("format") != OPERATOR_PUB_FORMAT:
            raise KeyFileError(f"operator public key file must have format {OPERATOR_PUB_FORMAT!r}")
        try:
            ed = b64d(doc["ed25519"]["public_key"])
            pq = doc.get("ml_dsa_65")
            pq_raw = b64d(pq["public_key"]) if pq else None
        except (KeyError, TypeError, ValueError) as exc:
            raise KeyFileError("operator public key file is malformed") from exc
        return cls(ed, pq_raw)

    @classmethod
    def from_file(cls, path: str | Path) -> OperatorPublicKey:
        try:
            return cls.from_json(json.loads(Path(path).read_text()))
        except (OSError, ValueError) as exc:
            raise KeyFileError(f"cannot read operator public key file {path}: {exc}") from exc

    def verify(self, payload: bytes, envelope: dict[str, Any], pqc: OQSBackend | None) -> dict[str, Any]:
        return verify_envelope(payload, envelope, self.as_public_keys(), pqc, context=POLICY_SIG_CONTEXT)


class OperatorKey:
    """Operator private key. Only ever loaded by the offline CLI commands."""

    def __init__(self, ed: Ed25519PrivateKey, pqc: OQSBackend | None, mldsa: tuple[bytes, bytes] | None):
        self._ed = ed
        self.pqc = pqc
        self._mldsa = mldsa
        self.public = OperatorPublicKey(_raw_pub(ed), mldsa[0] if mldsa else None)

    @classmethod
    def generate(
        cls, out_dir: str | Path, passphrase: str | None, pqc: OQSBackend | None, force: bool = False
    ) -> OperatorKey:
        d = Path(out_dir)
        existing = [p for p in d.glob("operator_*") if p.is_file()] if d.exists() else []
        if existing and not force:
            raise KeyFileError(f"{d} already contains operator key files; refusing to overwrite")
        d.mkdir(parents=True, exist_ok=True)
        d.chmod(0o700)
        ed = Ed25519PrivateKey.generate()
        ed_sk = ed.private_bytes(
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
        )
        atomic_write(d / f"{_ED_NAME}.key", seal_secret(ed_sk, "Ed25519", passphrase), 0o600)
        atomic_write(d / f"{_ED_NAME}.pub", _raw_pub(ed), 0o644)
        mldsa = None
        if pqc is not None:
            pk, sk = pqc.sig_keypair()
            atomic_write(d / f"{_PQ_NAME}.key", seal_secret(sk, SIG_ALG_PQ, passphrase), 0o600)
            atomic_write(d / f"{_PQ_NAME}.pub", pk, 0o644)
            mldsa = (pk, sk)
        key = cls(ed, pqc, mldsa)
        atomic_write(d / OPERATOR_PUB_FILE, json.dumps(key.public.to_json(), indent=2).encode(), 0o644)
        return key

    @classmethod
    def load(cls, key_dir: str | Path, passphrase: str | None, pqc: OQSBackend | None) -> OperatorKey:
        d = Path(key_dir)
        ed_path = d / f"{_ED_NAME}.key"
        if not ed_path.is_file():
            raise KeyFileError(f"no operator key in {d} (run: lattice-keeper operator-keygen --out {d})")
        ed_sk, _ = open_secret(ed_path.read_bytes(), "Ed25519", passphrase)
        ed = Ed25519PrivateKey.from_private_bytes(ed_sk)
        pub_path = d / f"{_ED_NAME}.pub"
        if pub_path.is_file() and pub_path.read_bytes() != _raw_pub(ed):
            raise KeyFileError("operator_ed25519.pub does not match operator_ed25519.key")
        mldsa = None
        pq_path = d / f"{_PQ_NAME}.key"
        if pq_path.is_file():
            if pqc is None:
                raise KeyFileError(
                    "this operator key is hybrid (ML-DSA-65) but liboqs is unavailable here; "
                    "sign on a machine with liboqs so the server's hybrid check passes"
                )
            sk, _ = open_secret(pq_path.read_bytes(), SIG_ALG_PQ, passphrase)
            mldsa = ((d / f"{_PQ_NAME}.pub").read_bytes(), sk)
        return cls(ed, pqc, mldsa)

    def sign(self, payload: bytes) -> dict[str, Any]:
        msg = _signing_message(payload, POLICY_SIG_CONTEXT)
        env: dict[str, Any] = {
            "v": 1,
            "signer": "operator",
            "context": POLICY_SIG_CONTEXT.decode().rstrip(":"),
            "payload_sha256": sha256_hex(payload),
            "ed25519": {"key_id": key_id(self.public.ed25519), "sig": b64e(self._ed.sign(msg))},
        }
        if self._mldsa is not None and self.pqc is not None:
            env["alg"] = "hybrid:Ed25519+ML-DSA-65"
            env["ml_dsa_65"] = {
                "key_id": key_id(self._mldsa[0]),
                "sig": b64e(self.pqc.sign(self._mldsa[1], msg)),
            }
        else:
            env["alg"] = "Ed25519"
            env["ml_dsa_65"] = None
        return env

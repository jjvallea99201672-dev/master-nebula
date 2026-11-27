"""On-disk storage for per-node ML-KEM and ML-DSA keypairs.

Private keys are written with mode 0600 and are never printed or logged.
The public index (index.json) contains only public keys.
"""

from __future__ import annotations

import json
import logging
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from dilithium_py.ml_dsa import ML_DSA_65
from kyber_py.ml_kem import ML_KEM_768

from constants import (
    KEY_DIR,
    ML_DSA_65_PK_SIZE,
    ML_DSA_65_SK_SIZE,
    ML_KEM_768_DK_SIZE,
    ML_KEM_768_EK_SIZE,
    PUBLIC_INDEX,
)

log = logging.getLogger("toroidal.keys")

_NODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def sanitize_node_id(node_id: str) -> str:
    if not isinstance(node_id, str) or not _NODE_RE.fullmatch(node_id):
        raise ValueError(f"invalid node_id: {node_id!r}")
    return node_id


def _write_secret(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)


def _write_public(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.chmod(path, 0o644)


def _ensure_secret_mode(path: Path) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode != 0o600:
        os.chmod(path, 0o600)


@dataclass
class PublicBundle:
    node_id: str
    sector: str
    kem_ek: bytes
    dsa_pk: bytes

    def as_index_record(self) -> dict:
        return {
            "node_id": self.node_id,
            "sector": self.sector,
            "ml_kem_public_key_hex": self.kem_ek.hex(),
            "ml_dsa_public_key_hex": self.dsa_pk.hex(),
            "ml_kem_public_key_bytes": len(self.kem_ek),
            "ml_dsa_public_key_bytes": len(self.dsa_pk),
        }

    def __repr__(self) -> str:
        return (
            f"PublicBundle(node_id={self.node_id!r}, sector={self.sector!r}, "
            f"kem_ek={len(self.kem_ek)} bytes, dsa_pk={len(self.dsa_pk)} bytes)"
        )


@dataclass
class NodeKeys:
    node_id: str
    sector: str
    kem_ek: bytes
    kem_dk: bytes
    dsa_pk: bytes
    dsa_sk: bytes

    def public(self) -> PublicBundle:
        return PublicBundle(self.node_id, self.sector, self.kem_ek, self.dsa_pk)

    def __repr__(self) -> str:
        return (
            f"NodeKeys(node_id={self.node_id!r}, sector={self.sector!r}, "
            f"kem_ek={len(self.kem_ek)} bytes, dsa_pk={len(self.dsa_pk)} bytes)"
        )


class KeyStore:
    def __init__(self, directory: Path | str | None = None):
        self.directory = Path(directory) if directory else KEY_DIR
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.directory, 0o700)
        except OSError:
            pass
        self.index_path = (
            Path(directory) / "index.json" if directory else PUBLIC_INDEX
        )

    def paths(self, node_id: str) -> dict[str, Path]:
        n = sanitize_node_id(node_id)
        return {
            "kem_ek": self.directory / f"{n}.kem.ek",
            "kem_dk": self.directory / f"{n}.kem.dk",
            "dsa_pk": self.directory / f"{n}.dsa.pk",
            "dsa_sk": self.directory / f"{n}.dsa.sk",
        }

    def exists(self, node_id: str) -> bool:
        p = self.paths(node_id)
        return all(path.exists() for path in p.values())

    def generate(self, node_id: str, sector: str) -> NodeKeys:
        """Create a fresh ML-KEM-768 + ML-DSA-65 keypair and store it."""
        sanitize_node_id(node_id)
        kem_ek, kem_dk = ML_KEM_768.keygen()
        dsa_pk, dsa_sk = ML_DSA_65.keygen()
        if len(kem_ek) != ML_KEM_768_EK_SIZE or len(kem_dk) != ML_KEM_768_DK_SIZE:
            raise RuntimeError("unexpected ML-KEM-768 key sizes")
        if len(dsa_pk) != ML_DSA_65_PK_SIZE or len(dsa_sk) != ML_DSA_65_SK_SIZE:
            raise RuntimeError("unexpected ML-DSA-65 key sizes")
        keys = NodeKeys(node_id, sector, kem_ek, kem_dk, dsa_pk, dsa_sk)
        self._write_keys(keys)
        log.info("[%s] Generated ML-KEM-768 + ML-DSA-65 keypair (public only logged).", node_id)
        log.info("[%s] ML-KEM ek %d B, ML-DSA pk %d B.", node_id, len(kem_ek), len(dsa_pk))
        return keys

    def load_or_create(self, node_id: str, sector: str) -> NodeKeys:
        if self.exists(node_id):
            return self.load(node_id, sector=sector)
        return self.generate(node_id, sector)

    def load(self, node_id: str, sector: str | None = None) -> NodeKeys:
        p = self.paths(node_id)
        missing = [str(path) for path in p.values() if not path.exists()]
        if missing:
            raise FileNotFoundError(f"missing key files for {node_id}: {missing}")
        for secret in (p["kem_dk"], p["dsa_sk"]):
            _ensure_secret_mode(secret)
        kem_ek = p["kem_ek"].read_bytes()
        kem_dk = p["kem_dk"].read_bytes()
        dsa_pk = p["dsa_pk"].read_bytes()
        dsa_sk = p["dsa_sk"].read_bytes()
        if len(kem_ek) != ML_KEM_768_EK_SIZE or len(kem_dk) != ML_KEM_768_DK_SIZE:
            raise ValueError(f"corrupt ML-KEM key material for {node_id}")
        if len(dsa_pk) != ML_DSA_65_PK_SIZE or len(dsa_sk) != ML_DSA_65_SK_SIZE:
            raise ValueError(f"corrupt ML-DSA key material for {node_id}")
        resolved_sector = sector or self._sector_from_index(node_id) or ""
        return NodeKeys(node_id, resolved_sector, kem_ek, kem_dk, dsa_pk, dsa_sk)

    def load_public(self, node_id: str) -> PublicBundle:
        if self.exists(node_id):
            keys = self.load(node_id)
            return keys.public()
        record = self.index().get("nodes", {}).get(node_id)
        if not record:
            raise FileNotFoundError(f"no public keys for node {node_id}")
        return PublicBundle(
            node_id=node_id,
            sector=record.get("sector", ""),
            kem_ek=bytes.fromhex(record["ml_kem_public_key_hex"]),
            dsa_pk=bytes.fromhex(record["ml_dsa_public_key_hex"]),
        )

    def index(self) -> dict:
        if not self.index_path.exists():
            return {"nodes": {}}
        return json.loads(self.index_path.read_text(encoding="utf-8"))

    def public_keys(self) -> dict[str, dict]:
        """Map node_id -> public key record, suitable for a ledger header."""
        return dict(self.index().get("nodes", {}))

    def _write_keys(self, keys: NodeKeys) -> None:
        p = self.paths(keys.node_id)
        _write_secret(p["kem_dk"], keys.kem_dk)
        _write_secret(p["dsa_sk"], keys.dsa_sk)
        _write_public(p["kem_ek"], keys.kem_ek)
        _write_public(p["dsa_pk"], keys.dsa_pk)
        self._update_index(keys.public())

    def _update_index(self, bundle: PublicBundle) -> None:
        data = self.index()
        data.setdefault("nodes", {})
        data["nodes"][bundle.node_id] = bundle.as_index_record()
        tmp = self.index_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(self.index_path)
        os.chmod(self.index_path, 0o644)

    def _sector_from_index(self, node_id: str) -> str | None:
        record = self.index().get("nodes", {}).get(node_id)
        if record:
            return record.get("sector")
        return None

#!/usr/bin/env python3
"""
lattice_keeper_v065.py
Patch ID: b3d8f407
Genesis Anchor: fd0adf90
Parent: v0.64 / 7c2f5e13
Strict Coherence Guard: Level 3 - Lineage Lock

CHANGELOG v0.64 -> v0.65 (production hardening audit):
1. FIX (High): patch_id is now INSIDE the signed message. Signed payload
        is versioned ("sigv2") and unambiguous:
            "sigv2|{fp_hex}|{genesis}|{prev_hash}|{patch_id}"
        A proof whose patch_id is changed no longer verifies. v0.64-style
        unsigned-patch_id proofs are rejected explicitly (sig_version
        field); nothing was deployed on v0.64, so no migration path is
        owed. Bridge verification updated to match.
2. FIX (High): /api/chain now walks and verifies every proof (fp, sig,
        chain hash, patch_id, genesis, link) BEFORE serving. Fails
        closed with 503 on any invalid entry. No unvalidated JSON is
        ever returned.
3. FIX (High): CORS is an explicit allowlist (LK_ALLOWED_ORIGINS,
        comma-separated). No origin header is emitted when unset —
        same-origin only. All proof/chain endpoints require a bearer
        token (LK_BRIDGE_TOKEN) when configured; without it they refuse
        to serve proofs (fail closed).
4. FIX (Medium): audit() validates the parent via an explicit lineage
        map (patch -> its actual parent), not mere registry membership.
5. FIX (Medium): Fp.from_hex uses a strict full-string policy:
        optional leading "0x" only, all hex, <= 64 chars. Mid-string
        prefixes and junk are rejected with ValueError.
6. FIX (Medium): audit status is renamed implementation_status (what
        the checks measure) and release approval is reported separately
        and honestly: it is NOT derivable from in-process checks and is
        reported as None with a note, never as an earned "approved".

Carried forward from v0.64 restyle: chain-load validation, atomic
fsync'd save, append() prev_hash guard, strict chain load, POSIX key
permission check, derived (not declared) audit status.

NOT DEPLOYED MEANS NOT BINDING: chain-hash format unchanged from
v0.64, but the sigv2 signed-payload change means v0.64-era proofs do
not verify. Start a fresh chain file.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

# ============================================================================
# Constants and lineage
# ============================================================================

GENESIS_ANCHOR = "fd0adf90"
PATCH_VERSION = "v0.65"
PATCH_ID = "b3d8f407"
PARENT_PATCH = "7c2f5e13"
SIG_VERSION = "sigv2"

KNOWN_PATCH_IDS = {
    "fd0adf90",
    "8a61c3b2",
    "9f4a7e1d",
    "4b1e9c02",
    "7c2f5e13",
    "b3d8f407",
}

# v0.65 FIX 4: explicit lineage — patch_id -> its actual parent.
# Membership in KNOWN_PATCH_IDS is not enough; the parent must be correct.
LINEAGE: Dict[str, str] = {
    "8a61c3b2": "fd0adf90",
    "9f4a7e1d": "8a61c3b2",
    "4b1e9c02": "9f4a7e1d",
    "7c2f5e13": "4b1e9c02",
    "b3d8f407": "7c2f5e13",
}

PALLAS_P_LIMBS = (
    0x992D30ED00000001,
    0x224698FC094CF91B,
    0x0000000000000000,
    0x4000000000000000,
)

PALLAS_P_HEX = (
    "40000000000000000000000000000000"
    "224698fc094cf91b"
    "992d30ed00000001"
)

DEFAULT_CHAIN_FILE = Path(os.getenv("LK_CHAIN_FILE", "./broadcast_chain.json"))
DEFAULT_KEY_FILE = Path(os.getenv("LK_KEY_FILE", "./keeper.key"))

_FP_HEX_RE = re.compile(r"^(?:0x)?[0-9a-fA-F]{1,64}$")


# ============================================================================
# Pallas field arithmetic
# ============================================================================

def compare_limbs(a, b) -> int:
    for index in range(3, -1, -1):
        if a[index] > b[index]:
            return 1
        if a[index] < b[index]:
            return -1
    return 0


def add_limbs(a, b):
    carry = 0
    result = []
    for index in range(4):
        total = a[index] + b[index] + carry
        result.append(total & 0xFFFFFFFFFFFFFFFF)
        carry = total >> 64
    return tuple(result), carry


def sub_limbs(a, b):
    borrow = 0
    result = []
    for index in range(4):
        difference = a[index] - b[index] - borrow
        if difference < 0:
            difference += 1 << 64
            borrow = 1
        else:
            borrow = 0
        result.append(difference & 0xFFFFFFFFFFFFFFFF)
    return tuple(result), borrow


@dataclass
class Fp:
    limbs: Tuple[int, int, int, int]

    def __post_init__(self):
        if len(self.limbs) != 4:
            raise ValueError("Fp requires exactly four limbs")
        if not all(0 <= value < (1 << 64) for value in self.limbs):
            raise ValueError("Fp limbs must be unsigned 64-bit integers")
        self._reduce_inplace()

    def _reduce_inplace(self):
        limbs = list(self.limbs)
        modulus = tuple(PALLAS_P_LIMBS)
        while compare_limbs(tuple(limbs), modulus) >= 0:
            reduced, _ = sub_limbs(tuple(limbs), modulus)
            limbs = list(reduced)
        object.__setattr__(self, "limbs", tuple(limbs))

    def to_hex(self) -> str:
        return "".join(f"{value:016x}" for value in reversed(self.limbs))

    def to_hex_le(self) -> str:
        return "".join(f"{value:016x}" for value in self.limbs)

    @classmethod
    def zero(cls) -> "Fp":
        return cls((0, 0, 0, 0))

    @classmethod
    def one(cls) -> "Fp":
        return cls((1, 0, 0, 0))

    @classmethod
    def from_hex(cls, hex_str: str) -> "Fp":
        """
        v0.65 FIX 5: strict full-string policy. An optional LEADING "0x"
        is allowed; a prefix embedded mid-string ("120x...") or any
        non-hex character is rejected with ValueError instead of being
        silently stripped.
        """
        if not isinstance(hex_str, str) or not _FP_HEX_RE.fullmatch(hex_str):
            raise ValueError(f"invalid Fp hex input: {hex_str!r}")
        normalized = hex_str.lower()
        if normalized.startswith("0x"):
            normalized = normalized[2:]
        normalized = normalized.zfill(64)
        limbs = [0, 0, 0, 0]
        for index in range(4):
            chunk = normalized[index * 16:(index + 1) * 16]
            limbs[3 - index] = int(chunk, 16)
        return cls(tuple(limbs))

    @classmethod
    def from_bytes_hash(cls, data: bytes) -> "Fp":
        digest = hashlib.sha256(data).digest()
        limbs = []
        for index in range(4):
            chunk = digest[index * 8:(index + 1) * 8]
            limbs.append(int.from_bytes(chunk, "little"))
        return cls(tuple(limbs))


def fp_add(a: Fp, b: Fp) -> Fp:
    result_limbs, carry = add_limbs(a.limbs, b.limbs)
    if carry or compare_limbs(result_limbs, tuple(PALLAS_P_LIMBS)) >= 0:
        result_limbs, _ = sub_limbs(result_limbs, tuple(PALLAS_P_LIMBS))
    return Fp(result_limbs)


def fp_hash_to_field(data: str) -> Fp:
    return Fp.from_bytes_hash(data.encode("utf-8"))


def validate_pallas_limbs(limbs) -> bool:
    try:
        rendered = "".join(f"{value:016x}" for value in reversed(tuple(limbs)))
    except (TypeError, ValueError):
        return False
    return rendered == PALLAS_P_HEX


def verify_genesis_anchor(patch_id: str, anchor: str) -> bool:
    return anchor == GENESIS_ANCHOR and patch_id in KNOWN_PATCH_IDS


def verify_lineage(patch_id: str, parent_id: str) -> bool:
    """v0.65 FIX 4: parent must be THIS patch's actual parent, not merely known."""
    return LINEAGE.get(patch_id) == parent_id


# ============================================================================
# Proof construction
# ============================================================================

def _compute_fp_hex(artist: str, title: str, timestamp: int, genesis: str, previous_hash: str) -> str:
    data = f"{artist}|{title}|{timestamp}|{genesis}|{previous_hash}"
    return fp_hash_to_field(data).to_hex()


def _signing_message(fp_hex: str, genesis: str, previous_hash: str, patch_id: str) -> bytes:
    """
    v0.65 FIX 1: versioned, unambiguous signed payload including patch_id.
    """
    return f"{SIG_VERSION}|{fp_hex}|{genesis}|{previous_hash}|{patch_id}".encode("utf-8")


def _compute_chain_hash(previous_hash: str, signature: str, artist: str, title: str, timestamp: int) -> str:
    chain_input = f"{previous_hash}|{signature}|{artist}|{title}|{timestamp}".encode("utf-8")
    return hashlib.sha256(chain_input).hexdigest()


# ============================================================================
# Key management
# ============================================================================

def _check_key_permissions(path: Path) -> None:
    if os.name != "posix":
        return
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise PermissionError(
            f"{path} has permissions 0o{mode:03o}; private key must not be "
            f"group/world accessible. Run: chmod 600 {path}"
        )


def load_or_create_private_key(path: Path = DEFAULT_KEY_FILE) -> Ed25519PrivateKey:
    if path.exists():
        _check_key_permissions(path)
        raw = path.read_bytes()
        if len(raw) != 32:
            raise ValueError(f"{path} must contain exactly 32 raw private-key bytes")
        return Ed25519PrivateKey.from_private_bytes(raw)

    path.parent.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path.write_bytes(raw)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    logging.info("[Key] Generated new Ed25519 keypair at %s", path)
    return key


# ============================================================================
# Broadcast proof
# ============================================================================

@dataclass
class BroadcastProof:
    title: str
    artist: str
    timestamp: int
    fp_element: str
    signature: str
    public_key: str
    prev_hash: str
    proof_chain_hash: str
    genesis_anchor: str
    patch_id: str
    sig_version: str = SIG_VERSION
    sig_alg: str = "Ed25519"

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> "BroadcastProof":
        values = dict(data)
        values.setdefault("sig_alg", "Ed25519")
        if "public_key" not in values:
            raise ValueError("Cannot load pre-v0.63 proof: public_key is missing")
        sig_version = values.get("sig_version")
        if sig_version != SIG_VERSION:
            # v0.64-era proofs (unsigned patch_id) are rejected explicitly.
            raise ValueError(
                f"unsupported sig_version {sig_version!r}; "
                f"this build verifies {SIG_VERSION} payloads only "
                f"(patch_id must be signed)"
            )
        return cls(**values)

    def to_metadata(self) -> Dict:
        return {
            "song": f"{self.artist} - {self.title}",
            "X-Lattice-Signature": self.signature,
            "X-Lattice-PublicKey": self.public_key,
            "X-Lattice-Fp": self.fp_element[:16],
            "X-Lattice-Chain": self.proof_chain_hash[:16],
            "X-Lattice-Genesis": self.genesis_anchor,
            "X-Lattice-Timestamp": str(self.timestamp),
            "X-Lattice-PatchId": self.patch_id,
            "X-Lattice-SigVersion": self.sig_version,
            "X-Lattice-SigAlg": self.sig_alg,
        }

    def to_liquidsoap_cmd(self) -> str:
        song = f"{self.artist} - {self.title}".replace("\\", "\\\\").replace('"', '\\"')
        return (
            f'var.set now_playing = "{song}"\n'
            f'var.set lattice_sig = "{self.signature}"\n'
            f'var.set lattice_pk = "{self.public_key}"\n'
            f'var.set lattice_chain = "{self.proof_chain_hash[:16]}"'
        )


# ============================================================================
# Chain store
# ============================================================================

class ChainStore:
    def __init__(self, path: Path = DEFAULT_CHAIN_FILE):
        self.path = path

    def load(self) -> Dict:
        if not self.path.exists():
            return {"current_hash": None, "chain": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("chain file root must be an object")
            if not isinstance(data.get("chain", []), list):
                raise ValueError("chain field must be a list")
            return data
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Failed to load chain {self.path}: {exc}") from exc

    def save(self, state: Dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=self.path.parent,
            prefix=f".{self.path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(state, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.replace(temporary_path, self.path)
        finally:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


# ============================================================================
# Broadcast chain
# ============================================================================

@dataclass
class BroadcastChain:
    genesis_anchor: str = GENESIS_ANCHOR
    chain: List[BroadcastProof] = field(default_factory=list)
    current_hash: str = ""
    _store: ChainStore = field(default_factory=ChainStore, repr=False)

    def __post_init__(self):
        state = self._store.load()
        stored_chain = state.get("chain", [])
        if stored_chain:
            self.chain = [BroadcastProof.from_dict(item) for item in stored_chain]
            self._validate_loaded_chain()
            expected_head = self._expected_head()
            stored_head = state.get("current_hash")
            if stored_head != expected_head:
                raise ValueError("stored current_hash does not match the recomputed chain head")
            self.current_hash = expected_head
            logging.info(
                "[BroadcastChain] Resumed: %d proofs, head=%s...",
                len(self.chain), self.current_hash[:16],
            )
        else:
            self.current_hash = hashlib.sha256(self.genesis_anchor.encode("utf-8")).hexdigest()
            logging.info("[BroadcastChain] Fresh chain, genesis=%s...", self.current_hash[:16])

    def _expected_head(self) -> str:
        current = hashlib.sha256(self.genesis_anchor.encode("utf-8")).hexdigest()
        for proof in self.chain:
            current = proof.proof_chain_hash
        return current

    def _validate_loaded_chain(self) -> None:
        expected_previous = hashlib.sha256(self.genesis_anchor.encode("utf-8")).hexdigest()
        for index, proof in enumerate(self.chain):
            if proof.prev_hash != expected_previous:
                raise ValueError(f"chain break at index {index}: prev_hash mismatch")
            expected_previous = proof.proof_chain_hash

    def append(self, proof: BroadcastProof) -> None:
        if proof.prev_hash != self.current_hash:
            raise ValueError("proof prev_hash does not match current chain head")
        self.chain.append(proof)
        self.current_hash = proof.proof_chain_hash
        self._store.save({
            "current_hash": self.current_hash,
            "chain": [p.to_dict() for p in self.chain],
        })


# ============================================================================
# SoulForge broadcast keeper
# ============================================================================

class SoulForgeBroadcastKeeper:
    """Signs broadcast metadata with Ed25519 (sigv2 payloads) and chains proofs."""

    def __init__(
        self,
        genesis_anchor: str = GENESIS_ANCHOR,
        store: Optional[ChainStore] = None,
        key_path: Path = DEFAULT_KEY_FILE,
    ):
        self.genesis_anchor = genesis_anchor
        self.patch_id = PATCH_ID
        self.version = PATCH_VERSION

        self.private_key = load_or_create_private_key(key_path)
        self.public_key_bytes = self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.public_key_hex = self.public_key_bytes.hex()

        self.chain = BroadcastChain(
            genesis_anchor=genesis_anchor,
            _store=store or ChainStore(),
        )

    def sign_broadcast(self, title: str, artist: str = "SOUL FORGE") -> BroadcastProof:
        timestamp = int(time.time())
        previous_hash = self.chain.current_hash

        fp_hex = _compute_fp_hex(artist, title, timestamp, self.genesis_anchor, previous_hash)
        message = _signing_message(fp_hex, self.genesis_anchor, previous_hash, self.patch_id)
        signature = self.private_key.sign(message).hex()
        chain_hash = _compute_chain_hash(previous_hash, signature, artist, title, timestamp)

        proof = BroadcastProof(
            title=title,
            artist=artist,
            timestamp=timestamp,
            fp_element=fp_hex,
            signature=signature,
            public_key=self.public_key_hex,
            prev_hash=previous_hash,
            proof_chain_hash=chain_hash,
            genesis_anchor=self.genesis_anchor,
            patch_id=self.patch_id,
            sig_version=SIG_VERSION,
        )
        self.chain.append(proof)

        logging.info(
            "[BROADCAST PROOF] %s - %s | Fp:%s... sig:%s... pk:%s...",
            artist, title, fp_hex[:16], signature[:16], self.public_key_hex[:16],
        )
        return proof

    def verify_proof(self, proof: BroadcastProof) -> Tuple[bool, str]:
        if proof.sig_alg != "Ed25519":
            return False, "unsupported signature algorithm"
        if proof.sig_version != SIG_VERSION:
            return False, f"unsupported sig_version: {proof.sig_version!r}"
        if len(proof.fp_element) != 64:
            return False, "invalid fp_element length"
        if len(proof.signature) != 128:
            return False, "invalid signature length"
        if len(proof.public_key) != 64:
            return False, "invalid public_key length"
        if proof.public_key != self.public_key_hex:
            return False, "public_key mismatch"

        fp_hex = _compute_fp_hex(
            proof.artist, proof.title, proof.timestamp,
            proof.genesis_anchor, proof.prev_hash,
        )
        if fp_hex != proof.fp_element:
            return False, "fp_element mismatch"

        # v0.65 FIX 1: patch_id is inside the signed message. Swapping it
        # for another known ID invalidates the signature.
        message = _signing_message(fp_hex, proof.genesis_anchor, proof.prev_hash, proof.patch_id)
        try:
            public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(proof.public_key))
            public_key.verify(bytes.fromhex(proof.signature), message)
        except (ValueError, InvalidSignature) as exc:
            return False, f"signature verification failed: {type(exc).__name__}"

        expected_chain_hash = _compute_chain_hash(
            proof.prev_hash, proof.signature, proof.artist, proof.title, proof.timestamp,
        )
        if expected_chain_hash != proof.proof_chain_hash:
            return False, "proof_chain_hash mismatch"

        if proof.genesis_anchor != self.genesis_anchor:
            return False, "genesis_anchor mismatch"
        if proof.patch_id not in KNOWN_PATCH_IDS:
            return False, f"unknown patch_id: {proof.patch_id}"

        return True, "proof valid"

    def verify_chain(self) -> Tuple[bool, str]:
        expected_previous = hashlib.sha256(self.genesis_anchor.encode("utf-8")).hexdigest()
        for index, proof in enumerate(self.chain.chain):
            if proof.prev_hash != expected_previous:
                return False, f"chain break at index {index}: prev_hash mismatch"
            valid, reason = self.verify_proof(proof)
            if not valid:
                return False, f"proof {index} invalid: {reason}"
            expected_previous = proof.proof_chain_hash
        if self.chain.current_hash != expected_previous:
            return False, "chain head mismatch"
        return True, f"chain valid ({len(self.chain.chain)} proofs)"

    def get_chain_state(self) -> Dict:
        return {
            "genesis_anchor": self.genesis_anchor,
            "patch_id": self.patch_id,
            "version": self.version,
            "sig_version": SIG_VERSION,
            "public_key": self.public_key_hex,
            "chain_length": len(self.chain.chain),
            "current_hash": self.chain.current_hash,
            "last_proof": self.chain.chain[-1].to_metadata() if self.chain.chain else None,
        }

    def generate_bridge_code(self) -> str:
        """
        Node.js bridge v0.65 — verification-only.
        v0.65: sigv2 payload (patch_id signed), /api/chain fails closed,
        CORS allowlist, optional bearer-token auth on proof/chain routes.
        """
        public_key_hex = self.public_key_hex
        genesis_anchor = self.genesis_anchor
        patch_id = self.patch_id
        return f'''// SOUL FORGE BROADCAST PROOF BRIDGE — v0.65
// Verification-only. Holds only the PUBLIC key.
"use strict";

const express = require("express");
const cors = require("cors");
const net = require("net");
const crypto = require("crypto");
const fs = require("fs");

const app = express();

const LIQUIDSOAP_HOST = process.env.LIQ_HOST || "localhost";
const LIQUIDSOAP_PORT = parseInt(process.env.LIQ_PORT || "1234", 10);
const GENESIS_ANCHOR = "{genesis_anchor}";
const CHAIN_FILE = process.env.LK_CHAIN_FILE || "./broadcast_chain.json";
const PUBLIC_KEY_HEX = process.env.LK_PUBLIC_KEY || "{public_key_hex}";
const PATCH_ID = "{patch_id}";
const SIG_VERSION = "sigv2";

// v0.65 FIX 3: CORS allowlist. Unset => no CORS header (same-origin only).
const ALLOWED_ORIGINS = (process.env.LK_ALLOWED_ORIGINS || "")
    .split(",")
    .map(s => s.trim())
    .filter(Boolean);

// v0.65 FIX 3: bearer token. Unset => proof/chain endpoints refuse to serve.
const BRIDGE_TOKEN = process.env.LK_BRIDGE_TOKEN || "";

if (ALLOWED_ORIGINS.length > 0) {{
    app.use(cors({{ origin: (origin, cb) => {{
        if (!origin || ALLOWED_ORIGINS.includes(origin)) return cb(null, true);
        cb(new Error("origin not allowed"));
    }}}}));
}}

app.use(express.json());

function requireToken(request, response, next) {{
    if (!BRIDGE_TOKEN) {{
        return response.status(503).send({{
            ok: false,
            reason: "bridge token not configured; serving proofs refused"
        }});
    }}
    const header = request.get("authorization") || "";
    if (header !== `Bearer ${{BRIDGE_TOKEN}}`) {{
        return response.status(401).send({{ ok: false, reason: "unauthorized" }});
    }}
    next();
}}

const KNOWN_PATCH_IDS = new Set([
    "fd0adf90", "8a61c3b2", "9f4a7e1d",
    "4b1e9c02", "7c2f5e13", "b3d8f407"
]);

const PALLAS_P = [
    BigInt("0x992d30ed00000001"),
    BigInt("0x224698fc094cf91b"),
    BigInt("0x0000000000000000"),
    BigInt("0x4000000000000000")
];
const MASK64 = (1n << 64n) - 1n;

function compareLimbs(a, b) {{
    for (let i = 3; i >= 0; i--) {{
        if (a[i] > b[i]) return 1;
        if (a[i] < b[i]) return -1;
    }}
    return 0;
}}

function subLimbs(a, b) {{
    let borrow = 0n;
    const result = [];
    for (let i = 0; i < 4; i++) {{
        let diff = a[i] - b[i] - borrow;
        if (diff < 0n) {{ diff += (1n << 64n); borrow = 1n; }}
        else borrow = 0n;
        result.push(diff & MASK64);
    }}
    return result;
}}

function fpReduceBytes(buffer32) {{
    const limbs = [];
    for (let i = 0; i < 4; i++) {{
        const low = BigInt(buffer32.readUInt32LE(i * 8));
        const high = BigInt(buffer32.readUInt32LE(i * 8 + 4));
        limbs.push(low | (high << 32n));
    }}
    while (compareLimbs(limbs, PALLAS_P) >= 0) {{
        const reduced = subLimbs(limbs, PALLAS_P);
        for (let i = 0; i < 4; i++) limbs[i] = reduced[i];
    }}
    return [...limbs].reverse()
        .map(v => v.toString(16).padStart(16, "0")).join("");
}}

function fpHash(data) {{
    return fpReduceBytes(crypto.createHash("sha256").update(data).digest());
}}

function rawPubKeyToSpki(rawHex) {{
    const raw = Buffer.from(rawHex, "hex");
    const prefix = Buffer.from("302a300506032b6570032100", "hex");
    return Buffer.concat([prefix, raw]);
}}

// v0.65 FIX 1: sigv2 message — patch_id is signed.
function verifySignature(fpHex, genesis, previousHash, patchId, signatureHex) {{
    const message = Buffer.from(
        `${{SIG_VERSION}}|${{fpHex}}|${{genesis}}|${{previousHash}}|${{patchId}}`,
        "utf8"
    );
    const signature = Buffer.from(signatureHex, "hex");
    const spki = rawPubKeyToSpki(PUBLIC_KEY_HEX);
    const key = crypto.createPublicKey({{ key: spki, format: "der", type: "spki" }});
    return crypto.verify(null, message, key, signature);
}}

function loadChain() {{
    try {{
        if (fs.existsSync(CHAIN_FILE)) {{
            return JSON.parse(fs.readFileSync(CHAIN_FILE, "utf8"));
        }}
    }} catch (error) {{
        console.warn("[ChainStore] Load failed:", error.message);
    }}
    return {{
        current_hash: crypto.createHash("sha256").update(GENESIS_ANCHOR).digest("hex"),
        chain: []
    }};
}}

let chainState = loadChain();

function verifyProof(proof) {{
    if (proof.sig_version !== SIG_VERSION) {{
        return {{ ok: false, reason: "unsupported sig_version" }};
    }}
    if (proof.public_key !== PUBLIC_KEY_HEX) {{
        return {{ ok: false, reason: "public key mismatch" }};
    }}
    const fpInput =
        `${{proof.artist}}|${{proof.title}}|` +
        `${{proof.timestamp}}|${{proof.genesis_anchor}}|` +
        `${{proof.prev_hash}}`;
    const fpHex = fpHash(fpInput);
    if (fpHex !== proof.fp_element) {{
        return {{ ok: false, reason: "fp_element mismatch" }};
    }}
    if (!verifySignature(fpHex, proof.genesis_anchor, proof.prev_hash,
                          proof.patch_id, proof.signature)) {{
        return {{ ok: false, reason: "signature verification failed" }};
    }}
    const chainInput =
        `${{proof.prev_hash}}|${{proof.signature}}|` +
        `${{proof.artist}}|${{proof.title}}|` +
        `${{proof.timestamp}}`;
    const chainHash = crypto.createHash("sha256").update(chainInput).digest("hex");
    if (chainHash !== proof.proof_chain_hash) {{
        return {{ ok: false, reason: "chain_hash mismatch" }};
    }}
    if (!KNOWN_PATCH_IDS.has(proof.patch_id)) {{
        return {{ ok: false, reason: "unknown patch_id" }};
    }}
    if (proof.genesis_anchor !== GENESIS_ANCHOR) {{
        return {{ ok: false, reason: "genesis mismatch" }};
    }}
    return {{ ok: true, reason: "proof valid" }};
}}

// v0.65 FIX 2: full chain walk before serving. Fails closed.
function verifyWholeChain() {{
    const genesisHash = crypto.createHash("sha256")
        .update(GENESIS_ANCHOR).digest("hex");
    let expected = genesisHash;
    for (let i = 0; i < chainState.chain.length; i++) {{
        const proof = chainState.chain[i];
        if (proof.prev_hash !== expected) {{
            return {{ ok: false, reason: `chain break at index ${{i}}` }};
        }}
        const result = verifyProof(proof);
        if (!result.ok) {{
            return {{ ok: false, reason: `proof ${{i}} invalid: ${{result.reason}}` }};
        }}
        expected = proof.proof_chain_hash;
    }}
    return {{ ok: true, reason: `chain valid (${{chainState.chain.length}} proofs)` }};
}}

function telnetCmd(command) {{
    return new Promise((resolve, reject) => {{
        const client = new net.Socket();
        let settled = false;
        let data = "";
        const timer = setTimeout(() => {{
            finish(new Error("Liquidsoap timeout"), data);
        }}, 1000);
        function finish(error, result) {{
            if (settled) return;
            settled = true;
            clearTimeout(timer);
            client.destroy();
            if (error) reject(error);
            else resolve(result);
        }}
        client.connect(LIQUIDSOAP_PORT, LIQUIDSOAP_HOST,
            () => client.write(command + "\\n"));
        client.on("data", chunk => {{ data += chunk; }});
        client.on("close", () => finish(null, data));
        client.on("error", error => finish(error, data));
    }});
}}

app.get("/admin/metadata", requireToken, (request, response) => {{
    const proofB64 = request.query.proof_b64;
    if (!proofB64) {{
        return response.status(400).send({{ ok: false, reason: "no proof supplied" }});
    }}
    let proof;
    try {{
        proof = JSON.parse(Buffer.from(proofB64, "base64").toString("utf8"));
    }} catch (error) {{
        return response.status(400).send({{ ok: false, reason: "invalid proof encoding" }});
    }}
    const result = verifyProof(proof);
    return response.status(result.ok ? 200 : 400).send(result);
}});

app.post("/api/verify", requireToken, (request, response) => {{
    const result = verifyProof(request.body);
    return response.status(result.ok ? 200 : 400).send(result);
}});

// v0.65 FIX 2: no unvalidated chain data is ever returned.
app.get("/api/chain", requireToken, (request, response) => {{
    const chainCheck = verifyWholeChain();
    if (!chainCheck.ok) {{
        return response.status(503).send({{
            ok: false, reason: `chain invalid: ${{chainCheck.reason}}`
        }});
    }}
    return response.send({{
        genesis_anchor: GENESIS_ANCHOR,
        public_key: PUBLIC_KEY_HEX,
        length: chainState.chain.length,
        chain: chainState.chain.slice(-20)
    }});
}});

const PORT = process.env.PORT || 3001;
app.listen(PORT, () => {{
    console.log("Soul Forge Broadcast Proof Bridge v0.65");
    console.log(`Genesis: ${{GENESIS_ANCHOR}}`);
    console.log(`Public key: ${{PUBLIC_KEY_HEX}}`);
    console.log(`Chain file: ${{CHAIN_FILE}}`);
    console.log(`CORS allowlist: ${{ALLOWED_ORIGINS.length ? ALLOWED_ORIGINS.join(", ") : "(same-origin only)"}}`);
    console.log(`Auth: ${{BRIDGE_TOKEN ? "bearer token required" : "NOT CONFIGURED — proof routes refuse to serve"}}`);
    console.log("Signing: DISABLED");
    console.log("Verification: ENABLED");
}});
'''


# ============================================================================
# LatticeKeeperV065
# ============================================================================

class LatticeKeeperV065:
    def __init__(self):
        self.genesis_anchor = GENESIS_ANCHOR
        self.patch_id = PATCH_ID
        self.parent_patch = PARENT_PATCH
        self.version = PATCH_VERSION
        self.broadcast_keeper = SoulForgeBroadcastKeeper()

    def audit(self) -> Dict:
        """
        v0.65 FIX 6: implementation_status is what the in-process checks
        measure. Release approval is NOT derivable from these checks and
        is reported as None with a note — never as an earned verdict.
        """
        chain_ok, chain_message = self.broadcast_keeper.verify_chain()
        genesis_ok = verify_genesis_anchor(self.patch_id, self.genesis_anchor)
        # v0.65 FIX 4: lineage — parent must be THIS patch's actual parent.
        lineage_ok = verify_lineage(self.patch_id, self.parent_patch)
        limbs_ok = validate_pallas_limbs(PALLAS_P_LIMBS)

        measured_checks = [
            ("genesis_anchor_verified", genesis_ok),
            ("lineage_verified", lineage_ok),
            ("pallas_limbs_validated", limbs_ok),
            ("chain_valid", chain_ok),
        ]

        implementation_status = (
            "SHIPABLE" if all(value for _, value in measured_checks)
            else "NOT_SHIPABLE"
        )

        return {
            **{name: value for name, value in measured_checks},
            "version": self.version,
            "patch_id": self.patch_id,
            "signature_alg": "Ed25519",
            "sig_version": SIG_VERSION,
            "public_key": self.broadcast_keeper.public_key_hex,
            "chain_message": chain_message,
            "chain_length": len(self.broadcast_keeper.chain.chain),
            "current_chain_hash": self.broadcast_keeper.chain.current_hash,
            "endianness": "little-endian (limbs[0] = LSB)",
            "implementation_status": implementation_status,
            "release_approved": None,
            "release_note": (
                "release approval requires deployment review "
                "(environment, secrets, threat model); it is not "
                "derivable from in-process checks"
            ),
        }


# ============================================================================
# Self-tests
# ============================================================================

def run_self_tests() -> bool:
    print("\n" + "=" * 60)
    print("LATTICE KEEPER v0.65 SELF-TESTS")
    print("=" * 60)

    with tempfile.TemporaryDirectory(prefix="lk_v065_") as tmp:
        tmp = Path(tmp)
        key_path = tmp / "keeper.key"

        print("\nTest 1: Pallas limbs valid")
        assert validate_pallas_limbs(PALLAS_P_LIMBS)
        print("  passed")

        print("Test 2: Fp add and reduction")
        assert fp_add(Fp((1, 0, 0, 0)), Fp((2, 0, 0, 0))).limbs[0] == 3
        p_minus_one = Fp((PALLAS_P_LIMBS[0] - 1, *PALLAS_P_LIMBS[1:]))
        assert fp_add(p_minus_one, Fp.one()).limbs == (0, 0, 0, 0)
        print("  passed")

        print("Test 3: Fp hex round-trip")
        field_value = fp_hash_to_field("ROUND TRIP PROBE")
        assert Fp.from_hex(field_value.to_hex()).limbs == field_value.limbs
        assert Fp.from_hex("0x" + field_value.to_hex()).limbs == field_value.limbs
        print("  passed")

        print("Test 4: v0.65 FIX 5 — from_hex rejects mid-string prefix and junk")
        for bad in ("120x" + "0" * 60, "zz" * 32, "0x" + "g" * 8, "12 34"):
            try:
                Fp.from_hex(bad)
                raise AssertionError(f"from_hex should reject {bad!r}")
            except ValueError:
                pass
        print("  passed")

        print("Test 5: deterministic field hashing")
        first = fp_hash_to_field("MOYSES - TRLY LOOP")
        second = fp_hash_to_field("MOYSES - TRLY LOOP")
        assert first.to_hex() == second.to_hex()
        assert first.to_hex() != fp_hash_to_field("DIFFERENT").to_hex()
        print("  passed")

        print("Test 6: v0.65 FIX 4 — lineage requires the correct parent")
        assert verify_lineage(PATCH_ID, PARENT_PATCH)
        assert verify_lineage("7c2f5e13", "4b1e9c02")
        assert not verify_lineage(PATCH_ID, "fd0adf90"), \
            "wrong parent must fail lineage even if it is a known/genesis id"
        print("  passed")

        print("Test 7: unknown patch rejected")
        assert not verify_genesis_anchor("deadbeef", GENESIS_ANCHOR)
        print("  passed")

        class MemoryStore(ChainStore):
            def __init__(self):
                self.state = {"current_hash": None, "chain": []}
            def load(self):
                return self.state
            def save(self, state):
                self.state = state

        keeper = SoulForgeBroadcastKeeper(store=MemoryStore(), key_path=key_path)

        print("Test 8: unsafe private-key permissions rejected")
        bad_key_path = tmp / "bad.key"
        bad_key_path.write_bytes(key_path.read_bytes())
        if os.name == "posix":
            bad_key_path.chmod(0o644)
            try:
                load_or_create_private_key(bad_key_path)
                raise AssertionError("world-readable key should be rejected")
            except PermissionError:
                print("  passed")
        else:
            print("  skipped on non-POSIX platform")

        print("Test 9: real Ed25519 signature (sigv2)")
        proof_one = keeper.sign_broadcast("TRLY LOOP", "MOYSES")
        assert len(proof_one.signature) == 128
        assert len(proof_one.public_key) == 64
        assert proof_one.sig_version == "sigv2"
        print("  passed")

        print("Test 10: v0.65 FIX 1 — patch_id tampering invalidates signature")
        swapped_id = replace(proof_one, patch_id="9f4a7e1d")  # another KNOWN id
        valid, reason = keeper.verify_proof(swapped_id)
        assert not valid, "swapping patch_id to a known id must fail signature"
        assert "signature" in reason
        print(f"  passed: {reason}")

        print("Test 11: v0.65 FIX 1 — sigv1-style (unsigned patch_id) proof rejected")
        legacy_message = f"{proof_one.fp_element}|{proof_one.genesis_anchor}|{proof_one.prev_hash}".encode()
        legacy_sig = keeper.private_key.sign(legacy_message).hex()
        legacy_proof = replace(proof_one, signature=legacy_sig, sig_version="sigv1")
        valid, reason = keeper.verify_proof(legacy_proof)
        assert not valid
        print(f"  passed: {reason}")

        print("Test 12: chain linking and verification")
        proof_two = keeper.sign_broadcast("BEMMMM", "BRITTY")
        assert proof_two.prev_hash == proof_one.proof_chain_hash
        keeper.sign_broadcast("XIBA", "MNGOOA")
        valid, reason = keeper.verify_chain()
        assert valid, reason
        print(f"  passed: {reason}")

        print("Test 13: proof verification (happy path)")
        valid, reason = keeper.verify_proof(proof_one)
        assert valid, reason
        print(f"  passed: {reason}")

        print("Test 14: artist tampering rejected")
        valid, reason = keeper.verify_proof(replace(proof_one, artist="IMPOSTOR"))
        assert not valid
        print(f"  passed: {reason}")

        print("Test 15: title tampering rejected")
        valid, reason = keeper.verify_proof(replace(proof_one, title="TAMPERED"))
        assert not valid
        print(f"  passed: {reason}")

        print("Test 16: signature tampering rejected")
        valid, reason = keeper.verify_proof(replace(proof_one, signature="ff" * 64))
        assert not valid
        print(f"  passed: {reason}")

        print("Test 17: public-key tampering rejected")
        other_pk = Ed25519PrivateKey.generate().public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ).hex()
        valid, reason = keeper.verify_proof(replace(proof_one, public_key=other_pk))
        assert not valid
        print(f"  passed: {reason}")

        print("Test 18: forged signature rejected")
        forged = replace(
            proof_one,
            signature=hashlib.sha256(proof_one.fp_element.encode()).hexdigest() + "00" * 32,
        )
        valid, reason = keeper.verify_proof(forged)
        assert not valid
        print(f"  passed: {reason}")

        print("Test 19: Liquidsoap command and metadata carry proof")
        cmd = proof_one.to_liquidsoap_cmd()
        assert proof_one.signature in cmd and proof_one.public_key in cmd
        meta = proof_one.to_metadata()
        assert meta["X-Lattice-SigVersion"] == "sigv2"
        print("  passed")

        print("Test 20: serialization round-trip; legacy dict rejected")
        restored = BroadcastProof.from_dict(proof_one.to_dict())
        assert restored.signature == proof_one.signature
        legacy_dict = proof_one.to_dict()
        del legacy_dict["sig_version"]
        try:
            BroadcastProof.from_dict(legacy_dict)
            raise AssertionError("pre-sigv2 dict should be rejected")
        except ValueError:
            print("  passed")

        print("Test 21: derived implementation status; wrong lineage fails audit")
        audit = LatticeKeeperV065.__new__(LatticeKeeperV065)
        audit.genesis_anchor = GENESIS_ANCHOR
        audit.patch_id = PATCH_ID
        audit.parent_patch = PARENT_PATCH
        audit.version = PATCH_VERSION
        audit.broadcast_keeper = keeper

        report = audit.audit()
        assert report["implementation_status"] == "SHIPABLE"
        assert report["release_approved"] is None, \
            "release approval must never be self-awarded"

        audit.parent_patch = "fd0adf90"  # wrong parent: known, but incorrect
        bad_report = audit.audit()
        assert bad_report["implementation_status"] == "NOT_SHIPABLE"
        assert bad_report["lineage_verified"] is False
        audit.parent_patch = PARENT_PATCH

        keeper.chain.chain[0] = replace(keeper.chain.chain[0], title="HACKED")
        tampered_report = audit.audit()
        assert tampered_report["implementation_status"] == "NOT_SHIPABLE"
        keeper.chain.chain[0] = replace(keeper.chain.chain[0], title="TRLY LOOP")
        print("  passed")

        print("Test 22: v0.65 FIX 2/3 — bridge fails closed, CORS allowlist, sigv2")
        bridge = keeper.generate_bridge_code()
        assert "verifyWholeChain" in bridge, "chain endpoint must verify before serving"
        assert "503" in bridge, "chain endpoint must fail closed"
        assert "LK_ALLOWED_ORIGINS" in bridge, "CORS must be an allowlist"
        assert "requireToken" in bridge, "proof routes must be gated"
        assert "sigv2" in bridge and "proof.patch_id" in bridge, \
            "bridge must sign/verify patch_id in sigv2 payload"
        assert "clearTimeout" in bridge and "client.destroy" in bridge
        print("  passed")

    print("\n" + "=" * 60)
    print("ALL v0.65 SELF-TESTS PASSED")
    print("=" * 60)
    return True


# ============================================================================
# Main
# ============================================================================

def main() -> int:
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
    keeper = LatticeKeeperV065()
    broadcast_keeper = keeper.broadcast_keeper

    print("=" * 60)
    print(f"LATTICE KEEPER {PATCH_VERSION}")
    print(f"Genesis: {GENESIS_ANCHOR}  Parent: {PARENT_PATCH} -> {PATCH_ID}")
    print(f"Public key: {broadcast_keeper.public_key_hex}")
    print("=" * 60)

    for title, artist in [("TRLY LOOP", "MOYSES"), ("BEMMMM", "BRITTY"), ("XIBA", "MNGOOA")]:
        proof = broadcast_keeper.sign_broadcast(title, artist)
        print(f"  {proof.artist} - {proof.title} | "
              f"sig:{proof.signature[:16]}... chain:{proof.proof_chain_hash[:8]}...")

    chain_valid, chain_message = broadcast_keeper.verify_chain()
    print(f"\nChain verify: {chain_message}")
    print(f"\nAudit: {json.dumps(keeper.audit(), indent=2)}")
    return 0 if chain_valid else 1


if __name__ == "__main__":
    if run_self_tests():
        print("\nINITIALIZING DEPLOYMENT\n")
        raise SystemExit(main())
    raise SystemExit(1)
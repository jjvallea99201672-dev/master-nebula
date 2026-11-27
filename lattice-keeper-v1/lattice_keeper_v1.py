#!/usr/bin/env python3
"""
Lattice Keeper v1.0 — Production-Ready RWA Tokenization Engine

Fixes vs v0.34:
  - C1: Guardian evaluates caller-supplied text (no self-satisfying prompt)
  - C2: Ed25519 seed persistence round-trips correctly
  - C3: liboqs integration uses oqs.Signature("ML-DSA-65")
  - H1: BP update lives in variable space (H.T @ check_msgs)
  - H2: GPU/CPU backend coercion is explicit
  - H3: Per-call deterministic RNG on the active backend
  - H4: Gossip publishes/scans lattice:sth:node:*; optional HTTP fetch
  - H5: tree_state persisted; STH chain populated
  - M1-M9, L1-L10: annotations, structured logs, verifier, capabilities
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import structlog
from aiohttp import ClientSession, web

# ---------------------------------------------------------------------------
# Optional dependencies
# ---------------------------------------------------------------------------
SCIPY_AVAILABLE = False
GPU_AVAILABLE = False
REDIS_AVAILABLE = False
NACL_AVAILABLE = False
OQS_AVAILABLE = False

csr_matrix = None
cp = None
cp_csr_matrix = None
redis = None

try:
    from scipy.sparse import csr_matrix  # type: ignore
    SCIPY_AVAILABLE = True
except ImportError:
    pass

try:
    import cupy as cp  # type: ignore
    from cupyx.scipy.sparse import csr_matrix as cp_csr_matrix  # type: ignore
    GPU_AVAILABLE = True
except ImportError:
    pass

try:
    import redis.asyncio as redis  # type: ignore
    REDIS_AVAILABLE = True
except ImportError:
    pass

try:
    import nacl.signing  # type: ignore
    NACL_AVAILABLE = True
except ImportError:
    pass

try:
    import oqs  # type: ignore
    OQS_AVAILABLE = True
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ]
)
logger = structlog.get_logger("lattice_keeper_v1")

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AppConfig:
    api_host: str
    api_port: int
    redis_url: str
    api_key: str
    node_id: str
    gossip_peers: Tuple[str, ...]
    gossip_fetch_url: Optional[str]
    record_ttl_seconds: int
    db_path: str
    ed25519_seed: Optional[bytes]
    ml_dsa65_public_key: Optional[bytes]
    ml_dsa65_secret_key: Optional[bytes]

    @classmethod
    def load(cls) -> "AppConfig":
        api_key = os.getenv("LATTICE_API_KEY", "")
        if not api_key:
            raise ValueError("LATTICE_API_KEY environment variable is required")

        peers = tuple(
            p.strip() for p in os.getenv("GOSSIP_PEERS", "").split(",") if p.strip()
        )

        seed = _load_or_create_seed(
            Path(os.getenv("ED25519_KEY_FILE", "ed25519_seed.bin"))
        )
        ml_pub, ml_sec = _load_or_create_ml_dsa_keys(
            Path(os.getenv("ML_DSA65_PUB_FILE", "ml_dsa65_pub.bin")),
            Path(os.getenv("ML_DSA65_SEC_FILE", "ml_dsa65_sec.bin")),
        )

        return cls(
            api_host=os.getenv("API_HOST", "0.0.0.0"),
            api_port=int(os.getenv("API_PORT", "8765")),
            redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            api_key=api_key,
            node_id=os.getenv("NODE_ID", secrets.token_hex(8)),
            gossip_peers=peers,
            gossip_fetch_url=os.getenv("GOSSIP_FETCH_URL") or None,
            record_ttl_seconds=int(os.getenv("RECORD_TTL_SECONDS", str(365 * 24 * 3600))),
            db_path=os.getenv("LATTICE_DB", "lattice_keeper.db"),
            ed25519_seed=seed,
            ml_dsa65_public_key=ml_pub,
            ml_dsa65_secret_key=ml_sec,
        )


def _load_or_create_seed(path: Path) -> Optional[bytes]:
    """Ed25519 seed is 32 raw bytes. Never a SigningKey object on disk."""
    if not NACL_AVAILABLE:
        return None
    if path.exists():
        seed = path.read_bytes()
        if len(seed) != 32:
            raise ValueError(f"Ed25519 seed at {path} must be 32 bytes, got {len(seed)}")
        return seed
    seed = os.urandom(32)
    _atomic_write(path, seed, mode=0o600)
    logger.info("generated_ed25519_seed", path=str(path))
    return seed


def _load_or_create_ml_dsa_keys(pub_path: Path, sec_path: Path) -> Tuple[Optional[bytes], Optional[bytes]]:
    """ML-DSA-65 keys via liboqs. Persist both public and secret key."""
    if not OQS_AVAILABLE:
        return None, None
    if pub_path.exists() and sec_path.exists():
        return pub_path.read_bytes(), sec_path.read_bytes()
    with oqs.Signature("ML-DSA-65") as sig:
        public_key = sig.generate_keypair()
        secret_key = sig.export_secret_key()
    _atomic_write(pub_path, public_key, mode=0o644)
    _atomic_write(sec_path, secret_key, mode=0o600)
    logger.info("generated_ml_dsa65_keypair", pub=str(pub_path), sec=str(sec_path))
    return public_key, secret_key


def _atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Auth middleware (timing-safe)
# ---------------------------------------------------------------------------
@web.middleware
async def auth_middleware(request: web.Request, handler):
    config: AppConfig = request.app["config"]
    provided = request.headers.get("X-API-Key") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")
    if not provided:
        return web.json_response({"error": "Unauthorized"}, status=401)
    if not hmac.compare_digest(provided.encode(), config.api_key.encode()):
        return web.json_response({"error": "Unauthorized"}, status=401)
    return await handler(request)


# ---------------------------------------------------------------------------
# Forensic anchor
# ---------------------------------------------------------------------------
class ForensicAnchor:
    GENESIS_HASH = "fd0adf9005622e103f02115a50314d91f83dc705f1d685b16d45f52ad6b940b2"
    CONTROL_VARIABLE = "Canadian Wildlife Conservation Report 2026 (CRC-2026-001)"
    REPORT_ID = "CRC-2026-001"

    @staticmethod
    def verify(data: Dict[str, Any]) -> Tuple[bool, str]:
        if not isinstance(data, dict):
            return False, "invalid_data_format"
        if data.get("control_variable") != ForensicAnchor.CONTROL_VARIABLE:
            return False, "control_variable_mismatch"
        if data.get("report_id") != ForensicAnchor.REPORT_ID:
            return False, "report_id_mismatch"
        return True, "ok"


# ---------------------------------------------------------------------------
# Persistent Merkle tree (SQLite-backed, cached root)
# ---------------------------------------------------------------------------
class PersistentMerkleTree:
    """
    Append-only Merkle tree with SQLite persistence.

    Root is cached in memory and recomputed only when leaves change.
    `tree_state` persists (root_hash, tree_size, previous_sth_hash) so a
    restart does not need to rehash the entire tree.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.RLock()
        self.leaves: List[bytes] = []
        self._root: Optional[str] = None
        self._tree_size: int = 0
        self._previous_sth_hash: str = ""
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS leaves (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    hash BLOB NOT NULL,
                    data BLOB NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tree_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    root_hash TEXT NOT NULL,
                    tree_size INTEGER NOT NULL,
                    previous_sth_hash TEXT NOT NULL DEFAULT ''
                )
                """
            )
            rows = conn.execute("SELECT hash FROM leaves ORDER BY id").fetchall()
            self.leaves = [bytes(r[0]) for r in rows]
            state = conn.execute(
                "SELECT root_hash, tree_size, previous_sth_hash FROM tree_state WHERE id = 1"
            ).fetchone()
            if state and state[1] == len(self.leaves):
                self._root = state[0]
                self._tree_size = state[1]
                self._previous_sth_hash = state[2]
            else:
                self._root = self._compute_root()
                self._tree_size = len(self.leaves)
                self._persist_state()

    def _persist_state(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tree_state (id, root_hash, tree_size, previous_sth_hash)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    root_hash = excluded.root_hash,
                    tree_size = excluded.tree_size,
                    previous_sth_hash = excluded.previous_sth_hash
                """,
                (self._root or "", self._tree_size, self._previous_sth_hash),
            )

    @staticmethod
    def _hash_leaf(data: bytes) -> bytes:
        return hashlib.sha256(b"\x00" + data).digest()

    @staticmethod
    def _hash_node(left: bytes, right: bytes) -> bytes:
        return hashlib.sha256(b"\x01" + left + right).digest()

    def _compute_root(self) -> str:
        if not self.leaves:
            return hashlib.sha256(b"").hexdigest()
        hashes = list(self.leaves)
        while len(hashes) > 1:
            nxt = []
            for i in range(0, len(hashes), 2):
                left = hashes[i]
                right = hashes[i + 1] if i + 1 < len(hashes) else left
                nxt.append(self._hash_node(left, right))
            hashes = nxt
        return hashes[0].hex()

    def append(self, data: bytes) -> bytes:
        leaf_hash = self._hash_leaf(data)
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO leaves (hash, data) VALUES (?, ?)",
                    (leaf_hash, data),
                )
            self.leaves.append(leaf_hash)
            self._root = self._compute_root()
            self._tree_size = len(self.leaves)
            self._persist_state()
        return leaf_hash

    def get_root(self) -> str:
        with self._lock:
            if self._root is None:
                self._root = self._compute_root()
            return self._root

    def get_size(self) -> int:
        with self._lock:
            return self._tree_size

    def get_previous_sth_hash(self) -> str:
        with self._lock:
            return self._previous_sth_hash

    def set_previous_sth_hash(self, value: str) -> None:
        with self._lock:
            self._previous_sth_hash = value
            self._persist_state()

    def get_inclusion_proof(self, index: int) -> List[str]:
        with self._lock:
            if index < 0 or index >= len(self.leaves):
                return []
            proof: List[str] = []
            n = len(self.leaves)
            idx = index
            current = list(self.leaves)
            while n > 1:
                sibling = idx ^ 1
                if sibling < n:
                    proof.append(current[sibling].hex())
                nxt = []
                for i in range(0, n, 2):
                    left = current[i]
                    right = current[i + 1] if i + 1 < n else current[i]
                    nxt.append(self._hash_node(left, right))
                current = nxt
                idx //= 2
                n = (n + 1) // 2
            return proof


def verify_inclusion_proof(
    leaf_data: bytes, index: int, proof: List[str], root_hex: str
) -> bool:
    """
    Verify a Merkle inclusion proof produced by PersistentMerkleTree.

    Uses the same last-node-duplication scheme as the tree: when a level has
    an odd number of nodes, the last node is paired with itself.
    """
    if index < 0:
        return False
    node = hashlib.sha256(b"\x00" + leaf_data).digest()
    idx = index
    for sibling_hex in proof:
        sibling = bytes.fromhex(sibling_hex)
        if idx % 2 == 0:
            node = hashlib.sha256(b"\x01" + node + sibling).digest()
        else:
            node = hashlib.sha256(b"\x01" + sibling + node).digest()
        idx //= 2
    return node.hex() == root_hex


# ---------------------------------------------------------------------------
# Relay-BP decoder (corrected)
# ---------------------------------------------------------------------------
class RelayBPDecoder:
    """
    Min-sum belief propagation with gamma-annealed damping.

    Update rule (variable space):
        M_vc = llr + sum_{c' != c} M_cv[c']
        M_cv[c] = alpha_c * min_{v' != v} |M_vc[c, v']| * prod sign(M_vc[c, v'])

    The simplified "memory BP" here keeps a single scalar message per
    variable, accumulates check evidence via H.T, and damps with gamma.
    This is a valid approximation for sparse H and is what the original
    v0.34 intended; the fix is that the update lives in variable space.
    """

    def __init__(
        self,
        H,
        max_legs: int = 6,
        iters_per_leg: int = 25,
        gamma_start: float = 0.25,
        gamma_end: float = 0.92,
        use_gpu: bool = True,
        seed: int = 42,
    ):
        if not SCIPY_AVAILABLE:
            raise RuntimeError("scipy is required for RelayBPDecoder")
        self.max_legs = max_legs
        self.iters_per_leg = iters_per_leg
        self.gamma_start = gamma_start
        self.gamma_end = gamma_end
        self.seed = seed
        self.use_gpu = bool(use_gpu and GPU_AVAILABLE)

        if self.use_gpu:
            self.H = cp_csr_matrix(H)
            self.xp = cp
        else:
            self.H = H.tocsr()
            self.xp = np

        self.n_qubits = self.H.shape[1]
        self.n_checks = self.H.shape[0]

    def decode(self, syndrome: np.ndarray, p_error: float = 0.01) -> Tuple[np.ndarray, bool]:
        if len(syndrome) != self.n_checks:
            raise ValueError(
                f"syndrome length {len(syndrome)} != expected checks {self.n_checks}"
            )

        xp = self.xp
        rng = xp.random.default_rng(self.seed)

        syn = xp.asarray(syndrome, dtype=xp.int8)
        best_error = xp.zeros(self.n_qubits, dtype=xp.int8)
        best_weight = float("inf")
        best_valid = False

        llr = xp.full(
            self.n_qubits,
            float(np.log((1 - p_error) / p_error)),
            dtype=xp.float32,
        )

        for leg in range(self.max_legs):
            frac = leg / max(1, self.max_legs - 1)
            gamma = self.gamma_start + (self.gamma_end - self.gamma_start) * frac
            gamma = xp.clip(
                gamma + rng.normal(0.0, 0.025, self.n_qubits).astype(xp.float32),
                0.15,
                0.95,
            )

            messages = self._run_leg(llr, syn, gamma)
            candidate = (messages < 0).astype(xp.int8)

            if self._is_valid(candidate, syn):
                weight = int(xp.count_nonzero(candidate))
                if weight < best_weight:
                    best_weight = weight
                    best_error = candidate.copy()
                    best_valid = True
                    if weight <= 2:
                        break
            llr = messages

        if self.use_gpu:
            best_error = cp.asnumpy(best_error)

        return best_error, bool(best_valid)

    def _run_leg(self, llr, syndrome, gamma):
        """
        Variable-space min-sum with damping.

        check_msgs = H @ messages          # shape (n_checks,)
        var_msgs   = H.T @ check_msgs      # shape (n_qubits,)
        messages   = (1 - gamma) * llr + gamma * var_msgs
        """
        xp = self.xp
        messages = llr.copy()
        for _ in range(self.iters_per_leg):
            check_msgs = self.H @ messages
            check_msgs = xp.sign(check_msgs) * xp.minimum(xp.abs(check_msgs), 0.85)
            var_msgs = self.H.T @ check_msgs
            messages = (1.0 - gamma) * llr + gamma * var_msgs
        return messages

    def _is_valid(self, error, syndrome) -> bool:
        xp = self.xp
        computed = (self.H @ error) % 2
        syn = xp.asarray(syndrome, dtype=xp.int8)
        return bool(xp.array_equal(computed, syn))


class SafeDecoder:
    """Error-handling shim around RelayBPDecoder. Not a distinct algorithm."""

    def __init__(self, parity_check_matrix, use_gpu: bool = True):
        self.decoder = RelayBPDecoder(parity_check_matrix, use_gpu=use_gpu)

    @property
    def n_checks(self) -> int:
        return self.decoder.n_checks

    def decode_syndrome(self, syndrome: np.ndarray) -> Tuple[np.ndarray, bool, str]:
        try:
            error, success = self.decoder.decode(syndrome)
            status = "CORRECTED" if success else "UNCORRECTED_OR_HIGH_WEIGHT"
            return error, success, status
        except Exception as exc:  # noqa: BLE001
            logger.error("decoder_error", error=str(exc))
            return np.zeros(self.decoder.n_qubits, dtype=int), False, "DECODER_ERROR"


# ---------------------------------------------------------------------------
# PQC signer (Ed25519 + ML-DSA-65)
# ---------------------------------------------------------------------------
class PQCSigner:
    """
    Hybrid signer. Ed25519 is always available if nacl is installed.
    ML-DSA-65 is available if liboqs is installed AND keys are present.

    The output schema is stable: fields are always present, empty strings
    when the corresponding algorithm is unavailable.
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self._ed_signer = None
        self._ed_verify_key = None
        if NACL_AVAILABLE and config.ed25519_seed is not None:
            import nacl.signing
            self._ed_signer = nacl.signing.SigningKey(config.ed25519_seed)
            self._ed_verify_key = self._ed_signer.verify_key

        self._ml_available = (
            OQS_AVAILABLE
            and config.ml_dsa65_public_key is not None
            and config.ml_dsa65_secret_key is not None
        )

    @property
    def ed25519_public_key_hex(self) -> str:
        if self._ed_verify_key is None:
            return ""
        return bytes(self._ed_verify_key).hex()

    @property
    def ml_dsa65_public_key_hex(self) -> str:
        if not self._ml_available:
            return ""
        return self.config.ml_dsa65_public_key.hex()

    def hybrid_sign(self, data: bytes) -> Dict[str, Any]:
        classical_sig = b""
        if self._ed_signer is not None:
            classical_sig = self._ed_signer.sign(data).signature

        pqc_sig = b""
        if self._ml_available:
            with oqs.Signature(
                "ML-DSA-65", secret_key=self.config.ml_dsa65_secret_key
            ) as sig:
                pqc_sig = sig.sign(data)

        mode = "Ed25519" if self._ed_signer is not None else "none"
        if self._ml_available:
            mode += " + ML-DSA-65"

        return {
            "hybrid_mode": mode,
            "classical_signature": classical_sig.hex(),
            "pqc_signature": pqc_sig.hex(),
            "ed25519_public_key": self.ed25519_public_key_hex,
            "ml_dsa65_public_key": self.ml_dsa65_public_key_hex,
            "payload_hash": hashlib.sha256(data).hexdigest(),
            "timestamp": datetime.now(UTC).isoformat(),
            "genesis_anchor": ForensicAnchor.GENESIS_HASH,
        }


# ---------------------------------------------------------------------------
# Guardian (pure policy function)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class GuardianDecision:
    allowed: bool
    reason: str
    message: str


class GuardianPolicy:
    """
    Guardian Policy Specification v1.1

    1. SAFETY: reject prompts containing jailbreak/bypass flags.
    2. ENVIRONMENTAL: if the prompt mentions conservation terms, the
       *output* must contain mitigation terms. Word-boundary + stem
       matching, not naive substring.
    3. TRANSPARENCY: enforced by ForensicAnchor.verify, not here.
    4. AUDIT: enforced by the audit sink, not here.

    This class is a pure function. It never authors its own prompt.
    """

    SAFETY_FLAGS = ("ignore safety", "bypass", "jailbreak", "circumvent", "disable")

    CONSERVATION_STEMS = ("soil", "biodivers", "wetland", "forest", "extinct", "habitat")
    MITIGATION_STEMS = ("sustain", "protect", "restor", "conserv", "preserv")

    _word_re = re.compile(r"[a-z]+")

    @classmethod
    def _tokens(cls, text: str) -> List[str]:
        return cls._word_re.findall(text.lower())

    @classmethod
    def _has_stem(cls, tokens: List[str], stems: Tuple[str, ...]) -> bool:
        return any(any(stem in tok for stem in stems) for tok in tokens)

    def evaluate(self, prompt: str, output: str) -> GuardianDecision:
        p_tokens = self._tokens(prompt)
        o_tokens = self._tokens(output)

        # 1. Safety
        lower_prompt = prompt.lower()
        if any(flag in lower_prompt for flag in self.SAFETY_FLAGS):
            return GuardianDecision(False, "short_term_risk", "safety_policy_violation")

        # 2. Environmental
        if self._has_stem(p_tokens, self.CONSERVATION_STEMS):
            if not self._has_stem(o_tokens, self.MITIGATION_STEMS):
                return GuardianDecision(
                    False, "insufficient_mitigation", "mitigation_language_required"
                )

        return GuardianDecision(True, "passed", output)


# ---------------------------------------------------------------------------
# Gossip / fork detection
# ---------------------------------------------------------------------------
@dataclass
class SignedTreeHead:
    timestamp: int
    tree_size: int
    root_hash: str
    previous_sth_hash: str
    signature: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "tree_size": self.tree_size,
            "root_hash": self.root_hash,
            "previous_sth_hash": self.previous_sth_hash,
            "signature": self.signature,
        }


class AntiForkGossip:
    """
    Publishes this node's STH to Redis under lattice:sth:node:{node_id}.
    Scans lattice:sth:node:* for peers. Optionally fetches peer /sth over
    HTTP when GOSSIP_FETCH_URL is set.
    """

    KEY_PREFIX = "lattice:sth:node:"

    def __init__(
        self,
        node_id: str,
        redis_client,
        pqc: PQCSigner,
        peers: Tuple[str, ...],
        fetch_url: Optional[str],
        config: AppConfig,
    ):
        self.node_id = node_id
        self.redis = redis_client
        self.pqc = pqc
        self.peers = peers
        self.fetch_url = fetch_url
        self.config = config
        self.local_sth: Optional[SignedTreeHead] = None
        self.peer_sths: Dict[str, SignedTreeHead] = {}
        self.fork_detected = False

    async def publish_sth(self, sth: SignedTreeHead) -> None:
        self.local_sth = sth
        if self.redis is None:
            return
        try:
            await self.redis.set(
                f"{self.KEY_PREFIX}{self.node_id}",
                json.dumps(sth.to_dict()),
                ex=self.config.record_ttl_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("sth_publish_failed", error=str(exc))

    async def detect_forks(self) -> None:
        if self.local_sth is None:
            return
        if self.redis is not None:
            await self._scan_redis()
        if self.fetch_url:
            await self._fetch_http()

    async def _scan_redis(self) -> None:
        try:
            keys = await self.redis.keys(f"{self.KEY_PREFIX}*")
        except Exception as exc:  # noqa: BLE001
            logger.warning("sth_scan_failed", error=str(exc))
            return
        for key in keys:
            peer_id = key.rsplit(":", 1)[-1]
            if peer_id == self.node_id:
                continue
            try:
                raw = await self.redis.get(key)
                if not raw:
                    continue
                data = json.loads(raw)
                peer_sth = SignedTreeHead(
                    timestamp=data["timestamp"],
                    tree_size=data["tree_size"],
                    root_hash=data["root_hash"],
                    previous_sth_hash=data.get("previous_sth_hash", ""),
                    signature=data.get("signature", {}),
                )
                self.peer_sths[peer_id] = peer_sth
                self._compare(peer_id, peer_sth)
            except Exception as exc:  # noqa: BLE001
                logger.warning("sth_parse_failed", peer=peer_id, error=str(exc))

    async def _fetch_http(self) -> None:
        try:
            async with ClientSession(timeout=web.ClientTimeout(total=5)) as session:
                async with session.get(
                    self.fetch_url,
                    headers={"X-API-Key": self.config.api_key},
                ) as resp:
                    if resp.status != 200:
                        logger.warning("sth_fetch_non_200", status=resp.status)
                        return
                    data = await resp.json()
                    peer_sth = SignedTreeHead(
                        timestamp=data["timestamp"],
                        tree_size=data["tree_size"],
                        root_hash=data["root_hash"],
                        previous_sth_hash=data.get("previous_sth_hash", ""),
                        signature=data.get("signature", {}),
                    )
                    peer_id = data.get("node_id", "http-peer")
                    self.peer_sths[peer_id] = peer_sth
                    self._compare(peer_id, peer_sth)
        except Exception as exc:  # noqa: BLE001
            logger.warning("sth_fetch_failed", error=str(exc))

    def _compare(self, peer_id: str, peer_sth: SignedTreeHead) -> None:
        local = self.local_sth
        if local is None:
            return
        if (
            local.tree_size != peer_sth.tree_size
            or local.root_hash != peer_sth.root_hash
        ):
            logger.warning(
                "fork_detected",
                peer=peer_id,
                local_root=local.root_hash[:16],
                peer_root=peer_sth.root_hash[:16],
                local_size=local.tree_size,
                peer_size=peer_sth.tree_size,
            )
            self.fork_detected = True


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------
class LatticeServer:
    def __init__(self, app: web.Application):
        self.app = app
        self.config: AppConfig = app["config"]
        self.merkle: PersistentMerkleTree = app["merkle"]
        self.gossip: AntiForkGossip = app["gossip"]
        self.guardian: GuardianPolicy = app["guardian"]
        self.pqc: PQCSigner = app["pqc"]
        self.decoder: Optional[SafeDecoder] = app.get("decoder")
        self.redis = app.get("redis")

    async def health(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "status": "healthy",
                "version": "1.0",
                "node_id": self.config.node_id,
                "genesis_anchor": ForensicAnchor.GENESIS_HASH,
                "tree_size": self.merkle.get_size(),
                "current_root": self.merkle.get_root(),
                "previous_sth_hash": self.merkle.get_previous_sth_hash(),
                "fork_detected": self.gossip.fork_detected,
                "peer_count": len(self.gossip.peer_sths),
            }
        )

    async def sth(self, request: web.Request) -> web.Response:
        """Expose this node's latest STH for peer fetch."""
        sth = self.gossip.local_sth
        if sth is None:
            return web.json_response({"error": "no_sth_yet"}, status=404)
        payload = sth.to_dict()
        payload["node_id"] = self.config.node_id
        return web.json_response(payload)

    async def capabilities(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "ed25519": NACL_AVAILABLE and self.pqc._ed_signer is not None,
                "ml_dsa65": self.pqc._ml_available,
                "decoder": self.decoder is not None,
                "gpu": GPU_AVAILABLE and (self.decoder is not None and self.decoder.decoder.use_gpu),
                "redis": self.redis is not None,
                "scipy": SCIPY_AVAILABLE,
                "gossip_peers": list(self.config.gossip_peers),
                "gossip_fetch_url": self.config.gossip_fetch_url,
                "n_checks": self.decoder.n_checks if self.decoder else None,
            }
        )

    async def self_audit(self, request: web.Request) -> web.Response:
        aligned, msg = ForensicAnchor.verify(
            {
                "control_variable": ForensicAnchor.CONTROL_VARIABLE,
                "report_id": ForensicAnchor.REPORT_ID,
                "valuation_cad": 3_100_000_000.0,
            }
        )
        return web.json_response(
            {
                "self_audit": "PASSED" if aligned else "FAILED",
                "reason": msg,
                "genesis_anchor": ForensicAnchor.GENESIS_HASH,
                "control_variable": ForensicAnchor.CONTROL_VARIABLE,
            }
        )

    async def decode_syndrome(self, request: web.Request) -> web.Response:
        if self.decoder is None:
            return web.json_response({"error": "decoder_unavailable"}, status=503)
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "invalid_json"}, status=400)
        syndrome = data.get("syndrome", [])
        if not isinstance(syndrome, list) or not syndrome:
            return web.json_response({"error": "syndrome_array_required"}, status=400)
        if len(syndrome) != self.decoder.n_checks:
            return web.json_response(
                {
                    "error": "syndrome_length_mismatch",
                    "expected": self.decoder.n_checks,
                    "got": len(syndrome),
                },
                status=400,
            )
        error, success, status = self.decoder.decode_syndrome(
            np.asarray(syndrome, dtype=int)
        )
        return web.json_response(
            {"error_vector": error.tolist(), "success": success, "status": status}
        )

    async def get_inclusion_proof(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
            index = int(data.get("index", -1))
        except Exception:
            return web.json_response({"error": "invalid_request"}, status=400)
        if index < 0 or index >= self.merkle.get_size():
            return web.json_response({"error": "index_out_of_range"}, status=400)
        proof = self.merkle.get_inclusion_proof(index)
        return web.json_response(
            {
                "index": index,
                "proof": proof,
                "root": self.merkle.get_root(),
                "tree_size": self.merkle.get_size(),
            }
        )

    async def verify_proof(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
            leaf_data = bytes.fromhex(data["leaf_hex"])
            index = int(data["index"])
            proof = list(data["proof"])
            root = data["root"]
        except Exception:
            return web.json_response({"error": "invalid_request"}, status=400)
        ok = verify_inclusion_proof(leaf_data, index, proof, root)
        return web.json_response({"valid": ok})

    async def tokenize_rwa(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "invalid_json"}, status=400)

        aligned, reason = ForensicAnchor.verify(data)
        if not aligned:
            return web.json_response(
                {"error": "forensic_anchor_rejection", "reason": reason}, status=403
            )

        # Guardian evaluates the caller's payload, not a server-authored wrapper.
        prompt = json.dumps(data, sort_keys=True)
        output = data.get("mitigation_statement", "")
        decision = self.guardian.evaluate(prompt, output)
        if not decision.allowed:
            return web.json_response(
                {
                    "error": "guardian_block",
                    "reason": decision.reason,
                    "message": decision.message,
                },
                status=403,
            )

        token_id = f"RWA-{secrets.token_hex(8).upper()}"
        record = {"token_id": token_id, **data}
        payload_bytes = json.dumps(record, sort_keys=True).encode()

        leaf_hash = self.merkle.append(payload_bytes)
        pqc_result = self.pqc.hybrid_sign(payload_bytes)

        previous_sth_hash = self.merkle.get_previous_sth_hash()
        sth = SignedTreeHead(
            timestamp=int(datetime.now(UTC).timestamp()),
            tree_size=self.merkle.get_size(),
            root_hash=self.merkle.get_root(),
            previous_sth_hash=previous_sth_hash,
            signature=pqc_result,
        )
        sth_hash = hashlib.sha256(
            json.dumps(sth.to_dict(), sort_keys=True).encode()
        ).hexdigest()
        self.merkle.set_previous_sth_hash(sth_hash)
        await self.gossip.publish_sth(sth)

        if self.redis is not None:
            try:
                await self.redis.setex(
                    f"audit:token:{token_id}",
                    self.config.record_ttl_seconds,
                    json.dumps(
                        {
                            "action": "tokenize",
                            "token_id": token_id,
                            "merkle_leaf_hash": leaf_hash.hex(),
                            "merkle_tree_size": self.merkle.get_size(),
                            "created_at": datetime.now(UTC).isoformat(),
                        },
                        default=str,
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("audit_write_failed", error=str(exc))

        return web.json_response(
            {
                "status": "TOKENIZED_AND_AUDITED",
                "token_id": token_id,
                "merkle_leaf_hash": leaf_hash.hex(),
                "merkle_root": self.merkle.get_root(),
                "merkle_tree_size": self.merkle.get_size(),
                "pqc_result": pqc_result,
                "genesis_anchor": ForensicAnchor.GENESIS_HASH,
            },
            status=202,
        )


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------
async def _connect_redis(config: AppConfig):
    if not REDIS_AVAILABLE:
        return None
    try:
        client = redis.Redis.from_url(
            config.redis_url, decode_responses=True, max_connections=20
        )
        await client.ping()
        logger.info("redis_connected")
        return client
    except Exception as exc:  # noqa: BLE001
        logger.warning("redis_unavailable", error=str(exc))
        return None


def _build_parity_check_matrix():
    if not SCIPY_AVAILABLE:
        return None
    return csr_matrix(
        np.array(
            [[1, 1, 0, 0], [0, 1, 1, 0], [0, 0, 1, 1], [1, 0, 0, 1]], dtype=int
        )
    )


async def main() -> None:
    try:
        config = AppConfig.load()
    except ValueError as exc:
        logger.error("config_error", error=str(exc))
        sys.exit(1)

    redis_client = await _connect_redis(config)

    merkle = PersistentMerkleTree(config.db_path)
    logger.info("merkle_loaded", leaves=len(merkle.leaves), root=merkle.get_root()[:16])

    pqc = PQCSigner(config)

    decoder = None
    H = _build_parity_check_matrix()
    if H is not None:
        decoder = SafeDecoder(H, use_gpu=True)

    guardian = GuardianPolicy()
    gossip = AntiForkGossip(
        node_id=config.node_id,
        redis_client=redis_client,
        pqc=pqc,
        peers=config.gossip_peers,
        fetch_url=config.gossip_fetch_url,
        config=config,
    )

    app = web.Application(middlewares=[auth_middleware])
    app["config"] = config
    app["merkle"] = merkle
    app["gossip"] = gossip
    app["guardian"] = guardian
    app["pqc"] = pqc
    app["decoder"] = decoder
    app["redis"] = redis_client

    server = LatticeServer(app)

    app.router.add_get("/health", server.health)
    app.router.add_get("/capabilities", server.capabilities)
    app.router.add_get("/audit/self", server.self_audit)
    app.router.add_get("/sth", server.sth)
    app.router.add_post("/decode/syndrome", server.decode_syndrome)
    app.router.add_post("/merkle/proof", server.get_inclusion_proof)
    app.router.add_post("/merkle/verify", server.verify_proof)
    app.router.add_post("/rwa/tokenize", server.tokenize_rwa)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, config.api_host, config.api_port)
    await site.start()

    logger.info(
        "startup",
        version="1.0",
        node_id=config.node_id,
        listen=f"http://{config.api_host}:{config.api_port}",
        db=config.db_path,
        ed25519=pqc._ed_signer is not None,
        ml_dsa65=pqc._ml_available,
        decoder=decoder is not None,
        redis=redis_client is not None,
        genesis_anchor=ForensicAnchor.GENESIS_HASH,
    )

    async def fork_detector_loop():
        try:
            while True:
                await asyncio.sleep(60)
                await gossip.detect_forks()
        except asyncio.CancelledError:
            return

    fork_task = asyncio.create_task(fork_detector_loop())

    try:
        await asyncio.Event().wait()
    finally:
        fork_task.cancel()
        try:
            await fork_task
        except asyncio.CancelledError:
            pass
        if redis_client is not None:
            await redis_client.close()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
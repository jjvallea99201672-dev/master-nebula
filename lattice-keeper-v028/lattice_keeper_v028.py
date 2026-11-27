#!/usr/bin/env python3
"""
Lattice Keeper v0.28 — FULLY RESTORED, ENHANCED & PATCHED
Post-Quantum RWA Tokenization + Forensic Auditor Axis + AI Guardian Layer
Copyright (c) 2026 Jay Vallea. All Rights Reserved.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Tuple

import aiohttp
import redis.asyncio as redis
import structlog
from aiohttp import web
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

structlog.configure(
    processors=[structlog.processors.TimeStamper(fmt="iso"), structlog.processors.JSONRenderer()]
)
logger = structlog.get_logger("lattice_keeper")

UTC = timezone.utc

# ======================== IMMUTABLE OPERATOR ANCHOR ========================
OPERATOR_GENESIS_HASH = "fd0adf9005622e103f02115a50314d91f83dc705f1d685b16d45f52ad6b940b2"

# ======================== CONFIG ========================
@dataclass
class AppConfig:
    api_host: str = os.getenv("API_HOST", "0.0.0.0")
    api_port: int = int(os.getenv("API_PORT", "8765"))
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    api_key: str = os.getenv("LATTICE_API_KEY", "")

    record_ttl_seconds: int = int(os.getenv("RECORD_TTL_SECONDS", str(10 * 365 * 24 * 3600)))
    max_body_bytes: int = 64 * 1024

    ollama_host: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "llama3.2")

    @classmethod
    def load(cls) -> "AppConfig":
        config = cls()
        if not config.api_key:
            raise ValueError("LATTICE_API_KEY environment variable is required and must be set.")
        return config

# ======================== FORENSIC ANCHOR ========================
class ForensicAnchor:
    CONTROL_VARIABLE = "Canadian Wildlife Conservation Report 2026 (CRC-2026-001)"
    ANCHORED_VALUATION_CAD = 3_100_000_000.0

    # PATCH 1: Use the immutable operator hash, not a dynamically generated one
    GENESIS_HASH: str = OPERATOR_GENESIS_HASH

    @staticmethod
    def verify(data: dict) -> Tuple[bool, str]:
        if not isinstance(data, dict):
            return False, "Invalid data format"

        # PATCH 1: Enforce Genesis Hash linkage if provided
        provided_hash = data.get("genesis_hash") or data.get("root_hash")
        if provided_hash and provided_hash != ForensicAnchor.GENESIS_HASH:
            return False, "Genesis Hash mismatch - operator anchor required"

        if data.get("control_variable") != ForensicAnchor.CONTROL_VARIABLE:
            return False, "control_variable mismatch"

        if data.get("report_id") != "CRC-2026-001":
            return False, "report_id mismatch"

        try:
            val = float(data.get("valuation_cad", 0))
            if abs(val - ForensicAnchor.ANCHORED_VALUATION_CAD) > 0.01:
                return False, f"valuation_cad mismatch: expected {ForensicAnchor.ANCHORED_VALUATION_CAD}"
        except (TypeError, ValueError):
            return False, "valuation_cad must be a valid number"

        return True, "Forensic Auditor Axis aligned"

# ======================== POST-QUANTUM CRYPTO ========================
try:
    import oqs
    PQC_AVAILABLE = True
except ImportError:
    PQC_AVAILABLE = False
    logger.warning("liboqs not available - running in simulated mode")

class PQCSigner:
    SIG_ALG = "ML-DSA-65"
    KEM_ALG = "ML-KEM-768"

    def __init__(self, keys_dir: str = "./pqc_keys"):
        self.keys_dir = Path(keys_dir)
        self.keys_dir.mkdir(parents=True, exist_ok=True)
        self.available = PQC_AVAILABLE
        
        # PATCH 2: Persistent HMAC key
        self.hmac_key_file = self.keys_dir / "hmac.key"
        if self.hmac_key_file.exists():
            self.hmac_key = self.hmac_key_file.read_bytes()
        else:
            self.hmac_key = secrets.token_bytes(32)
            self.hmac_key_file.write_bytes(self.hmac_key)
            self.hmac_key_file.chmod(0o600)

        self._init_keys()

    def _init_keys(self):
        if not self.available:
            return

        # Signature
        sig_pk = self.keys_dir / "sig_public.key"
        sig_sk = self.keys_dir / "sig_private.key"

        if sig_pk.exists() and sig_sk.exists():
            self.sig_public = sig_pk.read_bytes()
            self.sig_secret = sig_sk.read_bytes()
        else:
            with oqs.Signature(self.SIG_ALG) as sig:
                self.sig_public, self.sig_secret = sig.generate_keypair()
            sig_pk.write_bytes(self.sig_public)
            sig_sk.write_bytes(self.sig_secret)
            sig_sk.chmod(0o600)

        # KEM
        kem_pk = self.keys_dir / "kem_public.key"
        kem_sk = self.keys_dir / "kem_private.key"

        if kem_pk.exists() and kem_sk.exists():
            self.kem_public = kem_pk.read_bytes()
            self.kem_secret = kem_sk.read_bytes()
        else:
            with oqs.KeyEncapsulation(self.KEM_ALG) as kem:
                self.kem_public, self.kem_secret = kem.generate_keypair()
            kem_pk.write_bytes(self.kem_public)
            kem_sk.write_bytes(self.kem_secret)
            kem_sk.chmod(0o600)

    def hybrid_sign(self, data: bytes) -> dict:
        # PATCH 3: Fresh OQS context per operation to prevent thread-safety issues
        if self.available and hasattr(self, 'sig_secret'):
            with oqs.Signature(self.SIG_ALG) as signer:
                sig_bytes = signer.sign(self.sig_secret, data)
            pqc_sig = base64.b64encode(sig_bytes).decode()
        else:
            pqc_sig = "SIMULATED_ML_DSA_65_SIGNATURE"

        # PATCH 2: Use the persistent HMAC key
        classical_hmac = hmac.new(self.hmac_key, data, hashlib.sha256).hexdigest()

        return {
            "hybrid_mode": "ML-DSA-65 + HMAC-SHA256",
            "pqc_signature": pqc_sig,
            "classical_hmac": classical_hmac,
            "payload_hash": hashlib.sha256(data).hexdigest(),
            "timestamp": datetime.now(UTC).isoformat(),
        }

    def kem_encrypt(self, data: bytes) -> dict:
        if self.available and hasattr(self, 'kem_public'):
            with oqs.KeyEncapsulation(self.KEM_ALG) as kem:
                ciphertext, shared_secret = kem.encap(self.kem_public)
            
            aes_key = hashlib.sha256(shared_secret).digest()[:32]
            aesgcm = AESGCM(aes_key)
            nonce = os.urandom(12)
            encrypted = aesgcm.encrypt(nonce, data, None)
            combined = ciphertext + nonce + encrypted
            return {
                "kem_algorithm": self.KEM_ALG,
                "ciphertext": base64.b64encode(combined).decode(),
                "mode": "real"
            }
        return {"kem_algorithm": "simulated", "ciphertext": base64.b64encode(data).decode(), "mode": "simulated"}

# ======================== AUTH MIDDLEWARE ========================
@web.middleware
async def auth_middleware(request: web.Request, handler):
    config: AppConfig = request.app["config"]
    key = (
        request.headers.get("X-API-Key")
        or request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    )
    if not key or not hmac.compare_digest(key, config.api_key):
        return web.json_response({"error": "Unauthorized"}, status=401)
    return await handler(request)

# ======================== RAG + GUARDIAN ========================
try:
    from langchain_ollama import ChatOllama, OllamaEmbeddings
    from langchain_community.vectorstores import FAISS
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.runnables import RunnablePassthrough
    from langchain_core.output_parsers import StrOutputParser
    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False
    logger.warning("LangChain/Ollama not available - RAG disabled")

class RAGManager:
    def __init__(self, config: AppConfig):
        self.config = config
        self.vectorstore = None
        self._init_rag()

    def _init_rag(self):
        if not LANGCHAIN_AVAILABLE:
            return
        try:
            embeddings = OllamaEmbeddings(base_url=self.config.ollama_host, model="nomic-embed-text")
            index_path = Path("./lattice_rag_index")

            if index_path.exists():
                self.vectorstore = FAISS.load_local(str(index_path), embeddings, allow_dangerous_deserialization=True)
            else:
                sample_docs = [
                    "The Canadian Wildlife Conservation Report 2026 values protected ecosystems at CAD 3.1 billion.",
                    "Regenerative agriculture practices including cover cropping and rotational grazing restore soil health.",
                    "Wetlands and old-growth forests are critical for carbon sequestration and biodiversity protection.",
                    "Long-term environmental stewardship must prioritize sustainability over short-term gains."
                ]
                splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)
                splits = splitter.create_documents(sample_docs)
                self.vectorstore = FAISS.from_documents(splits, embeddings)
                self.vectorstore.save_local(str(index_path))
        except Exception as e:
            logger.error("rag_init_failed", error=str(e))

    def get_retriever(self):
        return self.vectorstore.as_retriever(search_kwargs={"k": 4}) if self.vectorstore else None

class GuardianVector:
    def __init__(self, config: AppConfig, redis_client: redis.Redis):
        self.config = config
        self.redis = redis_client
        self.pqc = PQCSigner()
        self.rag = RAGManager(config)

    async def audit(self, record: Dict[str, Any]):
        ts = int(datetime.now(UTC).timestamp())
        key = f"audit:lattice:{ts}:{hashlib.sha256(str(record).encode()).hexdigest()[:8]}"
        await self.redis.setex(key, self.config.record_ttl_seconds, json.dumps(record, default=str))

    def evaluate_guardian(self, prompt: str, output: str) -> Tuple[bool, str, str]:
        p = prompt.lower()
        o = output.lower()

        if any(flag in o for flag in ["ignore safety", "bypass", "jailbreak", "maximize profit"]):
            return False, "short_term_risk", "[GUARDIAN BLOCKED] Short-term optimization detected"

        conservation_terms = ["soil", "biodiversity", "wetland", "forest", "extinction", "drought"]
        if any(term in p for term in conservation_terms):
            if not any(word in o for word in ["sustainable", "protect", "restore", "regenerate", "long-term"]):
                return False, "insufficient_mitigation", "[GUARDIAN BLOCKED] Long-term environmental impact not addressed"

        return True, "passed", output

    async def rag_generate(self, prompt: str) -> str:
        if not self.rag.vectorstore or not LANGCHAIN_AVAILABLE:
            return "RAG system not available."

        try:
            retriever = self.rag.get_retriever()
            llm = ChatOllama(
                base_url=self.config.ollama_host,
                model=self.config.ollama_model,
                temperature=0.7,
                num_predict=1024
            )

            template = ChatPromptTemplate.from_template(
                "You are a conservation-focused AI. Answer using the provided context.\n\n"
                "Context: {context}\n\nQuestion: {question}\n\nAnswer:"
            )

            chain = (
                {"context": retriever, "question": RunnablePassthrough()}
                | template
                | llm
                | StrOutputParser()
            )

            return await chain.ainvoke(prompt)
        except Exception as e:
            logger.error("rag_generation_failed", error=str(e))
            return f"[RAG ERROR] {str(e)}"

    async def protect(self, prompt: str, raw_output: str, metadata: dict | None = None) -> dict:
        metadata = metadata or {}
        forensic_ok, forensic_msg = ForensicAnchor.verify(metadata)
        if not forensic_ok:
            return {"status": "forensic_rejected", "error": forensic_msg}

        passed, reason, protected_output = self.evaluate_guardian(prompt, raw_output)

        payload = {
            "prompt_hash": hashlib.sha256(prompt.encode()).hexdigest(),
            "output_hash": hashlib.sha256(protected_output.encode()).hexdigest(),
            "timestamp": datetime.now(UTC).isoformat(),
        }

        signature = self.pqc.hybrid_sign(json.dumps(payload, sort_keys=True).encode())

        audit_record = {
            "action": "guard",
            "passed": passed,
            "reason": reason,
            **payload,
            "signature": signature
        }
        await self.audit(audit_record)

        return {
            "status": "guarded" if passed else "blocked",
            "protected_output": protected_output,
            "reason": reason,
            "signature": signature,
            "genesis_hash": ForensicAnchor.GENESIS_HASH[:16]
        }

# ======================== SERVER ========================
class LatticeServer:
    def __init__(self, config: AppConfig, guardian: GuardianVector):
        self.config = config
        self.guardian = guardian

    async def health(self, request: web.Request):
        return web.json_response({
            "status": "healthy",
            "version": "0.28-patched",
            "guardian": "ACTIVE",
            "pqc_available": self.guardian.pqc.available,
            "rag_enabled": bool(self.guardian.rag.vectorstore),
            "genesis_hash": ForensicAnchor.GENESIS_HASH[:16] + "...",
        })

    async def tokenize_rwa(self, request: web.Request):
        try:
            data: dict = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        aligned, msg = ForensicAnchor.verify(data)
        if not aligned:
            return web.json_response({"error": "Forensic rejection", "reason": msg}, status=403)

        token_id = f"RWA-{secrets.token_hex(8).upper()}"
        payload_bytes = json.dumps({"token_id": token_id, **data}, sort_keys=True).encode()

        pqc_result = self.guardian.pqc.hybrid_sign(payload_bytes)
        kem_result = self.guardian.pqc.kem_encrypt(payload_bytes)

        record = {
            "token_id": token_id,
            "root_hash": hashlib.sha256(payload_bytes).hexdigest(),
            "pqc_result": json.dumps(pqc_result),
            "kem_result": json.dumps(kem_result),
            "forensic_status": "PASSED",
            "created_at": datetime.now(UTC).isoformat(),
        }

        await self.guardian.redis.hset(f"rwa:{token_id}", mapping=record)
        await self.guardian.redis.expire(f"rwa:{token_id}", self.config.record_ttl_seconds)
        await self.guardian.audit({"action": "tokenize", "token_id": token_id})

        return web.json_response({
            "status": "TOKENIZED_AND_AUDITED",
            "token_id": token_id,
            "pqc_result": pqc_result,
            "kem_result": kem_result,
        }, status=202)

    async def rag_guard(self, request: web.Request):
        try:
            data: dict = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        prompt = data.get("prompt", "").strip()
        metadata = data.get("metadata") or {}

        if not prompt:
            return web.json_response({"error": "prompt is required"}, status=400)

        raw_output = await self.guardian.rag_generate(prompt)
        result = await self.guardian.protect(prompt, raw_output, metadata)

        return web.json_response(result)

# ======================== MAIN ========================
async def main():
    config = AppConfig.load()

    redis_client = await redis.from_url(
        config.redis_url, decode_responses=True, max_connections=20
    )
    await redis_client.ping()

    guardian = GuardianVector(config, redis_client)
    server = LatticeServer(config, guardian)

    app = web.Application(
        middlewares=[auth_middleware],
        client_max_size=config.max_body_bytes
    )
    app["config"] = config
    app["guardian"] = guardian

    app.router.add_get("/health", server.health)
    app.router.add_post("/rwa/tokenize", server.tokenize_rwa)
    app.router.add_post("/rag_guard", server.rag_guard)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, config.api_host, config.api_port)
    await site.start()

    print("\n" + "=" * 110)
    print("🚀 Lattice Keeper v0.28 — FULLY RESTORED, ENHANCED & PATCHED")
    print(f"📍 http://{config.api_host}:{config.api_port}")
    print(f"🔐 Genesis Hash: {ForensicAnchor.GENESIS_HASH[:16]}...")
    print("🛡️  Endpoints: /health, /rwa/tokenize, /rag_guard")
    print("=" * 110)

    try:
        await asyncio.Event().wait()
    finally:
        await redis_client.aclose()
        await runner.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
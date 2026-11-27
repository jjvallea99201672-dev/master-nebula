"""Optional retrieval (FAISS via LangChain) + Ollama generation.

Copyright (c) 2026 Jay Vallea. All Rights Reserved.

FAISS indexes saved by LangChain include a pickle file, and unpickling
untrusted data can execute code. We therefore never load an index unless:

1. ``manifest.json`` in the index dir lists the SHA-256 of every index file, and
2. the SHA-256 of ``manifest.json`` itself equals ``RAG_INDEX_MANIFEST_SHA256``,
   a value the operator pins in configuration (printed by ``lattice-keeper rag-build``).

Only after both checks pass do we call LangChain with
``allow_dangerous_deserialization=True``. If verification fails, RAG is
disabled and the service runs without retrieval. Rebuild the index with
``lattice-keeper rag-build`` from your own source documents.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import aiohttp
import structlog

from .crypto import atomic_write, sha256_hex

log = structlog.get_logger("lattice_keeper.rag")
MANIFEST_NAME = "manifest.json"
MANIFEST_FORMAT = "lattice-keeper-rag-manifest/v1"
INDEX_FILES = ("index.faiss", "index.pkl")


class IndexVerificationError(Exception):
    pass


class UpstreamError(Exception):
    """Model backend failed or timed out (details are logged, not returned)."""


def _file_sha256(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_manifest(index_dir: str | Path) -> str:
    """Write manifest.json for an index dir and return its SHA-256 (the pin)."""
    d = Path(index_dir)
    files = {name: _file_sha256(d / name) for name in INDEX_FILES}
    blob = json.dumps({"format": MANIFEST_FORMAT, "files": files}, sort_keys=True, indent=2).encode()
    atomic_write(d / MANIFEST_NAME, blob, 0o644)
    return sha256_hex(blob)


def verify_index(index_dir: str | Path, pinned_manifest_sha256: str | None) -> None:
    d = Path(index_dir)
    if not pinned_manifest_sha256:
        raise IndexVerificationError("RAG_INDEX_MANIFEST_SHA256 not set; refusing to unpickle index")
    mpath = d / MANIFEST_NAME
    if not mpath.is_file():
        raise IndexVerificationError("manifest.json missing")
    blob = mpath.read_bytes()
    if sha256_hex(blob) != pinned_manifest_sha256.lower():
        raise IndexVerificationError("manifest.json hash does not match pinned value")
    manifest = json.loads(blob)
    if manifest.get("format") != MANIFEST_FORMAT:
        raise IndexVerificationError("unknown manifest format")
    files = manifest.get("files") or {}
    for name in INDEX_FILES:
        p = d / name
        if name not in files or not p.is_file() or p.is_symlink():
            raise IndexVerificationError(f"{name} missing from manifest or disk")
        if _file_sha256(p) != files[name]:
            raise IndexVerificationError(f"{name} hash mismatch")


class RAGEngine:
    def __init__(
        self,
        ollama_host: str,
        ollama_model: str,
        timeout_seconds: float,
        vectorstore: Any | None = None,
        status: str = "disabled",
    ) -> None:
        self.ollama_host = ollama_host
        self.ollama_model = ollama_model
        self.timeout = timeout_seconds
        self.vectorstore = vectorstore
        self.status = status  # "enabled" | "disabled" | "error"
        self._session: aiohttp.ClientSession | None = None

    @classmethod
    def from_settings(cls, s) -> RAGEngine:
        engine = cls(s.ollama_host, s.ollama_model, s.ollama_timeout_seconds)
        if not s.rag_enabled:
            return engine
        if not s.rag_index_dir:
            log.warning("rag_disabled", reason="RAG_INDEX_DIR not set")
            engine.status = "error"
            return engine
        try:
            verify_index(s.rag_index_dir, s.rag_index_manifest_sha256)
            from langchain_community.vectorstores import FAISS  # type: ignore[import-not-found]
            from langchain_ollama import OllamaEmbeddings  # type: ignore[import-not-found]

            emb = OllamaEmbeddings(model=s.rag_embed_model, base_url=s.ollama_host)
            # Safe only because the files were just verified against an operator-pinned hash.
            engine.vectorstore = FAISS.load_local(s.rag_index_dir, emb, allow_dangerous_deserialization=True)
            engine.status = "enabled"
        except ImportError:
            log.warning("rag_disabled", reason="install the [rag] extra")
            engine.status = "error"
        except IndexVerificationError as exc:
            log.error("rag_index_rejected", reason=str(exc))
            engine.status = "error"
        except Exception:
            log.exception("rag_load_failed")
            engine.status = "error"
        return engine

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout, connect=min(5.0, self.timeout))
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def retrieve(self, query: str, k: int = 4) -> list[str]:
        if self.vectorstore is None:
            return []
        loop = asyncio.get_running_loop()
        docs = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: self.vectorstore.similarity_search(query, k=k)),
            timeout=self.timeout,
        )
        return [d.page_content for d in docs]

    async def generate(self, prompt: str) -> tuple[str, int]:
        """Return (output_text, context_chunks_used). Raises UpstreamError."""
        try:
            context = await self.retrieve(prompt)
        except Exception as exc:
            raise UpstreamError("retrieval failed") from exc
        full = prompt
        if context:
            joined = "\n\n---\n\n".join(context)
            full = (
                "Answer using the context below. If it is not relevant, say so.\n\n"
                f"Context:\n{joined}\n\nQuestion: {prompt}"
            )
        session = await self._get_session()
        try:
            async with session.post(
                f"{self.ollama_host}/api/generate",
                json={"model": self.ollama_model, "prompt": full, "stream": False},
            ) as resp:
                if resp.status != 200:
                    raise UpstreamError(f"ollama status {resp.status}")
                data = await resp.json()
        except (TimeoutError, aiohttp.ClientError, ValueError) as exc:
            raise UpstreamError("ollama request failed") from exc
        text = data.get("response") if isinstance(data, dict) else None
        if not isinstance(text, str):
            raise UpstreamError("ollama returned no text")
        return text, len(context)


def build_index(source_dir: str, out_dir: str, ollama_host: str, embed_model: str) -> str:
    """Build a FAISS index from .txt/.md files and return the manifest pin."""
    from langchain_community.vectorstores import FAISS  # type: ignore[import-not-found]
    from langchain_core.documents import Document  # type: ignore[import-not-found]
    from langchain_ollama import OllamaEmbeddings  # type: ignore[import-not-found]
    from langchain_text_splitters import RecursiveCharacterTextSplitter  # type: ignore[import-not-found]

    docs = []
    for p in sorted(Path(source_dir).rglob("*")):
        if p.is_file() and p.suffix.lower() in {".txt", ".md"}:
            docs.append(
                Document(
                    page_content=p.read_text(encoding="utf-8", errors="replace"), metadata={"source": str(p)}
                )
            )
    if not docs:
        raise ValueError("no .txt/.md files found")
    chunks = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100).split_documents(docs)
    store = FAISS.from_documents(chunks, OllamaEmbeddings(model=embed_model, base_url=ollama_host))
    store.save_local(out_dir)
    return write_manifest(out_dir)

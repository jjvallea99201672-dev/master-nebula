from __future__ import annotations

import fakeredis.aioredis
import pytest

from lattice_keeper.config import Settings
from lattice_keeper.crypto import KeyRing, load_pqc_backend
from lattice_keeper.rag import RAGEngine
from lattice_keeper.server import create_app

API_KEY = "k" * 40
ENV = {
    "LATTICE_API_KEY": API_KEY,
    "REQUIRE_PQC": "false",
    "RATE_LIMIT_PER_MINUTE": "1000",
    "REFERENCE_CONTROL_VARIABLE": "Canadian Wildlife Conservation Report 2026 (CRC-2026-001)",
    "REFERENCE_REPORT_ID": "CRC-2026-001",
    "REFERENCE_VALUATION_CAD": "3100000000.00",
    "REFERENCE_ANCHOR_HASH": "fd0adf9005622e103f02115a50314d91f83dc705f1d685b16d45f52ad6b940b2",
}
GOOD_BODY = {
    "control_variable": ENV["REFERENCE_CONTROL_VARIABLE"],
    "report_id": "CRC-2026-001",
    "valuation_cad": 3100000000.00,
    "attributes": {"region": "BC"},
}

PQC_BACKEND, PQC_REASON = load_pqc_backend()
requires_pqc = pytest.mark.skipif(PQC_BACKEND is None, reason=f"liboqs unavailable: {PQC_REASON}")


class FakeRAG(RAGEngine):
    def __init__(self, reply: str = "hello world") -> None:
        super().__init__("http://ollama.invalid", "m", 1.0, status="disabled")
        self.reply = reply

    async def generate(self, prompt: str):
        return self.reply, 0


def make_settings(**overrides) -> Settings:
    env = {**ENV, **{k.upper(): str(v) for k, v in overrides.items()}}
    return Settings.from_env(env)


@pytest.fixture
def settings(tmp_path):
    return make_settings(KEYS_DIR=str(tmp_path / "keys"))


@pytest.fixture
def redis_client():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def classical_ring(tmp_path):
    return KeyRing.load_or_create(tmp_path / "ckeys", None, require_pqc=False, pqc=None)


@pytest.fixture
def pqc_ring(tmp_path):
    if PQC_BACKEND is None:
        pytest.skip(f"liboqs unavailable: {PQC_REASON}")
    return KeyRing.load_or_create(tmp_path / "pkeys", None, require_pqc=True, pqc=PQC_BACKEND)


@pytest.fixture
def make_client(aiohttp_client, settings, redis_client, classical_ring):
    async def _make(ring=None, rag=None, settings_=None):
        app = create_app(
            settings_ or settings,
            keyring=ring or classical_ring,
            redis_client=redis_client,
            rag=rag or FakeRAG(),
        )
        return await aiohttp_client(app)

    return _make


def auth(key: str = API_KEY) -> dict:
    return {"Authorization": f"Bearer {key}"}

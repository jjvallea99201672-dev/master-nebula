import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("GRIDMEM_SIG_ALG", "ed25519")
for k in ("XAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
    os.environ.pop(k, None)   # tests must never reach a real API

from gridmem.store import Store  # noqa: E402


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "home")
    yield s
    s.close()

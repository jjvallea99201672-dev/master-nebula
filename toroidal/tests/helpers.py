"""Shared test helpers."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from keys_store import KeyStore
from ledger import ToroidalLedger
from pq_relay_security import InterstellarPQCManager


def temp_dir():
    return tempfile.TemporaryDirectory(prefix="toroidal-test-")


def make_store(directory: str | Path) -> KeyStore:
    return KeyStore(Path(directory) / "keys")


def make_ledger(directory: str | Path) -> ToroidalLedger:
    return ToroidalLedger(path=Path(directory) / "ledger.json")


def make_node(store: KeyStore, node_id: str, sector: str = "test-sector") -> InterstellarPQCManager:
    return InterstellarPQCManager(node_id, sector, keystore=store)

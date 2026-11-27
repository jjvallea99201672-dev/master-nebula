"""Import shim: reuse Lattice Keeper v0.34.1 primitives without modifying them."""
import os
import sys
from pathlib import Path

KEEPER_DIR = Path(os.environ.get("GRIDMEM_KEEPER_DIR", "/workspace/lattice-keeper/v034"))
if str(KEEPER_DIR) not in sys.path:
    sys.path.insert(0, str(KEEPER_DIR))
os.environ.setdefault("OQS_INSTALL_PATH", str(KEEPER_DIR / ".oqs"))

from lattice_keeper_v034 import (  # noqa: E402
    CLAIMED_GENESIS_ANCHOR, VERSION as KEEPER_VERSION, MerkleTree, PQCSigner, canon,
    pqc_available, sha256_hex,
)

__all__ = ["CLAIMED_GENESIS_ANCHOR", "KEEPER_VERSION", "MerkleTree", "PQCSigner", "canon",
           "pqc_available", "sha256_hex", "KEEPER_DIR"]

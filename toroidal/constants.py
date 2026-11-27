"""Shared constants for the Toroidal Network toolkit."""

from pathlib import Path

# New Brunswick root hash — the genesis anchor for every module.
GENESIS_ANCHOR = "fd0adf9005622e103f02115a50314d91f83dc705f1d685b16d45f52ad6b940b2"

LEDGER_SIZE = 65536
GRID_SIDE = 256  # 256 x 256 torus; LEDGER_SIZE == GRID_SIDE ** 2
PRECURSOR_TARGET_CELL = 65500
ROOT_NODE_ID = "NB-ROOT"

PACKAGE_DIR = Path(__file__).resolve().parent
KEY_DIR = PACKAGE_DIR / "keys"
DATA_DIR = PACKAGE_DIR / "data"
LEDGER_PATH = DATA_DIR / "ledger.json"
PUBLIC_INDEX = KEY_DIR / "index.json"

KEM_NAME = "ML-KEM-768"
DSA_NAME = "ML-DSA-65"
AEAD_NAME = "AES-256-GCM"
SESSION_KDF_INFO_PREFIX = b"toroidal-session-v1"

# NIST FIPS 203 (ML-KEM-768) and FIPS 204 (ML-DSA-65) sizes in bytes.
ML_KEM_768_EK_SIZE = 1184
ML_KEM_768_DK_SIZE = 2400
ML_KEM_768_CT_SIZE = 1088
ML_KEM_768_SS_SIZE = 32
ML_DSA_65_PK_SIZE = 1952
ML_DSA_65_SK_SIZE = 4032
ML_DSA_65_SIG_SIZE = 3309

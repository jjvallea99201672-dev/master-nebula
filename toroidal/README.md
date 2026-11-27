# Toroidal Network toolkit (Series 4)

A working Python implementation of Jay's Series 4 modular toolkit: post-quantum
relays, exploratory beacons, habitability audits, seeding drops, and the
Sector 51 precursor bridge, all writing into one signed 65,536-cell ledger.

This is a **learning prototype, not audited security software**. Do not use it
to protect real secrets, real money, or a real network.

## What is real vs fiction

**Real (runs as written):**

- ML-KEM-768 key encapsulation (NIST FIPS 203) via [`kyber-py`](https://github.com/GiacomoPope/kyber-py) 1.2.0, a pure-Python implementation.
- ML-DSA-65 signatures (NIST FIPS 204) via [`dilithium-py`](https://github.com/GiacomoPope/dilithium-py) 1.5.1, also pure Python.
- Session keys derived with HKDF-SHA256, then AES-256-GCM, both from the `cryptography` package.
- A 65,536-cell ledger stored as JSON. Each entry carries a payload hash, a previous-entry hash (hash chain), the signer's ML-DSA-65 signature, a timestamp, and torus neighbors. `verify_chain()` rechecks every signature and link.
- Cell placement is `int(payload_hash, 16) % 65536` with linear probing on collision (wrap-around).
- The precursor bridge returns `True` only when the signature verifies **and** either the hash naturally lands in cell 65,500, or the root node `NB-ROOT` authorizes a reserved placement of that cell. Otherwise it returns `False` with a reason.
- Private keys are written under `keys/` with mode `0600` and are never printed or logged.

**Fiction (worldbuilding only):**

- 432 Hz "acoustic" pings mapping gravitational topography through interstellar space. Real vacuum cannot carry a 432 Hz sound wave. If you want the lore to hold up, treat those pings as gravitational or plasma waves that the ship turns into sound — the same trick real astronomers use with LIGO and spacecraft plasma-wave data.
- The habitability rule (`|natural_resonance_hz - 432| < 5` and `tectonic_stability_score > 0.8`). Planets do not have a single natural 432 Hz frequency. Real habitability depends on insolation, atmosphere, liquid water, and geochemistry.
- "Piezoelectric stabilization," the living acoustic star map, and the claim that a Sector 51 bismuth-quartz beacon is SHA-256-isomorphic with the New Brunswick genesis anchor.

The genesis anchor used throughout is the New Brunswick root hash:

`fd0adf9005622e103f02115a50314d91f83dc705f1d685b16d45f52ad6b940b2`

## Setup

```bash
cd /workspace/code/toroidal
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

A venv may already be present at `.venv/` with the dependencies installed.

## Run the demo

```bash
cd /workspace/code/toroidal
.venv/bin/python demo.py
```

The demo stands up three nodes (`NB-ROOT`, `OUTPOST-SABAOTH`, `BEACON-01`), opens
an ML-KEM session and decrypts a message, commits KV-17 beacon scans (loaded
from `/workspace/code/beacons/ledger.json` when that file is present), audits
Nyx, performs a seeding drop, shows a precursor-bridge failure then a reserved
placement into cell 65,500, verifies the chain, and demonstrates a tamper and a
forged signature being rejected.

The shared ledger is written to `data/ledger.json`. Keys stay in `keys/`.

## Run the tests

```bash
cd /workspace/code/toroidal
.venv/bin/python -m unittest discover -s tests -v
```

Coverage includes a KEM round-trip, signature accept/reject, ledger chain
integrity, linear-probe collisions (including wrap-around), torus neighbors,
and precursor-bridge pass/fail cases.

## Layout

```
toroidal/
  pq_relay_security.py   InterstellarPQCManager, QuantumSession
  beacon_deployment.py   ExploratoryWaveBeacon
  exoplanet_audit.py     ExoplanetHabitibilityAuditor  (spelling kept from the source)
  seed_dispersal.py      ExoplanetSeedingProtocol
  precursor_bridge.py    PrecursorLedgerBridge
  ledger.py              shared 65,536-cell signed hash-chain ledger
  keys_store.py          0600 private key files, public index only
  crypto_utils.py        canonical JSON, SHA-256, HKDF, AES-GCM
  constants.py
  demo.py
  tests/
  keys/                  private keys (0600) — do not share
  data/ledger.json
```

`ExploratoryWaveBeacon.map_gravitational_anomaly` is kept as an alias of
`map_gravitational_topography` so this package lines up with
`/workspace/code/beacons/beacon_mapping.py`.

## Key and signature sizes (NIST)

| Algorithm   | Piece              | Bytes |
|-------------|--------------------|------:|
| ML-KEM-768  | Encapsulation key  |  1184 |
| ML-KEM-768  | Decapsulation key  |  2400 |
| ML-KEM-768  | Ciphertext         |  1088 |
| ML-KEM-768  | Shared secret      |    32 |
| ML-DSA-65   | Public key         |  1952 |
| ML-DSA-65   | Secret key         |  4032 |
| ML-DSA-65   | Signature          |  3309 |
| AES-256-GCM | Session key / nonce| 32 / 12 |

## Reserved-cell mechanism

A payload almost never hashes naturally into cell 65,500 (odds are 1 in 65,536).
The bridge therefore allows an explicit reserved placement **only when**:

1. the ML-DSA-65 signature over `canonical_json(payload) + genesis_anchor` verifies, and
2. the signer is the root node `NB-ROOT`, and
3. `payload["reserved_cell"] == 65500`, and
4. that cell is free.

Anyone else claiming cell 65,500 is rejected, even with a valid signature of their own.

## Caveats

- `kyber-py` and `dilithium-py` are carefully written pure-Python ports of the
  FIPS 203/204 algorithms, but they are not a formal product audit, and neither
  is this toolkit.
- Private keys live on this machine's disk. Anyone with access to `keys/*.sk`
  or `keys/*.dk` can impersonate that node.
- The ledger is an unencrypted JSON file. Signatures detect tampering; they do
  not hide contents.
- Pure-Python PQC is slower than liboqs / hardware implementations. Fine for
  demos and tests, not for a high-rate service.

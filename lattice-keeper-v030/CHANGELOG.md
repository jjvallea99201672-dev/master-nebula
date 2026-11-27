# Changelog

Copyright (c) 2026 Jay Vallea. All Rights Reserved.

## 0.30.0 — 2026-10-21

### Added
- **Operator-signed, versioned policy** (`src/lattice_keeper/policy.py`). One canonical JSON document
  (`format`, `version`, `prev_hash`, `effective_at`, `note`, `values`) holds every configurable rule:
  guardian thresholds (`temp_max_c`, `temp_hyst_c`, `q_deltas_above`, `q_beta_above`,
  `q_coherence_below`), pump levels, `beta_default`, `beta_min`/`beta_max`, `beta_global` (global and
  per device) and the `/rag_guard` blocklist. Strict types and hard bounds are enforced on the server
  and on the device.
- **Operator key** (`src/lattice_keeper/operator.py`), separate from the server signing key: hybrid
  Ed25519 + ML-DSA-65 when liboqs is available, Ed25519-only otherwise. Private key files are
  scrypt + AES-256-GCM sealed. Signatures use their own context `lattice-keeper/policy/v1:`, so server
  and operator signatures can't be swapped.
- **Genesis policy v1** built from the environment (`TELEMETRY_BETA_GLOBAL`, `GUARDIAN_BLOCKLIST`) and the
  firmware v0.29 defaults the first time the server starts with an empty store (unsigned; audited as
  `policy_genesis`).
- `GET /policy` (admin or device key; `?view=device` is the compact form for the ESP32) and
  `PUT /policy` (admin key + valid operator signature, `version == current + 1`,
  `prev_hash == current hash`, sane `effective_at`; atomic compare-and-set in Redis).
- Every accepted and rejected policy attempt is appended to the signed audit chain
  (`policy_accepted` / `policy_rejected` with reason). Unauthenticated attempts are audited, capped at
  10 per minute.
- CLI: `operator-keygen` (also `keygen --operator`), `policy-next`, `policy-sign`, `policy-verify`.
- Config: `OPERATOR_PUBKEY_FILE` or `OPERATOR_ED25519_PUBKEY` [+ `OPERATOR_ML_DSA_65_PUBKEY`],
  `POLICY_CLOCK_SKEW_SECONDS`.
- Startup guards: refuse operator private keys in `KEYS_DIR`, an operator key equal to the server key, a
  hybrid operator key without liboqs, and (with `REQUIRE_PQC=true`) an Ed25519-only operator key.
- `/health` shows the active policy version and hash; `/pubkeys` shows the operator public key; `/rag_guard`
  results record the policy version; `POST /telemetry` responses include `policy_version`,
  `policy_sha256` and `policy_in_sync` (telemetry may send `policy_version` / `policy_sha256`).
- `PUT /policy` responses include `device_view_bytes` / `fits_device_buffer`.
- Firmware `firmware/guardian_v030/` (replaces `guardian_v029/`): optional on-device Ed25519 verification
  of the policy (rweather Crypto), anti-rollback, bounds checks, last-known-good policy in NVS (re-verified
  at boot), and fetching on boot, every 10 min and on a hash change. Host test in `host_test/`.
- Tests: `tests/test_policy.py` (accept, bad/impostor/wrong-context signatures, replay, rollback, version
  gap, wrong prev_hash, unauthenticated and device-key attempts, audit cap, bounds/strict types,
  future/regressed `effective_at`, runtime application, restart persistence, CLI round trip, hybrid
  signature and ML-DSA stripping, startup guards).

### Changed
- **`PUT /telemetry/beta_global` returns 410 Gone** (and is audited). `beta_global` is now part of the
  signed policy because it drives the device's `beta > q_beta_above` quarantine rule. v0.29 Redis keys
  `telemetry:beta_global*` are ignored (warning at startup).
- `TELEMETRY_BETA_GLOBAL` and `GUARDIAN_BLOCKLIST` are only read for the genesis policy.
- Device keys may now also call `GET /policy`.
- The audit log gains one `policy_genesis` entry on first start.
- `verify_envelope()` takes an optional signing context (default unchanged).

### Not done / known limits
- Devices verify only the Ed25519 half of a hybrid operator signature (no ML-DSA-65 on the ESP32).
- No scheduled (future-dated) policies, no multi-signature or key rotation command (rotating means pinning
  a new public key and restarting).
- Firmware compiled only (core 3.3.12 and 2.0.17); not tested on hardware.

## 0.29.0
- Hardened rewrite of v0.28 (real hybrid signatures, ML-KEM-768 encryption, signed hash-chained audit log,
  scoped device keys, telemetry endpoint, Docker, tests). See README "What changed from v0.28".

# Lattice Keeper v0.30

Copyright (c) 2026 Jay Vallea. All Rights Reserved.

A small HTTP service that **signs records** with a hybrid Ed25519 + ML-DSA-65
(post-quantum) signature, optionally **encrypts** them with ML-KEM-768, keeps a
**signed, hash-chained audit log** in Redis, and offers a prompt endpoint
(`/rag_guard`) that runs a local Ollama model behind a **keyword heuristic**
filter, with optional FAISS retrieval. Since v0.30, the rules that devices and
the server apply (guardian thresholds, pump levels, beta, blocklist) live in a
**versioned policy document that only the operator's offline key can change**
(see [Operator-signed policy](#operator-signed-policy-v030)).

This is the hardened version of v0.28. Everything it claims to do, it really
does. Anything it doesn't do is listed under [Limitations](#limitations).

---

## What changed in v0.30

- **Operator-signed policy.** A canonical JSON policy (`version`, `prev_hash`, `effective_at`,
  `values`) holds every configurable rule. A separate **operator key** (hybrid Ed25519 + ML-DSA-65,
  or Ed25519-only without liboqs) signs it offline; the server only has the operator *public* key.
  `PUT /policy` accepts a change only if the signature verifies, `version` is exactly current + 1
  and `prev_hash` equals the current policy hash. Every accepted and rejected attempt goes into the
  signed audit chain.
- **`PUT /telemetry/beta_global` now returns 410.** `beta_global` feeds the device's
  `beta > q_beta_above` quarantine rule, so it moved into the signed policy (see below).
- New CLI: `operator-keygen` (alias `keygen --operator`), `policy-next`, `policy-sign`, `policy-verify`.
- `GET /policy` (admin or device key), `/health` and `/pubkeys` show the active policy and operator key.
- Firmware `guardian_v030`: optional on-device Ed25519 verification of the policy, anti-rollback,
  last-known-good policy in NVS. Details in `firmware/guardian_v030/README.md`.

See `CHANGELOG.md` for the full list.

## What changed from v0.28

| v0.28 | v0.29 |
|---|---|
| HMAC made with a new random key each time and then thrown away, so it could never be checked | Removed. Signatures are real, **verifiable** Ed25519 (+ ML-DSA-65), with public keys at `GET /pubkeys` and checking at `POST /verify` |
| Without liboqs it returned the text `SIMULATED_ML_DSA_65_SIGNATURE` and base64 that *looked like* encryption | Never fakes. Default `REQUIRE_PQC=true` refuses to start without liboqs. With `REQUIRE_PQC=false`, responses say `"pqc": "unavailable"` and include no ciphertext |
| KEM "encryption" | Real ML-KEM-768 → HKDF-SHA256 → AES-256-GCM, with a documented wire format |
| Private keys stored in plain files | Created atomically with mode `0600` in a `0700` directory, optionally encrypted with `LATTICE_KEY_PASSPHRASE` (scrypt + AES-256-GCM) |
| `ForensicAnchor` with a hardcoded hash, report ID and a 3.1B valuation | A **configurable reference-value check** (env vars), honestly described as a consistency check, *not an audit* |
| "Post-Quantum Bitcoin Anchoring" in the title | Removed: **no Bitcoin anchoring exists in this code** (see Limitations) |
| `allow_dangerous_deserialization=True` on any index folder | An index loads only after its SHA-256 manifest matches a hash you pin in config |
| Guardian called "AI Safety Layer" | Labelled as a heuristic keyword filter, now with whole-word matching |
| Raw exception text sent to clients | Generic JSON errors with a `request_id`; details go only to the server log |
| No rate limit, no input validation | Per-key Redis rate limit, pydantic v2 models, body size limit, timeouts on Ollama calls |
| Single file | `src/` package, CLI, tests, Dockerfile, docker-compose |
| (ESP32 sketch v0.28.4) | `firmware/guardian_v029/` (now `guardian_v030/`): TLS-pinned telemetry to `POST /telemetry`, pump interlock, watchdog |

---

## Quick start (Docker)

```bash
cp .env.example .env
# fill in LATTICE_API_KEY (>= 32 chars), REDIS_PASSWORD, LATTICE_KEY_PASSPHRASE
python -c 'import secrets; print(secrets.token_urlsafe(32))'
docker compose up --build -d
curl -s localhost:8765/health
```

The image compiles liboqs 0.16.0 and liboqs-python in a builder stage. The
build fails if ML-DSA-65 or ML-KEM-768 can't be loaded. The final image (about 200 MB) is
`python:3.12-slim` and runs as UID 10001 with a read-only root filesystem.
Keys live in the `keys` volume, so **back that volume up**. If you lose the
keys, nobody can verify old signatures or decrypt old records.

Smoke test against the running stack (uses the key from `.env`):

```bash
scripts/smoke.sh      # health, pubkeys, auth, tokenize, get, verify valid/tampered/stripped, audit
```

### Optional: retrieval + local Ollama

`docker-compose.rag.yml` adds the `[rag]` extra (image is about 440 MB instead of about 200 MB),
an index volume, and the `ollama` service. Inside the stack `OLLAMA_HOST` defaults to
`http://ollama:11434`. Set `COMPOSE_OLLAMA_HOST` to point somewhere else.

```bash
F="-f docker-compose.yml -f docker-compose.rag.yml --profile ollama"
docker compose $F up -d ollama
docker compose $F exec ollama ollama pull qwen2.5:0.5b      # or any chat model
docker compose $F exec ollama ollama pull all-minilm        # embedding model
# in .env: OLLAMA_MODEL=qwen2.5:0.5b  RAG_EMBED_MODEL=all-minilm
mkdir -p rag-docs && cp your-docs/*.md rag-docs/
docker compose $F run --rm lattice-keeper rag-build --source /docs --out /data/rag/index
# copy the printed RAG_INDEX_MANIFEST_SHA256=... into .env, then
docker compose $F up -d
curl -s localhost:8765/health    # "rag": "enabled"
```

If the pin is missing or wrong, `/health` reports `"rag": "error"` and the index is not loaded.

## Quick start (local)

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"            # add ,pqc and/or ,rag as needed
# PQC: build liboqs (https://github.com/open-quantum-safe/liboqs) and
export OQS_INSTALL_PATH=/path/to/liboqs/install   # must contain lib/liboqs.so
pip install -e ".[pqc]"
export LATTICE_API_KEY=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')
lattice-keeper keygen      # creates/loads keys, prints public keys
lattice-keeper serve
```

> liboqs-python normally tries to download and compile liboqs **when it is
> imported** if it can't find the library. Lattice Keeper checks for the
> library first and never imports `oqs` without it, so a running server
> will never start a surprise build.

### CLI

| Command | Purpose |
|---|---|
| `lattice-keeper serve` | Run the HTTP server (shuts down gracefully on SIGTERM/SIGINT) |
| `lattice-keeper keygen` | Create any missing keys and print the public keys |
| `lattice-keeper verify-audit` | Check the whole audit chain and its signatures (exit code 1 if tampered) |
| `lattice-keeper rag-build --source DOCS --out INDEX` | Build a FAISS index from `.txt`/`.md` files and print the manifest hash to pin |
| `lattice-keeper rag-manifest INDEX` | Write a manifest for an index you already trust |
| `lattice-keeper operator-keygen --out DIR` | Create the **offline** operator key (`keygen --operator` does the same). Hybrid if liboqs is available; `--no-pqc` for Ed25519-only |
| `lattice-keeper policy-next --current FILE --out FILE` | Draft version N+1 from a saved `GET /policy` response (sets `version`, `prev_hash`, `effective_at`) |
| `lattice-keeper policy-sign FILE --key-dir DIR --out FILE` | Validate and sign a policy with the operator key; output is the body for `PUT /policy` |
| `lattice-keeper policy-verify FILE --pubkey operator_pubkey.json` | Verify a signed policy offline |

---

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `LATTICE_API_KEY` | **required** | One or more comma-separated keys, each at least 32 characters |
| `DEVICE_API_KEYS` | unset | Comma-separated device keys (each at least 32 chars), allowed **only** `POST /telemetry` and `GET /policy` |
| `OPERATOR_PUBKEY_FILE` | unset | Path to `operator_pubkey.json` from `operator-keygen`. Without an operator key, every `PUT /policy` is rejected |
| `OPERATOR_ED25519_PUBKEY` / `OPERATOR_ML_DSA_65_PUBKEY` | unset | Inline base64 alternative to `OPERATOR_PUBKEY_FILE` (not both) |
| `POLICY_CLOCK_SKEW_SECONDS` | `300` | How far in the future `effective_at` may be (clock skew allowance) |
| `TELEMETRY_BETA_GLOBAL` | unset | Genesis policy only: initial `values.telemetry.beta_global` (0.1–5.0) |
| `TELEMETRY_HISTORY` | `1000` | Telemetry samples kept per device |
| `LATTICE_KEY_PASSPHRASE` | unset | Encrypts private keys at rest (scrypt N=2^15 → AES-256-GCM). If unset, keys are stored unencrypted with mode 0600 |
| `KEYS_DIR` | `./keys` | Key directory (created with mode 0700) |
| `REQUIRE_PQC` | `true` | Refuse to start without liboqs. If `false`, signs with Ed25519 only and marks responses `pqc: unavailable` |
| `OQS_INSTALL_PATH` | `~/_oqs` | Prefix that contains `lib/liboqs.so` |
| `API_HOST` / `API_PORT` | `0.0.0.0` / `8765` | Listen address |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `REDIS_CONNECT_RETRIES` | `10` | Startup connection attempts, with exponential backoff up to 10 s |
| `RATE_LIMIT_PER_MINUTE` | `60` | Requests per API key per minute (fixed window, stored in Redis) |
| `MAX_BODY_BYTES` | `65536` | Request body limit (larger bodies get 413) |
| `RECORD_TTL_SECONDS` | 10 years | How long tokenized records are kept in Redis |
| `REFERENCE_ANCHOR_HASH` | unset | 64-hex reference hash, echoed into signed payloads and `/pubkeys` |
| `REFERENCE_CONTROL_VARIABLE` | unset | If set, requests must match it exactly |
| `REFERENCE_REPORT_ID` | unset | If set, requests must match it exactly |
| `REFERENCE_VALUATION_CAD` | unset | If set, `valuation_cad` must be within the tolerance |
| `REFERENCE_VALUATION_TOLERANCE` | `0.01` | Allowed difference for the valuation |
| `GUARDIAN_BLOCKLIST` | built-in list | Genesis policy only: initial `values.rag_guard.blocklist` |
| `OLLAMA_HOST` / `OLLAMA_MODEL` | `http://localhost:11434` / `llama3.2` | Model backend |
| `OLLAMA_TIMEOUT_SECONDS` | `30` | Total timeout for retrieval and generation |
| `RAG_ENABLED` | `false` | Turn retrieval on (needs the `[rag]` extra) |
| `RAG_INDEX_DIR` | unset | FAISS index directory |
| `RAG_INDEX_MANIFEST_SHA256` | unset | Pinned SHA-256 of `manifest.json`. Without it the index is **not** loaded |
| `RAG_EMBED_MODEL` | `nomic-embed-text` | Ollama embedding model |
| `SHUTDOWN_TIMEOUT_SECONDS` | `15` | Grace period for in-flight requests on SIGTERM |
| `LOG_LEVEL` | `INFO` | Logs are JSON lines on stdout |

`TELEMETRY_BETA_GLOBAL` and `GUARDIAN_BLOCKLIST` are read **only** to build policy v1 the
first time the server starts with an empty Redis. After that, change them with a signed policy;
editing the env has no effect. With `REQUIRE_PQC=true` the operator key must be hybrid
(Ed25519 + ML-DSA-65), otherwise the server refuses to start.

`.env.example` sets the reference values to the ones hardcoded in v0.28.
Change them, or leave them blank to turn the check off (`reference_check.status`
will then be `not_configured`).

---

## API

Every endpoint except `GET /health` and `GET /pubkeys` needs
`Authorization: Bearer <key>` (or `X-API-Key: <key>`). Errors always look like
`{"error": "...", "message": "...", "request_id": "..."}`. Send your own
`X-Request-ID` header if you want to match a request to the server logs.

```bash
export KEY=...   # your LATTICE_API_KEY
export URL=http://localhost:8765
```

### `GET /health` (no auth)
```bash
curl -s $URL/health
# {"status":"ok","version":"0.30.0","redis":"ok","pqc":"ml-dsa-65+ml-kem-768","rag":"disabled",
#  "guardian":"heuristic_keyword_filter","policy":{"version":2,"sha256":"…","source":"operator"},"operator_key":"pinned"}
```
Returns 503 with `"status":"degraded"` if Redis can't be reached.

### `GET /pubkeys` (no auth)
Returns the Ed25519, ML-DSA-65 and ML-KEM-768 public keys (base64), their
key IDs (the first 16 hex characters of the key's SHA-256), the
configured reference anchor hash, and the pinned operator public key (`operator`).

### `POST /rwa/tokenize`
```bash
curl -s -X POST $URL/rwa/tokenize -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' -d '{
  "control_variable": "Canadian Wildlife Conservation Report 2026 (CRC-2026-001)",
  "report_id": "CRC-2026-001",
  "valuation_cad": 3100000000.00,
  "attributes": {"region": "BC"}
}'
```
Body fields: `control_variable` (≤256 chars), `report_id` (`[A-Za-z0-9._:-]`, ≤64),
`valuation_cad` (decimal, 0 to 1e15, 2 decimal places), `attributes` (≤50 scalar
values), and `recipient_kem_public_key` (optional base64 ML-KEM-768 key, used
to encrypt the record to a third party instead of to this server). Unknown
fields are rejected with 422.

Response (201): `token_id`, `payload` (the exact object that was signed),
`signature` (envelope), `encryption` (KEM envelope, or
`{"status":"unavailable"}`), `pqc`, and `reference_check`. A reference
mismatch returns 403.

### `GET /rwa/{token_id}`
Returns the stored payload, signature and encryption envelope.

### `POST /verify`
```bash
curl -s -X POST $URL/verify -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"payload": { ...payload from tokenize... }, "signature": { ...signature... }}'
# {"valid":true,"payload_hash_match":true,"ed25519":true,"ml_dsa_65":true}
```
If the server has an ML-DSA-65 key, the PQC signature is **required**, so
stripping it to downgrade to Ed25519-only gives `valid: false`.

### `POST /rag_guard`
```bash
curl -s -X POST $URL/rag_guard -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"prompt": "Summarise the conservation report", "metadata": {"user": "jay"}}'
```
The prompt is checked first; if it matches the blocklist, the model is never
called. Otherwise the server runs retrieval (if enabled), calls Ollama, and
checks the output too. The response holds a signed `result` with
`allowed`, `output` (null when blocked), the guardian's matches, and RAG
status. If Ollama is down or times out, you get a generic 502. The blocklist comes from the
active policy (`values.rag_guard.blocklist`), and the signed result records `policy.version`.

### `POST /telemetry` (device or admin key)
Sensor telemetry from the ESP32 Guardian firmware (`firmware/guardian_v030/`). The body is validated
strictly: ranges for every field, `pump_interlock` and `guardian.state` from fixed lists, no NaN, no
unknown fields. The latest sample and a capped history (`TELEMETRY_HISTORY`) are stored in Redis.
Guardian state changes (nominal ↔ quarantine) are written to the audit log.
```bash
curl -s -X POST $URL/telemetry -H "Authorization: Bearer $DEVICE_KEY" -H 'Content-Type: application/json' -d '{
  "device_id":"lk-24DCC3A1B2C4","fw":"0.30.0","seq":42,"uptime_ms":210000,"sensor_ok":true,"temp_c":23.5,
  "radiant_raw":2048,"radiant_norm":0.5,"pump_duty":0.42,"pump_interlock":"ok","tau":3.1416,"beta":1.0,
  "guardian":{"state":"nominal","reason":""},"rssi":-61}'
# {"status":"accepted","seq":42,"beta_global":null,"policy_version":2,"policy_sha256":"…",
#  "policy_in_sync":true,"received_at":"..."}
```
`beta_global` comes from the active policy (`values.telemetry.beta_global_devices[device]` >
`values.telemetry.beta_global`), clamped to the policy's `beta_min`–`beta_max`. `null` means the
device uses its own default. Devices may send `policy_version` / `policy_sha256` (the policy they
apply); `policy_in_sync` says whether it matches the server's. Firmware v0.30 with policy
verification on ignores this unsigned `beta_global` and uses the verified policy instead.

**Device keys** (`DEVICE_API_KEYS`) are accepted **only** for `POST /telemetry` and `GET /policy`.
Anything else gets 403.

### `GET /telemetry/{device_id}` (admin key)
```bash
curl -s $URL/telemetry/lk-24DCC3A1B2C4 -H "Authorization: Bearer $KEY"
```

### `PUT /telemetry/beta_global` — removed (410 Gone)
In v0.29 any admin key could change `beta_global`, which can push a device into quarantine
(`beta > q_beta_above`). Two designs were possible: keep the endpoint but require an operator
signature on each call, or fold the value into the signed policy. v0.30 does the latter: one signed,
versioned, hash-chained document is easier to audit and replay-protect than a second signed-request
format, and devices can verify it themselves. The endpoint now returns 410 and the attempt is audited.
Old `telemetry:beta_global*` keys left in Redis by v0.29 are ignored (a warning is logged at startup);
put those values into a signed policy.

### `GET /policy` (admin or device key) and `PUT /policy` (admin key + operator signature)
See [Operator-signed policy](#operator-signed-policy-v030).

### `GET /audit/verify`
```bash
curl -s $URL/audit/verify -H "Authorization: Bearer $KEY"
# {"valid":true,"entries":42,"head_hash":"..."}
```

---

## Operator-signed policy (v0.30)

### The document
```json
{
  "format": "lattice-keeper-policy/v1",
  "version": 2,
  "prev_hash": "<sha256 of policy v1's canonical JSON>",
  "effective_at": "2026-10-21T12:00:00Z",
  "note": "lower the dS quarantine threshold",
  "values": {
    "guardian": {"temp_max_c": 60.0, "temp_hyst_c": 5.0, "q_deltas_above": 55.0, "q_beta_above": 3.0,
                 "q_coherence_below": 90.0, "pump_level_base": 80, "pump_level_c150": 130,
                 "pump_level_c200": 190, "pump_level_deltas": 220, "pump_deltas_above": 70.0,
                 "beta_default": 1.5},
    "telemetry": {"beta_min": 0.1, "beta_max": 5.0, "beta_global": null,
                  "beta_global_devices": {"lk-24DCC3A1B2C4": 1.2}},
    "rag_guard": {"blocklist": ["bypass", "jailbreak", "ignore previous instructions", "exploit"]}
  }
}
```
`policy_sha256 = sha256(canonical_json(policy))` (sorted keys, `,`/`:` separators, UTF-8).
Types are strict: pump levels must be JSON integers, thresholds numbers, no strings for numbers.

| Field | Hard bounds (server **and** device) | Applied by |
|---|---|---|
| `guardian.temp_max_c` / `temp_hyst_c` | 30–80 °C / 1–20 °C | firmware over-temperature trip |
| `guardian.q_deltas_above`, `q_beta_above`, `q_coherence_below` | 0–100, 0.1–5, 40–255 | firmware quarantine rules |
| `guardian.pump_level_*` | integers 0–255 | firmware pump levels |
| `guardian.pump_deltas_above` | 0–100 | firmware pump level switch |
| `guardian.beta_default` | 0.1–5 | firmware beta when no override |
| `telemetry.beta_min/max`, `beta_global`, `beta_global_devices` (≤64) | 0.1–5, overrides within min–max | server (`POST /telemetry` response) and firmware |
| `rag_guard.blocklist` (≤200 terms, ≤64 chars) | — | server `/rag_guard` |

No policy can turn off the firmware's sensor-fault / stale-sensor interlock, its re-arm logic or
the soft-start; those stay compiled in.

**Genesis (v1).** On first start with an empty store, the server builds v1 from the environment
(`TELEMETRY_BETA_GLOBAL`, `GUARDIAN_BLOCKLIST`) and the firmware v0.29 defaults, with
`prev_hash` = 64 zeros. v1 is **unsigned** (the server never has the operator key); its creation is
recorded in the audit chain (`policy_genesis`). Devices with verification on don't apply v1; they keep
their compiled defaults (the same values) until an operator-signed v2 exists.

### Workflow
```bash
# 1. once, on the operator's own machine (NOT the server). Use a passphrase.
lattice-keeper operator-keygen --out ~/lk-operator        # prompts, or set OPERATOR_KEY_PASSPHRASE
#    -> ~/lk-operator/operator_pubkey.json  (give ONLY this to the server: OPERATOR_PUBKEY_FILE=...)
#    -> prints the Ed25519 key for the firmware's LK_OPERATOR_ED25519_PUBKEY_B64

# 2. each change
curl -s $URL/policy -H "Authorization: Bearer $KEY" > current.json
lattice-keeper policy-next --current current.json --out proposal.json --note "lower dS threshold"
$EDITOR proposal.json                                     # change values only
lattice-keeper policy-sign proposal.json --key-dir ~/lk-operator --out signed.json
curl -s -X PUT $URL/policy -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' --data @signed.json
# {"status":"accepted","version":2,"policy_sha256":"…","prev_hash":"…","audit_seq":17,
#  "device_view_bytes":1012,"fits_device_buffer":true}
```
In Docker, mount the public key read-only, e.g. add `- ./operator_pubkey.json:/run/operator_pubkey.json:ro`
under `volumes:` and set `OPERATOR_PUBKEY_FILE=/run/operator_pubkey.json` in `.env`.

### `PUT /policy` rules
Body: `{"policy": {...}, "signature": {...}}` exactly as written by `policy-sign`.

| Check | Failure |
|---|---|
| Admin key | 401 (no or unknown key), 403 (device key) |
| Operator key pinned on the server | 409 `operator_key_not_configured` |
| Schema, types and hard bounds | 422 `validation_error` |
| Operator signature (both halves if the pinned key is hybrid; stripping ML-DSA is detected) | 403 `signature_invalid` |
| `version` > current (no replay / rollback) | 409 `stale_version` |
| `version` == current + 1 | 409 `version_gap` |
| `prev_hash` == current `policy_sha256` | 409 `prev_hash_mismatch` |
| `effective_at` ≤ now + `POLICY_CLOCK_SKEW_SECONDS`, and ≥ current `effective_at` | 422 `effective_at_in_future` / `effective_at_regressed` |

The check-and-write is atomic (Redis `WATCH`), so two concurrent valid v2s can't both win.
A change takes effect on the server immediately; `effective_at` is a signed timestamp, and
future-dated (scheduled) policies are not supported in v0.30.

**Audit.** Every attempt is appended to the signed audit chain: `policy_genesis`, `policy_accepted`
(version, hash, prev_hash, signer key ID) and `policy_rejected` (reason, status, claimed version,
submitted hash). Unauthenticated / device-key attempts are audited too, capped at 10 per minute so
anonymous requests can't grow the log without limit (`attempts_this_minute` keeps the count).
`verify-audit` and `GET /audit/verify` check these entries like any others.

### `GET /policy`
Full view: `status` (`operator_signed` / `genesis_unsigned`), `version`, `policy_sha256`, `policy`,
`canonical` (the exact signed bytes), `signature`, `accepted_at`, `operator_key`.
`GET /policy?view=device` is the compact form for the ESP32: `canonical`, `policy_sha256`, `version`,
and only the Ed25519 half of the signature (`ed25519_sig`), because the ML-DSA-65 signature
(about 4.4 KB base64) doesn't fit, and isn't verifiable, on the device.

### Server safety checks at startup
- refuses to start if any `operator_*.key` file is in `KEYS_DIR` (the server must not hold it);
- refuses an operator key equal to the server signing key;
- refuses a hybrid operator key if liboqs is unavailable (no policy could ever verify);
- with `REQUIRE_PQC=true`, refuses an Ed25519-only operator key.

---

## Formats

**Policy signature.** Same envelope, plus `"signer":"operator"`, over
`b"lattice-keeper/policy/v1:" + sha256_hex(canonical_json(policy))`. The different context
string means a server signature can never pass as an operator signature, or the reverse.

**Signature envelope.** The signed message is
`b"lattice-keeper/sig/v1:" + sha256_hex(canonical_json(payload))`, where
`canonical_json` means sorted keys, `(",", ":")` separators and UTF-8.
```json
{"v":1,"alg":"hybrid:Ed25519+ML-DSA-65","pqc":"ml-dsa-65","payload_sha256":"…",
 "ed25519":{"key_id":"…","sig":"<b64>"},"ml_dsa_65":{"key_id":"…","sig":"<b64>"}}
```
Anyone can verify offline with the keys from `/pubkeys` (Ed25519 via any
crypto library; ML-DSA-65 via liboqs or another FIPS 204 implementation).

**KEM envelope (`lk-kem-v1`).**
```
ss, kem_ct = ML-KEM-768.Encaps(recipient_pk)
key        = HKDF-SHA256(ikm=ss, salt=kem_ct, info="lattice-keeper/kem/v1 ML-KEM-768 AES-256-GCM", len=32)
ct         = AES-256-GCM(key, nonce=12 random bytes, plaintext=canonical payload, aad=token_id)
```
```json
{"status":"encrypted","format":"lk-kem-v1","alg":"ML-KEM-768+HKDF-SHA256+AES-256-GCM",
 "recipient_key_id":"…","kem_ciphertext":"<b64>","nonce":"<b64>","ciphertext":"<b64, includes tag>","aad":"<b64>"}
```

**Audit entries** are stored in the Redis list `audit:log`:
`{seq, ts, event, prev_hash, hash, signature}`. Here `hash` is the SHA-256
of the canonical `{seq, ts, event, prev_hash}`, and `signature` is an envelope
over the same bytes. `audit:head` stores the last `{seq, hash}`, which is how
a truncated tail gets detected.

**Private key files** are JSON (`lattice-keeper-secret/v1`), either
`{"encrypted":false,"secret":b64}` or scrypt + AES-256-GCM sealed (the
algorithm name is the AAD).

---

## Security notes

- Put a TLS reverse proxy in front of the service. It speaks plain HTTP.
  The compose file binds it to `127.0.0.1` only.
- API keys are compared in constant time against every configured key.
  The rate limit and logs use a hash of the key, never the key itself.
- `/health` and `/pubkeys` are the only endpoints that skip authentication.
- Set `LATTICE_KEY_PASSPHRASE` in production, keep it out of the image, and
  back up the key volume.
- Use a Redis password. Compose requires one. Redis holds the records, the
  audit log and the rate-limit counters.
- A FAISS index is loaded with pickle only after its files match a manifest
  whose hash *you* pinned. Pin only indexes you built yourself.
- Keep the operator key directory off the server, encrypted with a passphrase, and backed up.
  Losing it means no more policy changes (you'd have to pin a new key and restart). Anyone who
  has it **and** an admin API key can change device thresholds within the hard bounds.

## Limitations

- **No Bitcoin (or any blockchain) anchoring is implemented.** v0.28's
  title claimed it, but the code never did it. Signatures prove that *this
  server's key* signed something. They don't prove *when* it was signed, and
  nothing is published anywhere. Adding external timestamping (for example
  OpenTimestamps or RFC 3161) would be a separate feature.
- **"RWA tokenization" here means a signed record in Redis.** No token is
  issued on any ledger, and there's no legal or financial meaning to it.
- **The reference check is not an audit.** It only confirms that a request
  matches values the operator configured.
- **The guardian is a keyword heuristic.** Misspellings, synonyms, other
  languages and encodings get past it easily, and it will block harmless
  text that uses a listed word.
- **Audit log:** an attacker with Redis write access *and* the signing key
  can rewrite history. Without the key, tampering, reordering, insertion,
  deletion and truncation are all detected. If someone wipes both the list
  and the head, the log just looks empty, so export `head_hash` somewhere
  external from time to time if that matters to you.
- The rate limiter uses fixed one-minute windows, so short bursts of up to
  2× the limit can happen at window edges.
- ML-DSA / ML-KEM come from liboqs, which describes itself as a research and
  prototyping library. It has not been through a FIPS validation.
- If you run several replicas, they must share the same key volume (or the
  same keys), or signatures from one replica won't verify on another.
- **Policy:** devices verify only the Ed25519 half of the operator signature (classical security);
  the ML-DSA-65 half is enforced by the server. If Redis is wiped, the server starts a new genesis
  v1 and devices that stored a higher version will refuse lower versions until you sign past their
  version or reflash with `POLICY_FORGET_STORED 1`. There is no scheduled (future-dated) policy and
  no multi-signature (one operator key).

## Development

```bash
pip install -e ".[dev,pqc]"
ruff check src tests && ruff format --check src tests
OQS_INSTALL_PATH=/path/to/oqs pytest -q     # PQC tests are skipped if liboqs is missing
```

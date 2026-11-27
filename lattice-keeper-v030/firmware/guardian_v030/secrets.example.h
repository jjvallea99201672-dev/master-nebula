// Copy to secrets.h and fill in. secrets.h is git-ignored; never commit it.
// Copyright (c) 2026 Jay Vallea. All Rights Reserved.
#pragma once

#define WIFI_SSID      "your-ssid"
#define WIFI_PASSWORD  "your-wifi-password"

// A *device* key from the server's DEVICE_API_KEYS (>= 32 chars). Device keys can
// only call POST /telemetry and GET /policy, so a key pulled from this board's flash can't reach
// /rwa, /verify, /audit or /rag_guard.
//   python -c 'import secrets; print(secrets.token_urlsafe(32))'
#define LK_DEVICE_KEY  "REPLACE_WITH_A_32+_CHARACTER_DEVICE_KEY_xxxxxxxx"

#define LK_SERVER_HOST "lattice.example.com"   // must match the certificate's name
#define LK_SERVER_PORT 443

// ---- TLS: pick ONE (see README.md) ---------------------------------------
// Option A (recommended): pin the CA that issued the server certificate (PEM).
#define LK_CA_CERT \
  "-----BEGIN CERTIFICATE-----\n" \
  "...paste the root or intermediate CA here...\n" \
  "-----END CERTIFICATE-----\n"

// Option B: pin the server certificate's SHA-256 fingerprint (64 hex chars,
// colons/spaces allowed). Must be updated every time the certificate is renewed.
// #define LK_CERT_SHA256 "AB:CD:...:EF"

// ---- Development only ------------------------------------------------------
// Plain HTTP sends the key unencrypted. Only for a bench test on a trusted LAN,
// e.g. LK_SERVER_HOST "192.168.1.50" and LK_SERVER_PORT 8765.
// #define LK_ALLOW_PLAIN_HTTP 1

// ---- Operator-signed policy (v0.30, optional) --------------------------------
// The operator's Ed25519 PUBLIC key (not a secret), printed by
//   lattice-keeper operator-keygen --out ./operator-keys
// When set, the guardian fetches GET /policy, verifies the operator signature on
// the device and applies thresholds only from a valid, newer policy. When unset,
// it uses the compiled thresholds in config.h (v0.29 behaviour).
// #define LK_OPERATOR_ED25519_PUBKEY_B64 "BASE64_44_CHARS_FROM_OPERATOR_KEYGEN_xxxxxxx="

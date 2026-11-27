// ================================================
//  LATTICE KEEPER GUARDIAN v0.30.0 — operator-signed policy (device side)
//  Copyright (c) 2026 Jay Vallea. All Rights Reserved.
// ================================================
// Verifies a policy document fetched from GET /policy?view=device and turns it
// into threshold values. Header-only and free of Arduino APIs so it can also be
// compiled and tested on a PC (see host_test/).
//
// What is verified on the device:
//   sha256 = SHA-256(canonical bytes exactly as signed)
//   Ed25519.verify(sig, operator_pubkey, "lattice-keeper/policy/v1:" + hex(sha256))
// Only the Ed25519 half of the operator's hybrid signature is checked here; the
// ML-DSA-65 half is checked by the server (which refuses any policy whose hybrid
// signature fails). A device therefore has classical (Ed25519) assurance only.
//
// After the signature: format, version > last accepted version (anti-rollback)
// and every value against the same hard bounds the server enforces. Anything
// that fails leaves the last-known-good policy in place.
#pragma once

#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include <ArduinoJson.h>
#include <Ed25519.h>
#include <SHA256.h>

namespace lkpolicy {

static const char kContext[] = "lattice-keeper/policy/v1:";
static const char kFormat[] = "lattice-keeper-policy/v1";

struct Values {
  uint32_t version;  // 0 = compiled defaults (no signed policy applied)
  char sha256[65];   // hex of the applied policy ("" for defaults)
  float tempMaxC, tempHystC;
  float qDeltasAbove, qBetaAbove, qCoherenceBelow;
  float pumpDeltasAbove;
  uint8_t pumpBase, pumpC150, pumpC200, pumpDeltas;
  float betaDefault, betaMin, betaMax;
  float beta;  // this device's override > beta_global > NAN (= use betaDefault)
};

enum Result : int8_t {
  OK = 0,
  E_TOO_LARGE,
  E_B64,
  E_HASH_MISMATCH,
  E_SIGNATURE,
  E_JSON,
  E_FORMAT,
  E_ROLLBACK,
  E_BOUNDS,
  E_UNSIGNED,
};

inline const char *resultName(Result r) {
  switch (r) {
    case OK: return "ok";
    case E_TOO_LARGE: return "too_large";
    case E_B64: return "bad_base64";
    case E_HASH_MISMATCH: return "hash_mismatch";
    case E_SIGNATURE: return "bad_signature";
    case E_JSON: return "bad_json";
    case E_FORMAT: return "bad_format";
    case E_ROLLBACK: return "rollback_or_replay";
    case E_BOUNDS: return "out_of_bounds";
    case E_UNSIGNED: return "unsigned";
  }
  return "?";
}

// Standard base64 (RFC 4648) with padding. Returns decoded length or -1.
inline int b64decode(const char *in, uint8_t *out, size_t cap) {
  auto val = [](char c) -> int {
    if (c >= 'A' && c <= 'Z') return c - 'A';
    if (c >= 'a' && c <= 'z') return c - 'a' + 26;
    if (c >= '0' && c <= '9') return c - '0' + 52;
    if (c == '+') return 62;
    if (c == '/') return 63;
    return -1;
  };
  const size_t n = strlen(in);
  if (n % 4) return -1;
  size_t o = 0;
  for (size_t i = 0; i < n; i += 4) {
    int v[4];
    for (int k = 0; k < 4; k++) v[k] = in[i + k] == '=' ? -2 : val(in[i + k]);
    if (v[0] < 0 || v[1] < 0 || (v[2] == -2 && v[3] != -2) || v[2] == -1 || v[3] == -1) return -1;
    if ((v[2] == -2 || v[3] == -2) && i + 4 != n) return -1;
    const uint32_t t = (v[0] << 18) | (v[1] << 12) | ((v[2] < 0 ? 0 : v[2]) << 6) | (v[3] < 0 ? 0 : v[3]);
    const int bytes = v[2] == -2 ? 1 : (v[3] == -2 ? 2 : 3);
    if (o + bytes > cap) return -1;
    out[o++] = (t >> 16) & 0xFF;
    if (bytes > 1) out[o++] = (t >> 8) & 0xFF;
    if (bytes > 2) out[o++] = t & 0xFF;
  }
  return (int)o;
}

inline void sha256hex(const uint8_t *data, size_t len, char out[65]) {
  SHA256 h;
  uint8_t d[32];
  h.update(data, len);
  h.finalize(d, sizeof(d));
  for (int i = 0; i < 32; i++) snprintf(out + 2 * i, 3, "%02x", d[i]);
  out[64] = '\0';
}

// Signature check only. hexOut receives sha256(canonical).
inline bool verifySignature(const char *canonical, size_t len, const uint8_t sig[64], const uint8_t pub[32],
                            char hexOut[65]) {
  sha256hex((const uint8_t *)canonical, len, hexOut);
  uint8_t msg[sizeof(kContext) - 1 + 64];
  memcpy(msg, kContext, sizeof(kContext) - 1);
  memcpy(msg + sizeof(kContext) - 1, hexOut, 64);
  return Ed25519::verify(sig, pub, msg, sizeof(msg));
}

inline bool numIn(JsonVariantConst v, float lo, float hi, float &out) {
  if (!v.is<float>()) return false;  // integers count as numbers too
  const float f = v.as<float>();
  if (!isfinite(f) || f < lo || f > hi) return false;
  out = f;
  return true;
}

inline bool levelIn(JsonVariantConst v, uint8_t &out) {
  if (!v.is<int>()) return false;  // must be an integer, as the server requires
  const int i = v.as<int>();
  if (i < 0 || i > 255) return false;
  out = (uint8_t)i;
  return true;
}

// Parse already-verified canonical JSON and bounds-check every value. Hard
// bounds match lattice_keeper/policy.py. `out` is only written on OK.
inline Result parseValues(const char *canonical, size_t len, const char *deviceId, uint32_t lastVersion,
                          Values &out) {
  JsonDocument doc;
  if (deserializeJson(doc, canonical, len)) return E_JSON;
  if (!doc["format"].is<const char *>() || strcmp(doc["format"].as<const char *>(), kFormat) != 0)
    return E_FORMAT;
  if (!doc["version"].is<uint32_t>()) return E_FORMAT;
  Values v = {};
  v.version = doc["version"].as<uint32_t>();
  if (v.version <= lastVersion) return E_ROLLBACK;

  JsonVariantConst g = doc["values"]["guardian"];
  JsonVariantConst t = doc["values"]["telemetry"];
  if (!g.is<JsonObjectConst>() || !t.is<JsonObjectConst>()) return E_FORMAT;
  bool ok = numIn(g["temp_max_c"], 30.0f, 80.0f, v.tempMaxC) && numIn(g["temp_hyst_c"], 1.0f, 20.0f, v.tempHystC) &&
            numIn(g["q_deltas_above"], 0.0f, 100.0f, v.qDeltasAbove) &&
            numIn(g["q_beta_above"], 0.1f, 5.0f, v.qBetaAbove) &&
            numIn(g["q_coherence_below"], 40.0f, 255.0f, v.qCoherenceBelow) &&
            numIn(g["pump_deltas_above"], 0.0f, 100.0f, v.pumpDeltasAbove) &&
            numIn(g["beta_default"], 0.1f, 5.0f, v.betaDefault) && levelIn(g["pump_level_base"], v.pumpBase) &&
            levelIn(g["pump_level_c150"], v.pumpC150) && levelIn(g["pump_level_c200"], v.pumpC200) &&
            levelIn(g["pump_level_deltas"], v.pumpDeltas) && numIn(t["beta_min"], 0.1f, 5.0f, v.betaMin) &&
            numIn(t["beta_max"], 0.1f, 5.0f, v.betaMax) && v.betaMin <= v.betaMax;
  if (!ok) return E_BOUNDS;

  v.beta = NAN;
  JsonVariantConst dev = t["beta_global_devices"][deviceId];
  JsonVariantConst glob = t["beta_global"];
  JsonVariantConst pick = !dev.isNull() ? dev : glob;
  if (!pick.isNull() && !numIn(pick, v.betaMin, v.betaMax, v.beta)) return E_BOUNDS;
  out = v;
  return OK;
}

// Full check: optional expected hash, signature, then values.
inline Result verifyPolicy(const char *canonical, size_t len, const char *sigB64, const uint8_t pub[32],
                           const char *expectedSha, const char *deviceId, uint32_t lastVersion, Values &out) {
  if (!sigB64 || !sigB64[0]) return E_UNSIGNED;
  uint8_t sig[66];
  if (b64decode(sigB64, sig, sizeof(sig)) != 64) return E_B64;
  char hex[65];
  if (!verifySignature(canonical, len, sig, pub, hex)) return E_SIGNATURE;
  if (expectedSha && expectedSha[0] && strcmp(expectedSha, hex) != 0) return E_HASH_MISMATCH;
  Values v;
  const Result r = parseValues(canonical, len, deviceId, lastVersion, v);
  if (r != OK) return r;
  memcpy(v.sha256, hex, sizeof(hex));
  out = v;
  return OK;
}

}  // namespace lkpolicy

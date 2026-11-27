// ================================================
//  LATTICE KEEPER GUARDIAN VECTOR v0.30.0
//  Hardware: ESP32 + 120 LED + DS18B20 + Pump + Radiant ADC
//  Canonical Root: fd0adf9005622e103f02115a50314d91f83dc705f1d685b16d45f52ad6b940b2
//  Authority: Art.14 human oversight 2026-04-19T00:00:00Z
// ================================================
// Copyright (c) 2026 Jay Vallea. All Rights Reserved.
//
// v0.29.0 keeps the v0.28.4 look and logic (LED pulse and colours, coherence /
// deltaS / N_eff / beta_eff / r_h_proxy, quarantine rules, pump levels) and
// layers safety and networking fixes on top:
//   - pump safety interlock (sensor fault / stale > 5 s / over-temperature with
//     hysteresis) that overrides the pump, plus soft-start
//   - sensor faults also put the guardian into quarantine
//   - non-blocking DS18B20, hardware task watchdog
//   - Wi-Fi reconnect with backoff, HTTPS with CA or fingerprint pinning, hard
//     network deadlines on a separate core so the LEDs never freeze
//   - secrets in secrets.h, POST /telemetry with a scoped device key
// v0.30.0 adds operator-signed policy (optional; on when
// LK_OPERATOR_ED25519_PUBKEY_B64 is set in secrets.h):
//   - fetches GET /policy?view=device on boot, every POLICY_REFRESH_MS, and
//     when the server reports a different policy hash
//   - verifies the operator's Ed25519 signature on the device (policy_core.h),
//     rejects rollback/replay (version must increase) and out-of-bounds values
//   - applies thresholds / pump levels / beta only from a verified policy;
//     otherwise keeps the last-known-good policy (stored in NVS and re-verified
//     at boot) or the compiled defaults below
//   - the sensor-fault / stale-sensor interlock is not configurable by policy
//
// Honest naming: coherence, deltaS, N_eff, beta_eff, r_h_proxy and sigma_proxy
// are sensor-derived proxies (simple formulas over the temperature and radiant
// readings). They are not physics calculations.
//
// Builds on arduino-esp32 core 3.x (and 2.x via #if branches).
// Libraries: Adafruit NeoPixel, OneWire, DallasTemperature, ArduinoJson 7,
//            Crypto (rweather, for Ed25519/SHA-256 in policy_core.h).

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <Adafruit_NeoPixel.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <ArduinoJson.h>
#include <esp_task_wdt.h>
#include <esp_system.h>
#include <Preferences.h>
#include <math.h>

#include "config.h"
#include "policy_core.h"

#if __has_include("secrets.h")
#include "secrets.h"
#else
#error "Copy secrets.example.h to secrets.h and fill in your Wi-Fi, server and device key."
#endif

#ifndef ESP_ARDUINO_VERSION_MAJOR
#define ESP_ARDUINO_VERSION_MAJOR 1
#endif

// ---------------------------------------------------------------- compile-time checks
static_assert(sizeof(GENESIS_HASH) - 1 == 64, "GENESIS_HASH must be 64 hex chars");
static_assert(sizeof(LK_DEVICE_KEY) - 1 >= 32, "LK_DEVICE_KEY must be at least 32 characters");
#if defined(LK_ALLOW_PLAIN_HTTP)
#warning "LK_ALLOW_PLAIN_HTTP is set: the device key is sent UNENCRYPTED. Development only."
#define LK_USE_TLS 0
#elif defined(LK_CA_CERT) || defined(LK_CERT_SHA256)
#define LK_USE_TLS 1
#else
#error "Define LK_CA_CERT or LK_CERT_SHA256 in secrets.h (or LK_ALLOW_PLAIN_HTTP for a bench test)."
#endif
#if defined(LK_CA_CERT) && defined(LK_CERT_SHA256)
#error "Define only one of LK_CA_CERT or LK_CERT_SHA256."
#endif
#if defined(LK_OPERATOR_ED25519_PUBKEY_B64)
#define LK_POLICY_VERIFY 1
static_assert(sizeof(LK_OPERATOR_ED25519_PUBKEY_B64) - 1 == 44,
              "LK_OPERATOR_ED25519_PUBKEY_B64 must be the 44-char base64 Ed25519 key from operator-keygen");
#else
#define LK_POLICY_VERIFY 0
#endif

static constexpr float kTwoPi = 6.28318530718f;
static constexpr float kTauTotalCap = 400.0f;  // exp(-0.75*400/1.5) underflows to 0 in float

// ---------------------------------------------------------------- shared state
struct Snapshot {
  uint32_t uptimeMs;
  bool sensorOk;
  float oilTemp;          // NAN when no valid reading
  uint16_t radiantRaw;
  float radiantV;
  float pumpDuty;         // 0..1
  const char *interlock;  // string literal
  float tau;              // wrapped [0, 2*pi)
  float beta, betaEff, nEff, deltaS, rhProxy, coherence;
  bool quarantine;
  char reason[96];
};

static portMUX_TYPE gMux = portMUX_INITIALIZER_UNLOCKED;
static Snapshot gSnap = {};
static volatile float gBetaGlobal = NAN;   // from server; NAN = use BETA_GLOBAL_DEFAULT
static volatile bool gWifiUp = false;
static volatile bool gLastSyncOk = false;
static volatile uint32_t gLastSyncMs = 0;

// Active policy. Written by the net task (core 0) under gMux, copied by the loop
// (core 1) once per tick into P. Starts as the compiled defaults from config.h.
static lkpolicy::Values compiledDefaults() {
  lkpolicy::Values v = {};
  v.version = 0;
  v.tempMaxC = TEMP_MAX_C;
  v.tempHystC = TEMP_HYST_C;
  v.qDeltasAbove = Q_DELTAS_ABOVE;
  v.qBetaAbove = Q_BETA_ABOVE;
  v.qCoherenceBelow = Q_COHERENCE_BELOW;
  v.pumpDeltasAbove = PUMP_DELTAS_ABOVE;
  v.pumpBase = PUMP_LEVEL_BASE;
  v.pumpC150 = PUMP_LEVEL_C150;
  v.pumpC200 = PUMP_LEVEL_C200;
  v.pumpDeltas = PUMP_LEVEL_DELTAS;
  v.betaDefault = BETA_GLOBAL_DEFAULT;
  v.betaMin = BETA_MIN;
  v.betaMax = BETA_MAX;
  v.beta = NAN;
  return v;
}
static lkpolicy::Values gPolicy = compiledDefaults();
static lkpolicy::Values P = compiledDefaults();  // loop-core copy
static volatile bool gPolicyCheckDue = true;     // fetch on first connection
#if LK_POLICY_VERIFY
static uint8_t gOperatorPub[32];
static bool gOperatorPubOk = false;
static uint32_t gPolicyRejectedVersion = 0;  // net task only: last server policy we refused
static char gPolicyRejectedSha[65] = "";     // (or the unsigned genesis) - don't re-fetch it every sync
#endif

// ---------------------------------------------------------------- hardware objects
static Adafruit_NeoPixel strip(NUM_LEDS, LED_PIN, NEO_GRB + NEO_KHZ800);
static OneWire oneWire(ONEWIRE_PIN);
static DallasTemperature sensors(&oneWire);
static DeviceAddress sensorAddr;
static bool sensorPresent = false;
static char gDeviceId[24];

static inline float clampf(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }
static inline float wrap2pi(float x) { x = fmodf(x, kTwoPi); return x < 0 ? x + kTwoPi : x; }

static void wdtSetup() {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  esp_task_wdt_config_t cfg = {};
  cfg.timeout_ms = WDT_TIMEOUT_MS;
  cfg.idle_core_mask = 0;   // watch our own tasks, not the idle tasks
  cfg.trigger_panic = true; // reset the chip on timeout
  if (esp_task_wdt_reconfigure(&cfg) != ESP_OK) esp_task_wdt_init(&cfg);
#else
  esp_task_wdt_init((WDT_TIMEOUT_MS + 999) / 1000, true);
#endif
}

static void pumpAttach() {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcAttach(PUMP_PIN, PUMP_PWM_FREQ_HZ, PUMP_PWM_BITS);
#else
  ledcSetup(PUMP_PWM_CHANNEL, PUMP_PWM_FREQ_HZ, PUMP_PWM_BITS);
  ledcAttachPin(PUMP_PIN, PUMP_PWM_CHANNEL);
#endif
}

static void pumpWrite(uint32_t level) {  // 0..255
  if (level > 255) level = 255;
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcWrite(PUMP_PIN, level);
#else
  ledcWrite(PUMP_PWM_CHANNEL, level);
#endif
}

// ================================================================ sensors
// Non-blocking DS18B20: request a conversion, come back TEMP_CONVERSION_MS later.
struct TempState {
  bool converting = false;
  uint32_t requestedAt = 0;
  uint32_t lastRequest = 0;
  uint32_t lastGoodMs = 0;     // 0 = never
  uint8_t goodStreak = 0;
  bool lastReadOk = false;
  float tempC = NAN;
} temp;

static bool findSensor() {
  sensors.begin();
  sensorPresent = sensors.getAddress(sensorAddr, 0);
  if (sensorPresent) {
    sensors.setResolution(sensorAddr, 12);
    sensors.setWaitForConversion(false);
  }
  return sensorPresent;
}

static void temperatureTask(uint32_t now) {
  if (!temp.converting) {
    if (now - temp.lastRequest < TEMP_PERIOD_MS) return;
    temp.lastRequest = now;
    if (!sensorPresent && !findSensor()) {   // hot-plug: keep looking
      temp.lastReadOk = false;
      temp.goodStreak = 0;
      return;
    }
    sensors.requestTemperaturesByAddress(sensorAddr);  // returns immediately
    temp.converting = true;
    temp.requestedAt = now;
    return;
  }
  if (now - temp.requestedAt < TEMP_CONVERSION_MS) return;
  temp.converting = false;

  const float c = sensors.getTempC(sensorAddr);
  // -127 = disconnected / CRC failure. 85.0 = power-on reset value; invalid
  // unless we were already reading near 85 C.
  bool ok = (c != DEVICE_DISCONNECTED_C) && c > -55.0f && c < 125.0f;
  if (ok && c == 85.0f && !(temp.lastGoodMs && temp.tempC > 80.0f)) ok = false;

  temp.lastReadOk = ok;
  if (ok) {
    temp.tempC = c;
    temp.lastGoodMs = now ? now : 1;
    if (temp.goodStreak < 255) temp.goodStreak++;
  } else {
    temp.goodStreak = 0;
    if (c == DEVICE_DISCONNECTED_C) sensorPresent = false;  // rescan next period
  }
}

static bool tempFresh(uint32_t now) {
  return temp.lastGoodMs != 0 && (now - temp.lastGoodMs) <= SENSOR_STALE_MS;
}

// ================================================================ v0.28.4 GuardianState (proxies)
struct GuardianState {
  float beta = BETA_GLOBAL_DEFAULT;
  float tauTotal = 0.0f;  // v0.28.4 tau_global; saturates at kTauTotalCap (r_h_proxy is 0 by then)
  float tau = 0.0f;       // same tau wrapped to [0, 2*pi) for telemetry
  float phaseA = 0.0f;    // tau*4*beta_eff, accumulated and wrapped (LED wave)
  float phaseB = 0.0f;    // tau*2, accumulated and wrapped (brightness breathing)
  float oilTemp = DEVICE_DISCONNECTED_C;
  uint16_t radiantRaw = 0;
  float radiantV = 0.0f;
  float nEff = 0, betaEff = 0, rhProxy = 1.0f, sigmaProxy = 0, deltaS = 0, coherence = 40.0f;
} gs;

// tau advances 0.002 per 40 ms tick, as in v0.28.4. `steps` keeps the rate right if a tick runs late.
static void advanceTau(float steps) {
  const float d = TAU_STEP * steps;
  gs.tauTotal = fminf(gs.tauTotal + d, kTauTotalCap);
  gs.tau = wrap2pi(gs.tau + d);
  gs.phaseA = wrap2pi(gs.phaseA + d * 4.0f * gs.betaEff);
  gs.phaseB = wrap2pi(gs.phaseB + d * 2.0f);
}

static void updateProxies(uint32_t now) {
#if LK_POLICY_VERIFY
  // Beta only from the verified policy: device override > beta_global > beta_default.
  gs.beta = clampf(isnan(P.beta) ? P.betaDefault : P.beta, P.betaMin, P.betaMax);
#else
  gs.beta = isnan(gBetaGlobal) ? BETA_GLOBAL_DEFAULT : gBetaGlobal;  // v0.29 behaviour
#endif
  // v0.28.4 used the raw reading (-127 when the sensor was missing); same here.
  gs.oilTemp = tempFresh(now) ? temp.tempC : DEVICE_DISCONNECTED_C;
  gs.radiantRaw = (uint16_t)analogRead(RADIANT_ADC_PIN);
  gs.radiantV = gs.radiantRaw / 4095.0f;

  const float b = gs.beta;
  gs.nEff = N_NODES / (1.0f + 0.3f * b);
  gs.betaEff = b / (1.0f + 0.3f * b);
  gs.rhProxy = expf(-0.75f * gs.tauTotal / (1.0f + 0.1f * b));

  // calculateDeltaS (v0.28.4)
  const float rhPhys = gs.radiantV * gs.radiantV * gs.radiantV;
  gs.sigmaProxy = powf(rhPhys + 0.001f, 0.333f);  // logged only
  float dS = gs.betaEff * sqrtf(rhPhys + 0.001f) * powf(gs.nEff, 0.75f);
  dS *= (0.5f + 0.5f * gs.rhProxy);
  gs.deltaS = clampf(dS * 2.0f, 0.0f, 100.0f);

  // runGuardianRules coherence (v0.28.4)
  gs.coherence = clampf(255.0f - b * 30.0f - fabsf(gs.oilTemp - OIL_TARGET_C) * 5.0f, 40.0f, 255.0f);

  static uint32_t lastLog = 0;
  if (gs.deltaS > DELTAS_LOG_ABOVE && now - lastLog >= 1000) {  // throttled to 1/s
    lastLog = now;
    Serial.printf("High \xCE\x94S: %.2f (sigma_proxy %.4f, r_h_proxy %.4f)\n", gs.deltaS, gs.sigmaProxy,
                  gs.rhProxy);
  }
}

// ================================================================ guardian + interlock
enum FaultBits : uint8_t { F_SENSOR = 1 << 0, F_STALE = 1 << 1, F_OVERTEMP = 1 << 2 };
enum RuleBits : uint8_t { R_DELTAS = 1 << 0, R_BETA = 1 << 1, R_COHERENCE = 1 << 2 };

struct Guardian {
  bool quarantine = true;     // tamper flag (v0.28.4); start safe
  bool faultLatched = true;   // v0.29: sensor-fault quarantine with a clear delay
  uint32_t faultClearSince = 0;
  bool overtempLatched = false;
  uint8_t faults = F_STALE;
  uint8_t rules = 0;
} guardian;

static void guardianTask(uint32_t now) {
  // v0.29 sensor faults
  uint8_t f = 0;
  const bool fresh = tempFresh(now);
  if (!sensorPresent || !temp.lastReadOk) f |= F_SENSOR;
  if (!fresh) f |= F_STALE;
  if (fresh) {  // over-temperature with hysteresis
    if (temp.tempC >= P.tempMaxC) guardian.overtempLatched = true;
    else if (temp.tempC <= P.tempMaxC - P.tempHystC) guardian.overtempLatched = false;
  }
  if (guardian.overtempLatched) f |= F_OVERTEMP;
  guardian.faults = f;
  if (f) {
    guardian.faultLatched = true;
    guardian.faultClearSince = 0;
  } else if (guardian.faultLatched) {
    if (!guardian.faultClearSince) guardian.faultClearSince = now ? now : 1;
    if (now - guardian.faultClearSince >= FAULT_CLEAR_MS) guardian.faultLatched = false;
  }

  // v0.28.4 runGuardianRules: quarantine if deltaS>75 || beta>3.0 || coherence<90, else clear
  // (v0.30: thresholds come from the active policy; defaults are the v0.28.4 values)
  uint8_t r = 0;
  if (gs.deltaS > P.qDeltasAbove) r |= R_DELTAS;
  if (gs.beta > P.qBetaAbove) r |= R_BETA;
  if (gs.coherence < P.qCoherenceBelow) r |= R_COHERENCE;
  guardian.rules = r;

  const bool q = r || guardian.faultLatched;
  if (q && !guardian.quarantine) Serial.println("GUARDIAN ALERT: Quarantine - Higher-Order Lock");
  guardian.quarantine = q;
}

static void quarantineReason(char *out, size_t n) {
  out[0] = '\0';
  const char *names[] = {"sensor_fault", "stale", "overtemp", "deltas_high", "beta_high", "coherence_low"};
  const uint8_t bits = (guardian.faults & 0x7) | (uint8_t)(guardian.rules << 3);
  for (int i = 0; i < 6; i++) {
    if (bits & (1 << i)) {
      if (out[0]) strlcat(out, ",", n);
      strlcat(out, names[i], n);
    }
  }
  if (!out[0] && guardian.quarantine) strlcat(out, "fault_clearing", n);
}

struct Pump {
  float level = 0.0f;   // 0..255, ramped
  const char *interlock = "stale";
} pump;

static void pumpTask(float dt) {
  // v0.29 interlock: decides whether the pump may run at all.
  const uint8_t f = guardian.faults;
  const char *why = "ok";
  if (f & F_SENSOR) why = "sensor_fault";
  else if (f & F_STALE) why = "stale";
  else if (f & F_OVERTEMP) why = "overtemp";
  else if (guardian.quarantine) why = "quarantine";   // v0.28.4 quarantineMode: pump 0
  else if (temp.goodStreak < SENSOR_GOOD_TO_ARM) why = "rearming";
  pump.interlock = why;

  if (strcmp(why, "ok") != 0) {
    pump.level = 0.0f;  // cut immediately
  } else {
    // v0.28.4 adaptiveOilPumpControl
    int target = P.pumpBase;
    if (gs.deltaS > P.pumpDeltasAbove) target = P.pumpDeltas;
    else if (gs.coherence > 200.0f) target = P.pumpC200;
    else if (gs.coherence > 150.0f) target = P.pumpC150;
    if (target > pump.level) pump.level = fminf((float)target, pump.level + PUMP_RAMP_PER_S * dt);  // soft start
    else pump.level = (float)target;
  }
  pumpWrite((uint32_t)lroundf(pump.level));
}

// ================================================================ LEDs
static void statusPixel(uint32_t now) {
#if STATUS_PIXEL
  const bool blink = (now / 500) % 2;
  if (!gWifiUp) strip.setPixelColor(0, blink ? strip.Color(255, 120, 0) : 0);
  else if (!gLastSyncOk && gLastSyncMs) strip.setPixelColor(0, blink ? strip.Color(255, 0, 160) : 0);
#else
  (void)now;
#endif
}

static void ledTask(uint32_t now) {
  if (guardian.quarantine) {
    // v0.28.4 quarantineMode
    strip.setBrightness(QUARANTINE_BRIGHTNESS);
    strip.fill(strip.Color(255, 60, 0));
  } else {
    // v0.28.4 animatePulse: phase = tau*4*beta_eff + i*0.1; intensity = sin(phase)*90+130;
    // high coherence (>150) => (60, 200, intensity), else (200, 100, intensity);
    // brightness = 55 + 35*sin(tau*2).
    strip.setBrightness((uint8_t)lroundf(55.0f + 35.0f * sinf(gs.phaseB)));
    const bool high = gs.coherence > 150.0f;
    const uint8_t r = high ? 60 : 200, g = high ? 200 : 100;
    for (int i = 0; i < NUM_LEDS; i++) {
      const float intensity = sinf(gs.phaseA + i * 0.1f) * 90.0f + 130.0f;
      strip.setPixelColor(i, strip.Color(r, g, (uint8_t)intensity));
    }
  }
  statusPixel(now);
  strip.show();
}

static void publishSnapshot(uint32_t now) {
  Snapshot s;
  s.uptimeMs = now;
  s.sensorOk = sensorPresent && temp.lastReadOk;
  s.oilTemp = tempFresh(now) ? temp.tempC : NAN;
  s.radiantRaw = gs.radiantRaw;
  s.radiantV = gs.radiantV;
  s.pumpDuty = pump.level / 255.0f;
  s.interlock = pump.interlock;
  s.tau = gs.tau;
  s.beta = gs.beta;
  s.betaEff = gs.betaEff;
  s.nEff = gs.nEff;
  s.deltaS = gs.deltaS;
  s.rhProxy = gs.rhProxy;
  s.coherence = gs.coherence;
  s.quarantine = guardian.quarantine;
  quarantineReason(s.reason, sizeof(s.reason));
  portENTER_CRITICAL(&gMux);
  gSnap = s;
  portEXIT_CRITICAL(&gMux);
}

// ================================================================ network (core 0)
static bool readLine(Client &c, char *buf, size_t n, uint32_t deadline) {
  size_t i = 0;
  while ((int32_t)(deadline - millis()) > 0) {
    while (c.available()) {
      const int ch = c.read();
      if (ch == '\n') { buf[i] = '\0'; if (i && buf[i - 1] == '\r') buf[i - 1] = '\0'; return true; }
      if (i + 1 < n) buf[i++] = (char)ch;
    }
    if (!c.connected() && !c.available()) break;
    vTaskDelay(pdMS_TO_TICKS(5));
  }
  buf[i] = '\0';
  return false;
}

// Minimal HTTP/1.1 request with hard deadlines. body == nullptr => GET.
// Returns HTTP status or a negative error (-5 = response larger than respCap).
static int httpRequest(Client &c, const char *path, const char *body, size_t len, char *resp, size_t respCap) {
  const uint32_t deadline = millis() + HTTP_RESPONSE_TIMEOUT_MS;
  // v0.28.4 headers: X-API-Key and X-Request-ID "esp32-<NODE_ID>-<millis>"
  if (body) {
    c.printf("POST %s HTTP/1.1\r\nHost: %s\r\nX-API-Key: %s\r\nX-Request-ID: esp32-%d-%lu\r\n"
             "Content-Type: application/json\r\nContent-Length: %u\r\n"
             "User-Agent: lattice-guardian/" FW_VERSION "\r\nConnection: close\r\n\r\n",
             path, LK_SERVER_HOST, LK_DEVICE_KEY, NODE_ID, (unsigned long)millis(), (unsigned)len);
    c.write((const uint8_t *)body, len);
  } else {
    c.printf("GET %s HTTP/1.1\r\nHost: %s\r\nX-API-Key: %s\r\nX-Request-ID: esp32-%d-%lu\r\n"
             "User-Agent: lattice-guardian/" FW_VERSION "\r\nConnection: close\r\n\r\n",
             path, LK_SERVER_HOST, LK_DEVICE_KEY, NODE_ID, (unsigned long)millis());
  }

  char line[160];
  if (!readLine(c, line, sizeof(line), deadline)) return -10;
  int status = 0;
  if (sscanf(line, "HTTP/1.%*d %d", &status) != 1) return -11;
  long contentLength = -1;
  while (readLine(c, line, sizeof(line), deadline)) {
    if (!line[0]) break;  // end of headers
    if (strncasecmp(line, "Content-Length:", 15) == 0) contentLength = atol(line + 15);
  }
  if (contentLength >= (long)respCap) return -5;
  size_t got = 0;
  while ((int32_t)(deadline - millis()) > 0 && got + 1 < respCap &&
         (contentLength < 0 || (long)got < contentLength)) {
    while (c.available() && got + 1 < respCap) resp[got++] = (char)c.read();
    if (!c.connected() && !c.available()) break;
    if (contentLength >= 0 && (long)got >= contentLength) break;
    vTaskDelay(pdMS_TO_TICKS(5));
  }
  resp[got] = '\0';
  return status;
}

// Connect (TLS with CA or fingerprint pinning unless LK_ALLOW_PLAIN_HTTP), send, read.
static int httpCall(const char *path, const char *body, size_t len, char *resp, size_t respCap) {
#if LK_USE_TLS
  WiFiClientSecure client;
  client.setHandshakeTimeout(TLS_HANDSHAKE_TIMEOUT_S);
#if defined(LK_CA_CERT)
  client.setCACert(LK_CA_CERT);
#else
  client.setInsecure();  // chain not checked; the pinned fingerprint is checked below
#endif
#else
  WiFiClient client;
#endif
  esp_task_wdt_reset();
  if (!client.connect(LK_SERVER_HOST, LK_SERVER_PORT, HTTP_CONNECT_TIMEOUT_MS)) return -2;
#if LK_USE_TLS && defined(LK_CERT_SHA256)
  if (!client.verify(LK_CERT_SHA256, LK_SERVER_HOST)) {  // before any secret is sent
    client.stop();
    return -3;
  }
#endif
  esp_task_wdt_reset();
  const int status = httpRequest(client, path, body, len, resp, respCap);
  client.stop();
  return status;
}

static void putFixed(JsonDocument &doc, const char *k, float v, int digits) {
  doc[k] = serialized(String(v, digits));
}

static int syncOnce(uint32_t seq) {
  Snapshot s;
  portENTER_CRITICAL(&gMux);
  s = gSnap;
  portEXIT_CRITICAL(&gMux);

  JsonDocument doc;
  // v0.29 fields
  doc["device_id"] = gDeviceId;
  doc["fw"] = FW_VERSION;
  doc["seq"] = seq;
  doc["uptime_ms"] = s.uptimeMs;
  doc["sensor_ok"] = s.sensorOk;
  if (isnan(s.oilTemp)) doc["temp_c"] = nullptr;
  else putFixed(doc, "temp_c", s.oilTemp, 2);
  doc["radiant_raw"] = s.radiantRaw;
  putFixed(doc, "radiant_norm", clampf(s.radiantV, 0, 1), 4);
  putFixed(doc, "pump_duty", clampf(s.pumpDuty, 0, 1), 4);
  doc["pump_interlock"] = s.interlock;
  JsonObject g = doc["guardian"].to<JsonObject>();
  g["state"] = s.quarantine ? "quarantine" : "nominal";
  g["reason"] = s.reason;
  const long rssi = WiFi.RSSI();
  if (rssi < 0 && rssi >= -127) doc["rssi"] = rssi;
  // v0.28.4 fields
  doc["node_id"] = NODE_ID;
  putFixed(doc, "tau", clampf(s.tau, 0, 6.2831f), 4);
  putFixed(doc, "beta", clampf(s.beta, BETA_MIN, BETA_MAX), 3);
  putFixed(doc, "beta_eff", s.betaEff, 4);
  putFixed(doc, "N_eff", s.nEff, 3);
  putFixed(doc, "deltaS", s.deltaS, 3);
  putFixed(doc, "r_h_proxy", s.rhProxy, 5);
  putFixed(doc, "coherence", s.coherence, 2);
  if (isnan(s.oilTemp)) doc["oil_temp"] = nullptr;
  else putFixed(doc, "oil_temp", s.oilTemp, 2);
  doc["genesis"] = GENESIS_HASH;

  {
    lkpolicy::Values cur;
    portENTER_CRITICAL(&gMux);
    cur = gPolicy;
    portEXIT_CRITICAL(&gMux);
    if (cur.version) {  // v0.30: which signed policy this device is applying
      doc["policy_version"] = cur.version;
      doc["policy_sha256"] = cur.sha256;
    }
  }

  char body[1024];
  const size_t len = serializeJson(doc, body, sizeof(body));
  if (len == 0 || len >= sizeof(body) - 1) return -1;

  static char resp[HTTP_MAX_RESPONSE_BYTES];
  const int status = httpCall(TELEMETRY_PATH, body, len, resp, sizeof(resp));
  if (status != 200) return status;

  JsonDocument r;
  if (deserializeJson(r, resp)) return -4;
#if LK_POLICY_VERIFY
  // beta comes only from the verified policy; the server's hash tells us when to re-fetch.
  const char *srvSha = r["policy_sha256"] | "";
  portENTER_CRITICAL(&gMux);
  const bool differs = srvSha[0] && strcmp(srvSha, gPolicy.sha256) != 0;
  portEXIT_CRITICAL(&gMux);
  if (differs && strcmp(srvSha, gPolicyRejectedSha) == 0) return 200;  // already refused this one
  if (differs) gPolicyCheckDue = true;
#else
  if (r["beta_global"].is<float>()) {
    gBetaGlobal = clampf(r["beta_global"].as<float>(), BETA_MIN, BETA_MAX);  // v0.28.4: clamp 0.1-5.0
  } else if (r["beta_global"].isNull()) {
    gBetaGlobal = NAN;  // not set on the server => BETA_GLOBAL_DEFAULT
  }
#endif
  return 200;
}

// ================================================================ policy (core 0)
#if LK_POLICY_VERIFY

static void policyLog(const char *what, uint32_t version, lkpolicy::Result r) {
  Serial.printf("[policy] %s v%lu: %s\n", what, (unsigned long)version, lkpolicy::resultName(r));
}

static void applyPolicy(const lkpolicy::Values &v) {
  portENTER_CRITICAL(&gMux);
  gPolicy = v;
  portEXIT_CRITICAL(&gMux);
  Serial.printf("[policy] applied v%lu sha256=%.16s... q_deltas_above=%.1f temp_max_c=%.1f\n",
                (unsigned long)v.version, v.sha256, v.qDeltasAbove, v.tempMaxC);
}

// Re-verify the last-known-good policy stored in NVS (protects against flash corruption).
static void policyLoadStored() {
  Preferences prefs;
  if (!prefs.begin(POLICY_NVS_NAMESPACE, false)) return;
#if POLICY_FORGET_STORED
  prefs.clear();
  Serial.println("[policy] POLICY_FORGET_STORED: cleared stored policy");
#endif
  String canon = prefs.getString("canon", "");
  String sig = prefs.getString("sig", "");
  prefs.end();
  if (!canon.length()) return;
  lkpolicy::Values v;
  const auto r = lkpolicy::verifyPolicy(canon.c_str(), canon.length(), sig.c_str(), gOperatorPub, nullptr,
                                        gDeviceId, 0, v);
  if (r == lkpolicy::OK) applyPolicy(v);
  else policyLog("stored policy rejected, using compiled defaults", 0, r);
}

static void policyStore(const char *canon, size_t len, const char *sig) {
  Preferences prefs;
  if (!prefs.begin(POLICY_NVS_NAMESPACE, false)) return;
  prefs.putString("canon", String(canon, len));  // String(const char*, len): core 2.x and 3.x
  prefs.putString("sig", sig);
  prefs.end();
}

// Returns 200 if a new policy was applied or nothing changed, else an error code.
static int policyFetchOnce() {
  static char resp[POLICY_MAX_BYTES];
  const int status = httpCall(POLICY_PATH, nullptr, 0, resp, sizeof(resp));
  if (status != 200) return status;
  JsonDocument d;
  if (deserializeJson(d, resp)) return -4;
  JsonString canon = d["canonical"].as<JsonString>();
  const char *sig = d["ed25519_sig"] | "";
  const char *sha = d["policy_sha256"] | "";
  const uint32_t version = d["version"] | 0u;
  if (!canon.c_str()) return -4;

  lkpolicy::Values cur;
  portENTER_CRITICAL(&gMux);
  cur = gPolicy;
  portEXIT_CRITICAL(&gMux);
  if (cur.version && strcmp(sha, cur.sha256) == 0) return 200;              // already applied
  if (version == gPolicyRejectedVersion && strcmp(sha, gPolicyRejectedSha) == 0) return -20;  // known bad

  lkpolicy::Values v;
  const auto r = lkpolicy::verifyPolicy(canon.c_str(), canon.size(), sig, gOperatorPub, sha, gDeviceId,
                                        cur.version, v);
  if (r != lkpolicy::OK) {
    gPolicyRejectedVersion = version;
    strlcpy(gPolicyRejectedSha, sha, sizeof(gPolicyRejectedSha));
    policyLog(r == lkpolicy::E_UNSIGNED ? "server policy is the unsigned genesis; keeping current"
                                        : "rejected, keeping last-known-good",
              version, r);
    return r == lkpolicy::E_UNSIGNED ? 200 : -20 - (int)r;
  }
  applyPolicy(v);
  policyStore(canon.c_str(), canon.size(), sig);
  return 200;
}
#endif

static void netTask(void *) {
  esp_task_wdt_add(nullptr);
  uint32_t backoff = WIFI_BACKOFF_MIN_MS, nextWifiTry = 0, nextSync = 0, seq = 0;
#if LK_POLICY_VERIFY
  uint32_t nextPolicy = 0, policyRetryAt = 0;
#endif
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(false);   // we reconnect ourselves, with backoff
  WiFi.setSleep(false);
  for (;;) {
    esp_task_wdt_reset();
    const uint32_t now = millis();
    gWifiUp = WiFi.status() == WL_CONNECTED;
    if (!gWifiUp) {
      if ((int32_t)(now - nextWifiTry) >= 0) {
        Serial.printf("[net] Wi-Fi connect attempt (next backoff %lu ms)\n", (unsigned long)backoff);
        WiFi.disconnect(false, false);
        WiFi.begin(WIFI_SSID, WIFI_PASSWORD);   // returns immediately
        nextWifiTry = now + backoff + (esp_random() % 500);
        backoff = min<uint32_t>(backoff * 2, WIFI_BACKOFF_MAX_MS);
      }
      vTaskDelay(pdMS_TO_TICKS(100));
      continue;
    }
    backoff = WIFI_BACKOFF_MIN_MS;
#if LK_POLICY_VERIFY
    if (gOperatorPubOk && (gPolicyCheckDue || (int32_t)(now - nextPolicy) >= 0) &&
        (int32_t)(now - policyRetryAt) >= 0) {
      gPolicyCheckDue = false;
      const int rc = policyFetchOnce();
      esp_task_wdt_reset();
      if (rc == 200 || rc <= -20) {
        nextPolicy = millis() + POLICY_REFRESH_MS;  // fetched (applied, unchanged, or rejected)
      } else {
        Serial.printf("[policy] fetch failed: %d\n", rc);
        policyRetryAt = millis() + POLICY_RETRY_MS;  // network/server error: retry sooner
        gPolicyCheckDue = true;
      }
    }
#endif
    if ((int32_t)(now - nextSync) >= 0) {
      nextSync = now + SYNC_INTERVAL_MS;
      const int rc = syncOnce(seq++);
      gLastSyncOk = rc == 200;
      gLastSyncMs = millis();
      if (rc != 200) Serial.printf("[net] telemetry failed: %d\n", rc);
    }
    vTaskDelay(pdMS_TO_TICKS(50));
  }
}

// ================================================================ setup / loop
void setup() {
  // Pump off before anything else.
  pumpAttach();
  pumpWrite(0);

  Serial.begin(115200);
  wdtSetup();
  esp_task_wdt_add(nullptr);  // the Arduino loop task

  const uint64_t mac = ESP.getEfuseMac();
  snprintf(gDeviceId, sizeof(gDeviceId), "lk-%04X%08lX", (uint16_t)(mac >> 32), (unsigned long)(uint32_t)mac);
  Serial.println("Guardian Vector v" FW_VERSION " (Hardened) - Genesis fd0adf90...");
  Serial.printf("device_id=%s node_id=%d reset_reason=%d\n", gDeviceId, NODE_ID, (int)esp_reset_reason());

  strip.begin();
  strip.setBrightness(LED_BRIGHTNESS);
  strip.clear();
  strip.show();

  analogReadResolution(12);
  analogSetPinAttenuation(RADIANT_ADC_PIN, ADC_11db);
  findSensor();

#if LK_POLICY_VERIFY
  gOperatorPubOk = lkpolicy::b64decode(LK_OPERATOR_ED25519_PUBKEY_B64, gOperatorPub, sizeof(gOperatorPub)) == 32;
  if (!gOperatorPubOk) Serial.println("[policy] LK_OPERATOR_ED25519_PUBKEY_B64 is not valid base64; policy disabled");
  else policyLoadStored();  // last-known-good from NVS (re-verified) before any network
#else
  Serial.println("[policy] verification off (no LK_OPERATOR_ED25519_PUBKEY_B64); compiled thresholds");
#endif
  portENTER_CRITICAL(&gMux);
  P = gPolicy;
  portEXIT_CRITICAL(&gMux);
  publishSnapshot(millis());

  // 12 KB: Ed25519 verification and the policy JSON need more stack than v0.29's 8 KB.
  xTaskCreatePinnedToCore(netTask, "lk-net", NET_TASK_STACK, nullptr, 1, nullptr, 0);
}

void loop() {
  static uint32_t lastTick = 0;
  const uint32_t now = millis();
  esp_task_wdt_reset();

  temperatureTask(now);  // non-blocking; checked every pass

  if (now - lastTick >= TICK_MS) {
    const float dt = lastTick ? (now - lastTick) / 1000.0f : TICK_MS / 1000.0f;
    lastTick = now;
    portENTER_CRITICAL(&gMux);
    P = gPolicy;  // policy changes take effect on the next tick
    portEXIT_CRITICAL(&gMux);
    updateProxies(now);
    guardianTask(now);
    pumpTask(dt);
    advanceTau(dt * 1000.0f / TICK_MS);  // v0.28.4: tau_global += 0.002 per 40 ms loop
    ledTask(now);
    publishSnapshot(now);
  }
  delay(1);  // yield; LEDs and the interlock never wait on the network
}

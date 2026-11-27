# Lattice Keeper Guardian Vector v0.30.0 (ESP32 firmware)

Copyright (c) 2026 Jay Vallea. All Rights Reserved.

Firmware for an ESP32 that drives a 120-LED NeoPixel "lattice" display, reads a
DS18B20 temperature sensor and a radiant (analog) sensor, runs a pump through a
PWM driver behind a safety interlock, and sends telemetry to the Lattice Keeper
v0.30 server every 5 s. Optionally (v0.30) it fetches the **operator-signed policy**, verifies the
signature on the device, and takes its thresholds from it (see [Operator-signed policy](#operator-signed-policy-v030)).

## Hardware (same as v0.28.4)

| Function | Pin | Notes |
|---|---|---|
| NeoPixel data (120 LEDs, GRB 800 kHz) | GPIO 14 | 330 Ω series resistor and a 1000 µF cap across the strip supply are recommended |
| DS18B20 data | GPIO 4 | 4.7 kΩ pull-up to 3V3 |
| Pump PWM (LEDC, 5 kHz, 8-bit, as v0.28.4) | GPIO 27 | drive a logic-level MOSFET with a flyback diode on the pump, never the pump directly |
| Radiant sensor (ADC1, 0–3.3 V, 11 dB) | GPIO 34 | input-only pin, fine to use while Wi-Fi is on |

## What it does

v0.29.0 kept the v0.28.4 look and logic and added safety and networking on top; v0.30.0 adds
operator-signed policy. With policy verification off (no operator key in `secrets.h`) it behaves exactly
like v0.29.0.

- **Control loop (core 1, never blocks):** a 40 ms tick, as in v0.28.4. Each tick reads the radiant ADC,
  updates the proxies, runs the guardian rules and the pump, and draws the LEDs. The DS18B20 is read
  without blocking, about once a second.
- **Network task (core 0):** Wi-Fi reconnect with exponential backoff (1 s → 60 s, with jitter), and HTTPS
  `POST /telemetry` every 5 s with hard deadlines (3 s connect, 5 s TLS handshake, 4 s response).
  A slow server can't freeze the LEDs or the interlock.
- **Hardware task watchdog** (20 s) watches both tasks.

### Proxies (v0.28.4 formulas; sensor-derived, not physics)
| Name | Formula |
|---|---|
| beta | policy on: verified policy (device override > `beta_global` > `beta_default`), clamped to the policy's `beta_min`–`beta_max`. Policy off: server's `beta_global` clamped to 0.1–5.0, default 1.5 |
| tau | +0.002 per 40 ms tick |
| N_eff | `100 / (1 + 0.3·beta)` |
| beta_eff | `beta / (1 + 0.3·beta)` |
| r_h_proxy | `exp(-0.75·tau / (1 + 0.1·beta))` |
| radiant_v | `adc / 4095` |
| deltaS | `beta_eff · sqrt(radiant_v³ + 0.001) · N_eff^0.75 · (0.5 + 0.5·r_h_proxy)`, then ×2, clamped to 0–100. `sigma_proxy = (radiant_v³+0.001)^0.333` is logged only |
| coherence | `255 − beta·30 − |oil_temp − 28|·5`, clamped to 40–255. `oil_temp` is −127 when the sensor is missing, as in v0.28.4 |

Float precision: the LED wave phase (`tau·4·beta_eff`) and the brightness phase (`tau·2`) are separate
accumulators, each wrapped to [0, 2π), so they never lose precision and don't jump when beta changes.
The `tau` used in `r_h_proxy` stops growing at 400; by then `exp()` has already underflowed to 0, so the
values are identical to v0.28.4. Telemetry sends `tau` wrapped to [0, 2π).

### Guardian / quarantine
- **v0.28.4 rules:** quarantine if `deltaS > 75`, `beta > 3.0` or `coherence < 90` (the three thresholds
  are policy values `q_deltas_above`, `q_beta_above`, `q_coherence_below`; these are the defaults), and leave as soon as
  none of them holds. On entry it prints `GUARDIAN ALERT: Quarantine - Higher-Order Lock` once.
  `High ΔS` is printed when `deltaS > 60` (throttled to once a second).
- **v0.29 sensor faults** also quarantine:
  - `sensor_fault`: DS18B20 missing, bad CRC, or the 85 °C power-on value
  - `stale`: no valid reading for 5 s
  - `overtemp`: ≥ 60 °C, cleared at ≤ 55 °C (policy `temp_max_c` 30–80 and `temp_hyst_c` 1–20)
  These leave quarantine only after 10 s with no faults.
- The device boots in quarantine (no temperature reading yet means `coherence` = 40).
- Note: with beta capped at 5.0, `deltaS` tops out at about 64, so with the default thresholds the
  `deltaS > 75` rule and the 220 pump level can't actually trigger. That is unchanged from v0.28.4.
  Since v0.30 you can lower `q_deltas_above` / `pump_deltas_above` with a signed policy, without reflashing.

### Pump (v0.28.4 levels, 5 kHz 8-bit LEDC) + v0.29 interlock
- **Levels:** 80 base; 130 if coherence > 150; 190 if coherence > 200; 220 if deltaS > 70
  (policy `pump_level_base`, `pump_level_c150`, `pump_level_c200`, `pump_level_deltas`, `pump_deltas_above`).
- **Interlock** (overrides the level): the pump is **off** on `sensor_fault`, `stale`, `overtemp`, in quarantine
  (the v0.28.4 `quarantineMode` set the pump to 0 too), and until 3 good readings in a row after a fault
  (`rearming`). Turning off is instant. Turning on is a soft start at up to 120 counts per second.
  The pump is set to 0 first thing at boot.
- The `pump_interlock` telemetry field reports why.

> This is a software interlock. Also fit a hardware thermal cutoff in series with the pump if overheating could cause damage.

### LEDs (v0.28.4)
- **Normal (`animatePulse`):** pixel *i* = `(r, g, sin(tau·4·beta_eff + i·0.1)·90 + 130)`, where
  `(r, g)` = `(60, 200)` when coherence > 150 (teal/green) or `(200, 100)` otherwise (warm).
  Strip brightness = `55 + 35·sin(tau·2)`.
- **Quarantine:** the whole strip is orange `(255, 60, 0)` at brightness 40.
- **v0.29 status pixel** (`STATUS_PIXEL` in `config.h`, set it to 0 to turn off): pixel 0 blinks amber when
  Wi-Fi is down and magenta when uploads are failing.

## Build

Arduino IDE or arduino-cli, with board **ESP32 Dev Module** (`esp32:esp32:esp32`).
Works with arduino-esp32 core **3.x** (tested 3.3.12). Core **2.x** is handled with `#if` branches
(`ledcSetup/ledcAttachPin`, old watchdog API).

Libraries: Adafruit NeoPixel, OneWire, DallasTemperature, ArduinoJson (v7), Crypto (rweather, 0.4.0; used for
Ed25519 and SHA-256).

Compiled with arduino-cli for `esp32:esp32:esp32` (policy verification on / off):

| Core | Flash (of 1,310,720 B) | Globals |
|---|---|---|
| 3.3.12, policy on | 1,085,416 B (82%) | 53,936 B (16%) |
| 3.3.12, policy off | 1,066,980 B (81%) | 50,240 B (15%) |
| 2.0.17, policy on | 964,029 B (73%) | 51,512 B (15%) |
| 2.0.17, policy off | 945,889 B (72%) | 47,816 B (14%) |

```bash
arduino-cli core install esp32:esp32
arduino-cli lib install "Adafruit NeoPixel" OneWire DallasTemperature ArduinoJson Crypto
cp secrets.example.h secrets.h      # then edit it
arduino-cli compile --fqbn esp32:esp32:esp32 .
arduino-cli upload  --fqbn esp32:esp32:esp32 -p /dev/ttyUSB0 .
arduino-cli monitor -p /dev/ttyUSB0 -c baudrate=115200
```
Tunables (pins, temperatures, timings, ramp) are in `config.h`. Values marked `[policy]` there are
the compiled defaults that a verified policy replaces.

## Server side

1. Create a **device key** (at least 32 characters) and add it to the server's `.env`:
   `DEVICE_API_KEYS=<key>` (comma-separate one key per board). Device keys can only call
   `POST /telemetry` and `GET /policy`, so a key pulled from a board's flash can't reach the other endpoints.
2. Put the same key in `secrets.h` as `LK_DEVICE_KEY`.
3. `beta_global` (all devices or per device) is set in the signed policy since v0.30
   (`values.telemetry.beta_global` / `beta_global_devices`); `PUT /telemetry/beta_global` returns 410.
   Read the latest sample with `GET /telemetry/<device_id>` (admin key). The device ID is printed on
   the serial console at boot (`lk-` + the chip MAC).

## HTTPS setup

The server speaks plain HTTP, so put a TLS reverse proxy (Caddy, nginx, Traefik) in front of it.
`LK_SERVER_HOST` must match the certificate's name.

**Option A: pin the CA (recommended).** This survives certificate renewals as long as the CA stays the same.
```bash
# Show the chain the server presents; the LAST certificate printed is the top of the chain.
openssl s_client -connect lattice.example.com:443 -servername lattice.example.com -showcerts </dev/null
```
Copy that issuing root or intermediate CA (for Let's Encrypt, ISRG Root X1 is a good choice) into
`LK_CA_CERT` in `secrets.h`, one `"...\n"` string per line.

**Option B: pin the server certificate's SHA-256 fingerprint.** This is simpler, but you must update it at
every renewal (about every 60–90 days with Let's Encrypt), or the device will stop syncing (magenta pixel).
```bash
openssl s_client -connect lattice.example.com:443 -servername lattice.example.com </dev/null 2>/dev/null \
  | openssl x509 -noout -fingerprint -sha256
# sha256 Fingerprint=AB:CD:...   -> #define LK_CERT_SHA256 "AB:CD:..."
```
The fingerprint is checked right after the TLS handshake, **before** the key is sent.

**Plain HTTP (development only).** Define `LK_ALLOW_PLAIN_HTTP 1` in `secrets.h` and point the device at,
for example, `192.168.1.50:8765`. The compiler prints a warning, because the device key goes over the
network unencrypted. Note that the stock docker-compose binds to `127.0.0.1`, so for a LAN bench test you
must change the port mapping on purpose.

If none of these is defined, the sketch refuses to compile.

## Operator-signed policy (v0.30)

Turn it on by adding the operator's **public** Ed25519 key to `secrets.h`:
```c
#define LK_OPERATOR_ED25519_PUBKEY_B64 "…44 base64 chars printed by `lattice-keeper operator-keygen`…"
```
(It's also `ed25519.public_key` in `operator_pubkey.json`.) Then the device:

1. **At boot**, before any network, loads the last-known-good policy from NVS (`lkpolicy` namespace) and
   **re-verifies** its signature and bounds; if that fails it uses the compiled defaults.
2. **Fetches** `GET /policy?view=device` (with its device key, over the same pinned TLS) as soon as Wi-Fi is up,
   every 10 minutes (`POLICY_REFRESH_MS`), and whenever a telemetry response reports a `policy_sha256` that
   differs from the one it applies. Network errors retry after 60 s.
3. **Verifies on the device** (`policy_core.h`, using rweather Crypto):
   `Ed25519.verify(sig, operator_pubkey, "lattice-keeper/policy/v1:" + hex(SHA-256(canonical)))`, then that
   the hash matches the server's `policy_sha256`, `format` is right, **`version` is greater than the one it
   applies** (no replay or rollback), and every value is a number of the right type within the same hard
   bounds the server uses (for example `temp_max_c` 30–80, pump levels integers 0–255).
4. **Applies** it only if every check passes (the control loop picks it up on the next 40 ms tick) and saves
   it to NVS. Otherwise it logs `[policy] rejected …: <reason>` and keeps the last-known-good policy.
   The unsigned genesis v1 is never applied; until a signed v2 exists the device runs its compiled defaults
   (which are the same values).

Telemetry then carries `policy_version` and `policy_sha256`, and the server answers with `policy_in_sync`.
With verification on, the unsigned `beta_global` in the telemetry response is ignored.

**Fail-safe:** a policy can only move values within the hard bounds. It can't disable the sensor-fault /
stale-sensor interlock, the 10 s fault-clear delay, the re-arm count or the soft start, and a policy problem
never stops the control loop.

**Why Ed25519 only on the device.** The operator signature is hybrid (Ed25519 + ML-DSA-65) when the operator key
was made with liboqs, and the server requires both halves. ML-DSA-65 isn't available in the ESP32 Arduino
cores, and its signature (about 4.4 KB base64) doesn't fit the device response buffer, so the device checks
the Ed25519 half only. On the device the assurance is classical, not post-quantum. ESP32 mbedTLS has no
Ed25519 either, which is why rweather's Crypto library is used. A "pin a SHA-256 of the policy" fallback was
rejected: it would need a reflash for every policy change, which defeats the purpose.

**Recovering from a wiped server.** The device remembers the highest version it accepted. If Redis is wiped,
the server starts over at genesis v1, and the device refuses every version up to the one it stored. Either keep
signing new versions on the server (each must be current + 1) until you pass the device's version, or flash once
with `POLICY_FORGET_STORED 1` in `config.h` (it clears the stored policy at boot), then flash again with 0.

**Host test.** `host_test/run.sh` compiles `policy_core.h` on a PC against the same Crypto and ArduinoJson
sources and runs it on vectors signed by the real server code: valid policy, per-device and global beta,
rollback, wrong key, tampered bytes, bad base64, unsigned, signed-but-out-of-bounds, float pump level,
integer threshold, UTF-8 blocklist through JSON `\u` escapes, wrong format (12 cases, all pass).

## Telemetry format (`POST /telemetry`)

Headers as in v0.28.4: `X-API-Key: <device key>` and `X-Request-ID: esp32-<NODE_ID>-<millis>`. The server
echoes the request ID, so a device upload can be matched to the server log.

```json
{"device_id":"lk-24DCC3A1B2C4","fw":"0.30.0","seq":42,"uptime_ms":210000,"sensor_ok":true,"temp_c":28.06,
 "radiant_raw":2048,"radiant_norm":0.5001,"pump_duty":0.7451,"pump_interlock":"ok",
 "guardian":{"state":"nominal","reason":""},"rssi":-61,
 "node_id":1,"tau":3.1416,"beta":1.500,"beta_eff":1.0345,"N_eff":68.966,"deltaS":8.807,"r_h_proxy":0.00000,
 "coherence":209.70,"oil_temp":28.06,"genesis":"fd0adf9005622e103f02115a50314d91f83dc705f1d685b16d45f52ad6b940b2",
 "policy_version":2,"policy_sha256":"…"}
```
`policy_version` / `policy_sha256` are sent only once a signed policy is applied.
Response: `{"status":"accepted","seq":42,"beta_global":null,"genesis_match":true,"policy_version":2,
"policy_sha256":"…","policy_in_sync":true,"received_at":"..."}`.
- Policy off: `beta_global` is applied clamped to 0.1–5.0, and `null` means use the default of 1.5.
  Policy on: `beta_global` is ignored and `policy_sha256` triggers a re-fetch if it differs.
- `genesis_match` is informational: it compares the device's genesis hash with the server's
  `REFERENCE_ANCHOR_HASH`, and is `null` if that isn't configured.

## Limitations

- The look and logic follow the saved behavioural summary of v0.28.4 (`../original/`). Known deliberate
  differences: the safety interlock and sensor-fault quarantine, the optional status pixel, `tau` wrapping, and
  throttled `High ΔS` logging.
- The policy is stored in NVS unencrypted; it isn't secret, and it is re-verified at every boot.
- The device key sits in flash. Enable flash encryption plus secure boot if physical access is a concern.
  The key is scoped to `/telemetry`, which limits the damage if it leaks.
- A full TLS handshake runs on every upload (`Connection: close`). That keeps the pinning check simple at
  the cost of about 1 s of CPU on core 0 every 5 s.
- Compiled only; not yet tested on real hardware. The policy verifier was tested on a PC (`host_test/`), not
  on the chip; Ed25519 verification on an ESP32 should take roughly tens of milliseconds, but that isn't measured.

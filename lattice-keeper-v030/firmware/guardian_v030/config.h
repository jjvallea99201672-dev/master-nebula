// ================================================
//  LATTICE KEEPER GUARDIAN VECTOR v0.30.0 — non-secret tunables
//  Copyright (c) 2026 Jay Vallea. All Rights Reserved.
// ================================================
#pragma once

// ---------------------------------------------------------------- hardware (unchanged from v0.28.4)
#define NODE_ID              1
#define LED_PIN              14
#define NUM_LEDS             120
#define LED_BRIGHTNESS       80      // setup brightness (v0.28.4)
#define QUARANTINE_BRIGHTNESS 40     // v0.28.4 quarantine brightness
#define ONEWIRE_PIN          4       // DS18B20 data (4.7k pull-up to 3V3)
#define PUMP_PIN             27
#define RADIANT_ADC_PIN      34      // input-only ADC1 pin
#define PUMP_PWM_FREQ_HZ     5000    // v0.28.4: LEDC 5 kHz
#define PUMP_PWM_BITS        8       // v0.28.4: 8-bit (levels 0-255)
#define PUMP_PWM_CHANNEL     0       // only used on arduino-esp32 core 2.x

#define GENESIS_HASH "fd0adf9005622e103f02115a50314d91f83dc705f1d685b16d45f52ad6b940b2"

// ---------------------------------------------------------------- timing
#define TICK_MS              40      // v0.28.4 loop period (delay 40 ms); one animation step per tick
#define TEMP_PERIOD_MS       1000    // request a DS18B20 conversion about once a second
#define TEMP_CONVERSION_MS   750     // 12-bit conversion time
#define SYNC_INTERVAL_MS     5000    // v0.28.4 sync period
#define WDT_TIMEOUT_MS       20000   // hardware task watchdog (exceeds worst-case HTTP time)

// ---------------------------------------------------------------- v0.28.4 proxy model (sensor-derived, not physics)
#define BETA_GLOBAL_DEFAULT  1.5f    // v0.28.4 GuardianState default [policy: beta_default]
#define BETA_MIN             0.1f    // clamp for beta_global [policy: telemetry.beta_min]
#define BETA_MAX             5.0f    // [policy: telemetry.beta_max]
#define TAU_STEP             0.002f  // tau_global += 0.002 per 40 ms tick
#define OIL_TARGET_C         28.0f   // coherence is highest at this temperature
#define N_NODES              100.0f  // "N" in N_eff
#define DELTAS_LOG_ABOVE     60.0f   // print "High ΔS" above this (throttled to 1/s)

// Values marked [policy] below are compiled DEFAULTS. With operator-signed policy enabled
// (LK_OPERATOR_ED25519_PUBKEY_B64 in secrets.h) a verified policy replaces them at runtime.

// ---------------------------------------------------------------- v0.28.4 guardian rules [policy]
#define Q_DELTAS_ABOVE       75.0f
#define Q_BETA_ABOVE         3.0f
#define Q_COHERENCE_BELOW    90.0f

// ---------------------------------------------------------------- v0.28.4 pump levels (8-bit duty) [policy]
#define PUMP_LEVEL_BASE      80
#define PUMP_LEVEL_C150      130     // coherence > 150
#define PUMP_LEVEL_C200      190     // coherence > 200
#define PUMP_LEVEL_DELTAS    220     // deltaS > 70
#define PUMP_DELTAS_ABOVE    70.0f

// ---------------------------------------------------------------- v0.29 safety interlock (overrides the pump)
#define TEMP_MAX_C           60.0f   // trip: pump off at/above this [policy, bounded 30-80]
#define TEMP_HYST_C          5.0f    // re-arm once temp <= TEMP_MAX_C - TEMP_HYST_C [policy, 1-20]
// Not configurable by policy (fail-safe): stale/sensor-fault interlock, re-arm, ramp.
#define SENSOR_STALE_MS      5000    // no valid reading for this long => pump off + quarantine
#define SENSOR_GOOD_TO_ARM   3       // consecutive good readings needed after a fault
#define FAULT_CLEAR_MS       10000   // sensor faults must be clear this long before leaving quarantine
#define PUMP_RAMP_PER_S      120.0f  // soft-start: max increase in duty counts per second (decrease is instant)

// ---------------------------------------------------------------- display extras (v0.29)
#define STATUS_PIXEL         1       // pixel 0 blinks amber (Wi-Fi down) / magenta (upload failing); 0 = off

// ---------------------------------------------------------------- network
#define WIFI_BACKOFF_MIN_MS  1000
#define WIFI_BACKOFF_MAX_MS  60000
#define HTTP_CONNECT_TIMEOUT_MS 3000
#define TLS_HANDSHAKE_TIMEOUT_S 5
#define HTTP_RESPONSE_TIMEOUT_MS 4000
#define HTTP_MAX_RESPONSE_BYTES 1024
#define TELEMETRY_PATH       "/telemetry"
#define NET_TASK_STACK       12288
#define FW_VERSION           "0.30.0"

// ---------------------------------------------------------------- operator-signed policy (v0.30)
#define POLICY_PATH          "/policy?view=device"
#define POLICY_REFRESH_MS    600000  // re-check every 10 min (also when the server's hash differs)
#define POLICY_RETRY_MS      60000   // after a network/server error
#define POLICY_MAX_BYTES     3584    // response buffer; server reports fits_device_buffer on PUT
#define POLICY_NVS_NAMESPACE "lkpolicy"
#define POLICY_FORGET_STORED 0       // set 1 for ONE flash to clear the stored policy (see README)

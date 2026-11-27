#!/usr/bin/env bash
# Build policy_core.h on the host against rweather/Crypto + ArduinoJson and run it
# on vectors signed by the real server code. Needs: g++, python with lattice_keeper.
# Copyright (c) 2026 Jay Vallea. All Rights Reserved.
set -euo pipefail
cd "$(dirname "$0")"
LIBS="${ARDUINO_LIBS:-$HOME/Arduino/libraries}"
C="$LIBS/Crypto/src"
OUT="$(mktemp -d)"
python gen_vectors.py "$OUT/vectors.txt"
g++ -std=c++17 -O1 -Wall -I"$C" -I"$LIBS/ArduinoJson/src" -o "$OUT/t" test_policy_core.cpp rng_stub.cpp \
  "$C/Ed25519.cpp" "$C/Curve25519.cpp" "$C/SHA512.cpp" "$C/SHA256.cpp" "$C/Hash.cpp" \
  "$C/BigNumberUtil.cpp" "$C/Crypto.cpp"
"$OUT/t" "$OUT/vectors.txt"

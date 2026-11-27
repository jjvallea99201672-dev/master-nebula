"""Generate test vectors for the device-side verifier using the real server code.

Copyright (c) 2026 Jay Vallea. All Rights Reserved.
Writes vectors.txt: one case per line: name|expect|device_view_json_b64|sig_b64|pub_b64|last_version|device_id|expect_beta
"""

import base64
import json
import sys
import tempfile

from lattice_keeper.config import Settings
from lattice_keeper.crypto import canonical_json
from lattice_keeper.operator import OperatorKey
from lattice_keeper.policy import genesis_document, next_proposal

b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731

tmp = tempfile.mkdtemp()
op = OperatorKey.generate(f"{tmp}/op", None, pqc=None)
other = OperatorKey.generate(f"{tmp}/other", None, pqc=None)
g = genesis_document(Settings.from_env({"LATTICE_API_KEY": "x" * 32}))
pub = b64(op.public.ed25519)
rows = []


def case(name, expect, doc, signer=op, last=1, dev="lk-AAAA00000001", beta="nan", canon=None, sig=None):
    c = canon if canon is not None else canonical_json(doc)
    s = sig if sig is not None else signer.sign(canonical_json(doc))["ed25519"]["sig"]
    # Wrapped exactly like GET /policy?view=device (aiohttp json.dumps escapes non-ASCII as \\uXXXX).
    view = json.dumps({"canonical": c.decode(), "ed25519_sig": s}).encode()
    rows.append("|".join([name, expect, b64(view), s, pub, str(last), dev, beta]))


v2 = next_proposal(g)
v2["values"]["guardian"]["q_deltas_above"] = 55.5
v2["values"]["telemetry"]["beta_global"] = 2.0
v2["values"]["telemetry"]["beta_global_devices"] = {"lk-AAAA00000001": 0.7}
case("valid_device_override", "ok", v2, beta="0.7")
case("valid_global_beta", "ok", v2, dev="lk-BBBB00000002", beta="2.0")
case("rollback_same_version", "rollback_or_replay", v2, last=2)
case("wrong_key", "bad_signature", v2, signer=other)
tampered = canonical_json(v2).replace(b"55.5", b"95.5")
case("tampered_bytes", "bad_signature", v2, canon=tampered)
case("bad_b64", "bad_base64", v2, sig="!!!")
case("unsigned", "unsigned", v2, sig="")
hot = next_proposal(g)
hot["values"]["guardian"]["temp_max_c"] = 95.0  # validly signed but outside hard bounds
case("signed_out_of_bounds", "out_of_bounds", hot)
fl = next_proposal(g)
fl["values"]["guardian"]["pump_level_base"] = 80.5
case("float_pump_level", "out_of_bounds", fl)
ints = next_proposal(g)
ints["values"]["guardian"]["q_deltas_above"] = 60
case("int_for_float_ok", "ok", ints)
unicode_doc = next_proposal(g)
unicode_doc["values"]["rag_guard"]["blocklist"] = ["contraseña", "密码"]
case("utf8_blocklist_ok", "ok", unicode_doc)
fmt = next_proposal(g)
fmt["format"] = "something-else/v1"
case("wrong_format", "bad_format", fmt)
open(sys.argv[1] if len(sys.argv) > 1 else "vectors.txt", "w").write("\n".join(rows) + "\n")
print(f"{len(rows)} vectors")

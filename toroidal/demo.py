#!/usr/bin/env python3
"""End-to-end Toroidal Network demonstration.

Nodes: NB-ROOT (genesis), OUTPOST-SABAOTH, BEACON-01.
Walks through an ML-KEM session, KV-17 beacon scans, the Nyx audit,
a seeding drop, the precursor bridge (pass and fail), chain verification,
a tamper test, and a forged signature.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

# Allow `python demo.py` from this directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from constants import (
    DATA_DIR,
    DSA_NAME,
    GENESIS_ANCHOR,
    KEM_NAME,
    KEY_DIR,
    LEDGER_PATH,
    LEDGER_SIZE,
    ML_DSA_65_PK_SIZE,
    ML_DSA_65_SIG_SIZE,
    ML_DSA_65_SK_SIZE,
    ML_KEM_768_CT_SIZE,
    ML_KEM_768_DK_SIZE,
    ML_KEM_768_EK_SIZE,
    ML_KEM_768_SS_SIZE,
    PRECURSOR_TARGET_CELL,
    ROOT_NODE_ID,
)
from crypto_utils import short_hex
from keys_store import KeyStore
from ledger import ToroidalLedger, tamper_entry
from pq_relay_security import InterstellarPQCManager, verify_signature
from beacon_deployment import ExploratoryWaveBeacon
from exoplanet_audit import ExoplanetHabitibilityAuditor
from seed_dispersal import ExoplanetSeedingProtocol
from precursor_bridge import PrecursorLedgerBridge

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("toroidal.demo")

KV17_LEDGER = Path("/workspace/code/beacons/ledger.json")

FALLBACK_SCANS = [
    {
        "beacon_id": "BEACON-01",
        "sensor_readouts": {
            "gravitational_gradient": 91.4959284,
            "ion_flux_ev": 12.0,
            "x_au": 0.7,
            "y_au": 0.0,
        },
    },
    {
        "beacon_id": "BEACON-02",
        "sensor_readouts": {
            "gravitational_gradient": 7.75212557,
            "ion_flux_ev": 1.619325,
            "x_au": -2.276144,
            "y_au": 0.828449,
        },
    },
    {
        "beacon_id": "BEACON-06",
        "sensor_readouts": {
            "gravitational_gradient": 0.54099066,
            "ion_flux_ev": 0.149072,
            "x_au": 1.616857,
            "y_au": 9.169654,
        },
    },
    {
        "beacon_id": "BEACON-03",
        "sensor_readouts": {
            "gravitational_gradient": 7.76282912,
            "ion_flux_ev": 1.619325,
            "x_au": 2.276144,
            "y_au": -0.828449,
        },
    },
]


def banner(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def load_kv17_scans(limit: int = 4) -> list[dict]:
    if not KV17_LEDGER.exists():
        log.info("KV-17 ledger not found at %s; using fallback scans.", KV17_LEDGER)
        return FALLBACK_SCANS[:limit]
    data = json.loads(KV17_LEDGER.read_text(encoding="utf-8"))
    picks: list[dict] = []
    seen: set[str] = set()
    # Prefer one scan per physical beacon, in file order.
    for entry in data.get("entries") or []:
        bid = entry.get("beacon_id")
        if bid in seen:
            continue
        seen.add(bid)
        picks.append(
            {
                "beacon_id": bid,
                "sensor_readouts": entry["sensor_readouts"],
                "source_cell": entry.get("cell"),
                "source_checksum": entry.get("checksum"),
            }
        )
        if len(picks) >= limit:
            break
    log.info("Loaded %d KV-17 scans from %s.", len(picks), KV17_LEDGER)
    return picks or FALLBACK_SCANS[:limit]


def fresh_ledger(path: Path) -> ToroidalLedger:
    if path.exists():
        path.unlink()
    return ToroidalLedger(path=path)


def main() -> int:
    banner("Toroidal Network toolkit — Series 4 demonstration")
    print(f"Genesis anchor : {GENESIS_ANCHOR}")
    print(f"Ledger size    : {LEDGER_SIZE} cells (256 x 256 torus)")
    print(f"KEM / DSA      : {KEM_NAME} / {DSA_NAME} (kyber-py + dilithium-py)")
    print(
        f"Key sizes      : KEM ek {ML_KEM_768_EK_SIZE} B, dk {ML_KEM_768_DK_SIZE} B, "
        f"ct {ML_KEM_768_CT_SIZE} B, ss {ML_KEM_768_SS_SIZE} B"
    )
    print(
        f"                 DSA pk {ML_DSA_65_PK_SIZE} B, sk {ML_DSA_65_SK_SIZE} B, "
        f"sig {ML_DSA_65_SIG_SIZE} B"
    )
    print("Session crypto : HKDF-SHA256 -> AES-256-GCM")
    print("This is a learning prototype, not audited security software.")

    KEY_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    store = KeyStore(KEY_DIR)

    banner("1. Nodes and post-quantum keypairs")
    root = InterstellarPQCManager(ROOT_NODE_ID, "New Brunswick Genesis", keystore=store)
    sabaoth = InterstellarPQCManager("OUTPOST-SABAOTH", "Outer Rim / Epoch 17", keystore=store)
    beacon_node = InterstellarPQCManager("BEACON-01", "Uncharted Sector KV-17", keystore=store)
    for node in (root, sabaoth, beacon_node):
        print(
            f"  {node.node_id:20} sector={node.sector!r}  "
            f"KEM ek={len(node.ml_kem_public_key)} B  DSA pk={len(node.ml_dsa_public_key)} B"
        )
    print(f"  Private keys stored under {KEY_DIR} with mode 0600 (never printed).")

    banner("2. ML-KEM-768 session (BEACON-01 -> OUTPOST-SABAOTH)")
    origin_sess, envelope, target_sess = beacon_node.open_session(sabaoth)
    print(f"  Ciphertext     : {envelope['ciphertext_bytes']} B, hash {short_hex(envelope['ciphertext_hash'])}")
    print(f"  Protocol       : {envelope['protocol']} + {envelope['kdf']} + {envelope['aead']}")
    plaintext = (
        "Sector KV-17 mapped. Hidden ice giant Nyx locked at 2.12, 6.51 AU. "
        "First Council Charter accepted. Toroidal grid synchronized."
    )
    sealed = origin_sess.encrypt(plaintext)
    opened = target_sess.decrypt(sealed)
    print(f"  Plaintext      : {plaintext}")
    print(f"  Ciphertext     : {len(sealed['ciphertext']) // 2} B (incl. GCM tag), nonce {sealed['nonce']}")
    print(f"  Decrypted      : {opened.decode('utf-8')}")
    print(f"  Match          : {opened.decode('utf-8') == plaintext}")
    if opened.decode("utf-8") != plaintext:
        raise SystemExit("session decrypt mismatch")

    banner("3. KV-17 beacon scans committed to the shared ledger")
    ledger = fresh_ledger(LEDGER_PATH)
    scans = load_kv17_scans(4)
    for scan in scans:
        probe = ExploratoryWaveBeacon(
            scan["beacon_id"],
            "Uncharted Sector KV-17",
            ledger=ledger,
            signer=beacon_node,
        )
        checksum = probe.map_gravitational_topography(scan["sensor_readouts"])
        result = probe.last_result or {}
        print(
            f"  {scan['beacon_id']:12} checksum={checksum[:12]}... "
            f"cell={result['cell']:05d} (preferred {result['preferred_cell']:05d})"
        )

    banner("4. Nyx habitability audit (criterion is fiction)")
    auditor = ExoplanetHabitibilityAuditor(
        "Nyx", "Uncharted Sector KV-17", ledger=ledger, signer=beacon_node
    )
    # Ice giant: far from 432 Hz and poorly "tectonic" — should require stabilization.
    nyx_env = {
        "body_class": "ice giant",
        "tectonic_stability_score": 0.31,
        "natural_resonance_hz": 419.0,
        "detected_x_au": 2.1053763440860216,
        "detected_y_au": 6.571326164874552,
        "mass_msun": 0.00045,
    }
    nyx_checksum = auditor.audit_habitability(nyx_env)
    print(f"  Status         : {auditor.last_status}")
    print(f"  Resonance delta: {auditor.last_delta:.2f} Hz (fiction window is < 5 Hz from 432)")
    print(f"  Checksum       : {nyx_checksum[:16]}...")

    banner("5. Seeding drop (limited piezoelectric companion payload)")
    seeder = ExoplanetSeedingProtocol(
        "Nyx", "KV-17", ledger=ledger, signer=sabaoth
    )
    manifest = {
        "crop_strain": "New Brunswick Heirloom Heritage Mix",
        "mycorrhizal_spores_millions": 50.0,
        "purpose": "piezoelectric stabilization companion drop",
        "audit_status": auditor.last_status,
    }
    seed_checksum = seeder.execute_seeding_drop(manifest)
    print(f"  Crop           : {manifest['crop_strain']}")
    print(f"  Spores         : {manifest['mycorrhizal_spores_millions']} million")
    print(f"  Checksum       : {seed_checksum[:16]}...")

    banner("6. Precursor bridge — fail, then pass into Cell 65500")
    bridge = PrecursorLedgerBridge(ledger=ledger)
    precursor_payload = {
        "beacon": "Sector 51 bismuth-quartz",
        "material": "bismuth-quartz",
        "sector": "51",
        "reserved_cell": PRECURSOR_TARGET_CELL,
        "note": "ancient isomorphic beacon",
    }

    # FAIL: signed by the outpost, not the root, so reserved placement is denied.
    bad_sig = sabaoth.sign_toroidal_state(precursor_payload)
    failed = bridge.verify_precursor_isomorphism(
        precursor_payload,
        signature=bad_sig,
        public_key=sabaoth.ml_dsa_public_key,
        signer_id=sabaoth.node_id,
        ledger=ledger,
    )
    print(f"  FAIL attempt   : ok={failed}")
    print(f"  Reason         : {bridge.last_result['reason']}")
    print(f"  Derived cell   : {bridge.last_result['derived_cell']} (target {PRECURSOR_TARGET_CELL})")

    # PASS: root signs the same payload; reserved placement is authorized.
    good_sig = root.sign_toroidal_state(precursor_payload)
    passed = bridge.verify_precursor_isomorphism(
        precursor_payload,
        signature=good_sig,
        public_key=root.ml_dsa_public_key,
        signer_id=root.node_id,
        ledger=ledger,
        manager=root,
    )
    print(f"  PASS attempt   : ok={passed}")
    print(f"  Reason         : {bridge.last_result['reason']}")
    print(f"  Placed cell    : {bridge.last_result.get('placed_cell')}")
    print(f"  Mode           : {bridge.last_result.get('mode')}")
    if not passed or failed:
        raise SystemExit("precursor bridge did not produce the expected pass/fail pair")

    banner("7. verify_chain()")
    chain = ledger.verify_chain()
    print(f"  ok             : {chain['ok']}")
    print(f"  entries        : {chain['entries']}")
    print(f"  head hash      : {short_hex(chain['head_hash'])}...")
    print(f"  errors         : {chain['errors'] or 'none'}")
    print(f"  ledger file    : {ledger.path}")
    if not chain["ok"]:
        raise SystemExit("chain verification failed on the intact ledger")

    banner("8. Tamper test (copy of the ledger, one payload field edited)")
    tamper_path = DATA_DIR / "ledger_tampered.json"
    snapshot = ledger.copy_to(tamper_path)
    def _edit(entry):
        # Flip a value inside the committed payload without updating hashes or sigs.
        payload = entry.get("payload") or {}
        if "checksum" in payload:
            payload["checksum"] = "00" * 32
        else:
            payload["tampered"] = True
    tamper_entry(snapshot, 0, _edit)
    tampered = snapshot.verify_chain()
    print(f"  ok             : {tampered['ok']} (expected False)")
    print(f"  first errors   : {tampered['errors'][:3]}")
    # The original ledger must still verify.
    intact = ledger.verify_chain()
    print(f"  original still : {intact['ok']}")
    if tampered["ok"] or not intact["ok"]:
        raise SystemExit("tamper test did not behave as expected")

    banner("9. Forged signature rejected")
    body = {"cell": 1, "module": "forge-test", "payload": {"msg": "I am the root"}, "signer": ROOT_NODE_ID}
    forged = sabaoth.sign_toroidal_state(body)
    forged["signer"] = ROOT_NODE_ID  # claim to be the root, but signed with Sabaoth's key
    accepted = verify_signature(body, forged, root.ml_dsa_public_key)
    also_raw = verify_signature(body, forged, sabaoth.ml_dsa_public_key)
    print(f"  Verified as NB-ROOT     : {accepted} (expected False)")
    print(f"  Verified as SABAOTH key : {also_raw} (True — it is Sabaoth's signature, mislabeled)")
    # A completely random signature token should also fail.
    garbage = dict(forged)
    garbage["quantum_signature"] = "00" * ML_DSA_65_SIG_SIZE
    garbage.pop("hash_digest", None)
    garbage_ok = verify_signature(body, garbage, sabaoth.ml_dsa_public_key)
    print(f"  Zeroed signature        : {garbage_ok} (expected False)")
    if accepted or garbage_ok or not also_raw:
        raise SystemExit("forged-signature test did not behave as expected")

    banner("Done")
    print(f"  Shared ledger  : {ledger.path} ({chain['entries']} signed entries)")
    print(f"  Key store      : {KEY_DIR}")
    print("  All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Precursor bridge pass / fail tests."""

from __future__ import annotations

import unittest

from helpers import make_ledger, make_node, make_store, temp_dir
from constants import PRECURSOR_TARGET_CELL, ROOT_NODE_ID
from precursor_bridge import PrecursorLedgerBridge


PAYLOAD = {
    "beacon": "Sector 51 bismuth-quartz",
    "material": "bismuth-quartz",
    "sector": "51",
    "reserved_cell": PRECURSOR_TARGET_CELL,
}


class TestPrecursorBridge(unittest.TestCase):
    def test_unsigned_payload_fails(self):
        bridge = PrecursorLedgerBridge()
        self.assertFalse(bridge.verify_precursor_isomorphism(PAYLOAD))
        self.assertIn("missing", bridge.last_result["reason"])

    def test_non_root_reserved_request_fails(self):
        with temp_dir() as td:
            store = make_store(td)
            ledger = make_ledger(td)
            other = make_node(store, "OUTPOST-SABAOTH")
            sig = other.sign_toroidal_state(PAYLOAD)
            bridge = PrecursorLedgerBridge(ledger=ledger)
            ok = bridge.verify_precursor_isomorphism(
                PAYLOAD,
                signature=sig,
                public_key=other.ml_dsa_public_key,
                signer_id=other.node_id,
                ledger=ledger,
            )
            self.assertFalse(ok)
            self.assertIn("requires signer", bridge.last_result["reason"])
            self.assertIsNone(ledger.get_entry_at(PRECURSOR_TARGET_CELL))

    def test_tampered_payload_fails(self):
        with temp_dir() as td:
            store = make_store(td)
            root = make_node(store, ROOT_NODE_ID)
            sig = root.sign_toroidal_state(PAYLOAD)
            tampered = dict(PAYLOAD, material="pyrite")
            bridge = PrecursorLedgerBridge()
            ok = bridge.verify_precursor_isomorphism(
                tampered,
                signature=sig,
                public_key=root.ml_dsa_public_key,
                signer_id=ROOT_NODE_ID,
            )
            self.assertFalse(ok)
            self.assertIn("signature", bridge.last_result["reason"])

    def test_root_reserved_placement_passes_and_commits(self):
        with temp_dir() as td:
            store = make_store(td)
            ledger = make_ledger(td)
            root = make_node(store, ROOT_NODE_ID, "New Brunswick Genesis")
            sig = root.sign_toroidal_state(PAYLOAD)
            bridge = PrecursorLedgerBridge(ledger=ledger)
            ok = bridge.verify_precursor_isomorphism(
                PAYLOAD,
                signature=sig,
                public_key=root.ml_dsa_public_key,
                signer_id=ROOT_NODE_ID,
                ledger=ledger,
                manager=root,
            )
            self.assertTrue(ok, bridge.last_result)
            self.assertEqual(bridge.last_result["mode"], "reserved")
            self.assertEqual(bridge.last_result["placed_cell"], PRECURSOR_TARGET_CELL)
            entry = ledger.get_entry_at(PRECURSOR_TARGET_CELL)
            self.assertIsNotNone(entry)
            self.assertTrue(entry["reserved"])
            self.assertEqual(entry["signer"], ROOT_NODE_ID)
            self.assertTrue(ledger.verify_chain()["ok"])

    def test_second_claim_of_same_cell_fails(self):
        with temp_dir() as td:
            store = make_store(td)
            ledger = make_ledger(td)
            root = make_node(store, ROOT_NODE_ID)
            sig = root.sign_toroidal_state(PAYLOAD)
            bridge = PrecursorLedgerBridge(ledger=ledger)
            self.assertTrue(
                bridge.verify_precursor_isomorphism(
                    PAYLOAD,
                    signature=sig,
                    public_key=root.ml_dsa_public_key,
                    signer_id=ROOT_NODE_ID,
                    ledger=ledger,
                    manager=root,
                )
            )
            ok = bridge.verify_precursor_isomorphism(
                PAYLOAD,
                signature=sig,
                public_key=root.ml_dsa_public_key,
                signer_id=ROOT_NODE_ID,
                ledger=ledger,
                manager=root,
            )
            self.assertFalse(ok)
            self.assertIn("already occupied", bridge.last_result["reason"])

    def test_natural_landing_is_accepted_when_cell_matches(self):
        """If a payload naturally hashes to the target cell, no reserved flag is needed."""
        with temp_dir() as td:
            store = make_store(td)
            root = make_node(store, ROOT_NODE_ID)
            # Use a tiny custom target so we can search for a natural landing quickly.
            target = 17
            bridge = PrecursorLedgerBridge(target_cell=target)
            payload = None
            for i in range(200_000):
                candidate = {"beacon": "natural-search", "n": i}
                _h, cell = bridge.derived_hash_and_cell(candidate)
                if cell == target:
                    payload = candidate
                    break
            self.assertIsNotNone(payload)
            sig = root.sign_toroidal_state(payload)
            ok = bridge.verify_precursor_isomorphism(
                payload,
                signature=sig,
                public_key=root.ml_dsa_public_key,
                signer_id=ROOT_NODE_ID,
            )
            self.assertTrue(ok, bridge.last_result)
            self.assertEqual(bridge.last_result["mode"], "natural")


class TestSignerSpoofing(unittest.TestCase):
    """signature['signer'] is a claim; it must not grant root authority by itself."""

    def _forged(self, eve):
        sig = dict(eve.sign_toroidal_state(PAYLOAD))
        sig["signer"] = ROOT_NODE_ID  # EVE relabels her signature as root's
        return sig

    def test_spoofed_root_with_own_key_is_rejected(self):
        with temp_dir() as td:
            eve = make_node(make_store(td), "EVE")
            bridge = PrecursorLedgerBridge()
            ok = bridge.verify_precursor_isomorphism(
                PAYLOAD, self._forged(eve), public_key=eve.ml_dsa_public_key
            )
            self.assertFalse(ok)
            self.assertIn("registered key", bridge.last_result["reason"])

    def test_spoofed_root_against_registered_key_is_rejected(self):
        with temp_dir() as td:
            store = make_store(td)
            ledger = make_ledger(td)
            ledger.register_public_key(make_node(store, ROOT_NODE_ID))
            eve = make_node(store, "EVE")
            bridge = PrecursorLedgerBridge(ledger=ledger)
            ok = bridge.verify_precursor_isomorphism(
                PAYLOAD, self._forged(eve), public_key=eve.ml_dsa_public_key
            )
            self.assertFalse(ok)
            self.assertIn("does not match", bridge.last_result["reason"])

    def test_genuine_root_with_matching_registered_key_passes(self):
        with temp_dir() as td:
            store = make_store(td)
            ledger = make_ledger(td)
            root = make_node(store, ROOT_NODE_ID)
            ledger.register_public_key(root)
            bridge = PrecursorLedgerBridge(ledger=ledger)
            sig = root.sign_toroidal_state(PAYLOAD)
            ok = bridge.verify_precursor_isomorphism(
                PAYLOAD, sig, public_key=root.ml_dsa_public_key
            )
            self.assertTrue(ok, bridge.last_result)
            self.assertEqual(bridge.last_result["mode"], "reserved")


if __name__ == "__main__":
    unittest.main()

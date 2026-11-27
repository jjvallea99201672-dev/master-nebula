"""ML-DSA-65 sign / verify / reject tests."""

from __future__ import annotations

import unittest

from helpers import make_node, make_store, temp_dir
from constants import GENESIS_ANCHOR, ML_DSA_65_PK_SIZE, ML_DSA_65_SIG_SIZE
from crypto_utils import signed_message
from dilithium_py.ml_dsa import ML_DSA_65
from pq_relay_security import verify_signature


class TestSignatures(unittest.TestCase):
    def test_raw_ml_dsa_65_verify_and_reject(self):
        pk, sk = ML_DSA_65.keygen()
        self.assertEqual(len(pk), ML_DSA_65_PK_SIZE)
        msg = signed_message({"epoch": 17, "status": "ok"})
        sig = ML_DSA_65.sign(sk, msg)
        self.assertEqual(len(sig), ML_DSA_65_SIG_SIZE)
        self.assertTrue(ML_DSA_65.verify(pk, msg, sig))
        self.assertFalse(ML_DSA_65.verify(pk, signed_message({"epoch": 18, "status": "ok"}), sig))

    def test_manager_sign_and_verify(self):
        with temp_dir() as td:
            store = make_store(td)
            node = make_node(store, "NB-ROOT", "Genesis")
            payload = {"message": "First Council Charter accepted.", "anchor": GENESIS_ANCHOR[:16]}
            sig = node.sign_toroidal_state(payload)
            self.assertEqual(sig["algorithm"], "ML-DSA-65")
            self.assertEqual(sig["signer"], "NB-ROOT")
            self.assertEqual(sig["quantum_signature_bytes"], ML_DSA_65_SIG_SIZE)
            self.assertTrue(node.verify_toroidal_state(payload, sig))
            self.assertTrue(verify_signature(payload, sig, node.ml_dsa_public_key))

    def test_tampered_payload_rejected(self):
        with temp_dir() as td:
            node = make_node(make_store(td), "NB-ROOT")
            payload = {"cell": 10, "module": "test"}
            sig = node.sign_toroidal_state(payload)
            tampered = {"cell": 11, "module": "test"}
            self.assertFalse(node.verify_toroidal_state(tampered, sig))

    def test_wrong_key_rejected(self):
        with temp_dir() as td:
            store = make_store(td)
            root = make_node(store, "NB-ROOT")
            other = make_node(store, "OUTPOST-SABAOTH")
            payload = {"msg": "hello"}
            sig = root.sign_toroidal_state(payload)
            self.assertFalse(other.verify_toroidal_state(payload, sig))
            self.assertFalse(verify_signature(payload, sig, other.ml_dsa_public_key))
            self.assertTrue(verify_signature(payload, sig, root.ml_dsa_public_key))

    def test_forged_signer_label_rejected_against_claimed_key(self):
        with temp_dir() as td:
            store = make_store(td)
            root = make_node(store, "NB-ROOT")
            other = make_node(store, "OUTPOST-SABAOTH")
            payload = {"msg": "I am the root"}
            sig = other.sign_toroidal_state(payload)
            sig = dict(sig, signer="NB-ROOT")
            self.assertFalse(verify_signature(payload, sig, root.ml_dsa_public_key))

    def test_keys_are_not_in_repr_or_public_bundle(self):
        with temp_dir() as td:
            node = make_node(make_store(td), "NB-ROOT")
            text = repr(node) + str(node.public_bundle())
            self.assertNotIn(node._dsa_sk.hex(), text)
            self.assertNotIn(node._kem_dk.hex(), text)
            self.assertIn("ml_dsa_public_key_hex", node.public_bundle())


if __name__ == "__main__":
    unittest.main()

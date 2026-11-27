"""ML-KEM-768 round-trip and session AEAD tests."""

from __future__ import annotations

import unittest

from helpers import make_node, make_store, temp_dir
from constants import ML_KEM_768_CT_SIZE, ML_KEM_768_EK_SIZE, ML_KEM_768_SS_SIZE
from crypto_utils import derive_session_key
from kyber_py.ml_kem import ML_KEM_768
from pq_relay_security import InterstellarPQCManager


class TestKemRoundtrip(unittest.TestCase):
    def test_raw_ml_kem_768_roundtrip(self):
        ek, dk = ML_KEM_768.keygen()
        self.assertEqual(len(ek), ML_KEM_768_EK_SIZE)
        ss1, ct = ML_KEM_768.encaps(ek)
        self.assertEqual(len(ct), ML_KEM_768_CT_SIZE)
        self.assertEqual(len(ss1), ML_KEM_768_SS_SIZE)
        ss2 = ML_KEM_768.decaps(dk, ct)
        self.assertEqual(ss1, ss2)

    def test_wrong_decapsulation_key_does_not_match(self):
        ek, _dk = ML_KEM_768.keygen()
        _ek2, dk2 = ML_KEM_768.keygen()
        ss1, ct = ML_KEM_768.encaps(ek)
        ss2 = ML_KEM_768.decaps(dk2, ct)
        # FIPS 203 implicit rejection returns a pseudo-random secret, not the real one.
        self.assertNotEqual(ss1, ss2)
        self.assertEqual(len(ss2), ML_KEM_768_SS_SIZE)

    def test_manager_session_encrypt_decrypt(self):
        with temp_dir() as td:
            store = make_store(td)
            alice = make_node(store, "ALICE", "A")
            bob = make_node(store, "BOB", "B")
            origin, envelope, target = alice.open_session(bob)
            self.assertEqual(envelope["protocol"], "ML-KEM-768")
            self.assertEqual(envelope["ciphertext_bytes"], ML_KEM_768_CT_SIZE)
            sealed = origin.encrypt("harmonic lock confirmed")
            opened = target.decrypt(sealed)
            self.assertEqual(opened, b"harmonic lock confirmed")

    def test_hkdf_keys_match_on_both_sides(self):
        with temp_dir() as td:
            store = make_store(td)
            alice = make_node(store, "ALICE")
            bob = make_node(store, "BOB")
            envelope = alice.encapsulate_quantum_session(bob.ml_kem_public_key, target_id="BOB")
            ss_alice = alice._last_shared_secret
            ss_bob = bob.decapsulate_quantum_session(envelope)
            self.assertEqual(ss_alice, ss_bob)
            k1 = derive_session_key(ss_alice, "ALICE", "BOB")
            k2 = derive_session_key(ss_bob, "ALICE", "BOB")
            self.assertEqual(k1, k2)
            self.assertEqual(len(k1), 32)

    def test_placeholder_pubkey_is_rejected(self):
        with temp_dir() as td:
            alice = make_node(make_store(td), "ALICE")
            with self.assertRaises(ValueError):
                alice.encapsulate_quantum_session("MLKEM768_PUB_ALICE_fd0adf90")


if __name__ == "__main__":
    unittest.main()

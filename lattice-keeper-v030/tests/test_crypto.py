import copy
import os
import stat

import pytest

from lattice_keeper.crypto import (
    KeyFileError,
    KeyRing,
    PQCUnavailableError,
    canonical_json,
    verify_envelope,
)

from .conftest import requires_pqc

PAYLOAD = canonical_json({"a": 1, "b": "two"})


def test_classical_sign_verify_roundtrip(classical_ring):
    env = classical_ring.sign(PAYLOAD)
    assert env["pqc"] == "unavailable" and env["ml_dsa_65"] is None and env["alg"] == "Ed25519"
    assert classical_ring.verify(PAYLOAD, env)["valid"] is True


def test_tampered_payload_or_sig_fails(classical_ring):
    env = classical_ring.sign(PAYLOAD)
    assert classical_ring.verify(canonical_json({"a": 2, "b": "two"}), env)["valid"] is False
    bad = copy.deepcopy(env)
    bad["ed25519"]["sig"] = env["ed25519"]["sig"][:-4] + "AAA="
    assert classical_ring.verify(PAYLOAD, bad)["valid"] is False
    assert classical_ring.verify(PAYLOAD, {"garbage": True})["valid"] is False


def test_no_fake_strings_when_pqc_unavailable(classical_ring):
    env = classical_ring.sign(PAYLOAD)
    assert "SIMULATED" not in str(env)
    kem = classical_ring.kem_encrypt(PAYLOAD, b"aad")
    assert kem["status"] == "unavailable"
    assert "ciphertext" not in kem and "kem_ciphertext" not in kem


def test_require_pqc_refuses_without_backend(tmp_path):
    with pytest.raises(PQCUnavailableError, match="REQUIRE_PQC"):
        KeyRing.load_or_create(tmp_path / "k", None, require_pqc=True, pqc=None)


def test_keys_persist_with_0600(tmp_path):
    r1 = KeyRing.load_or_create(tmp_path / "k", None, require_pqc=False, pqc=None)
    sk = tmp_path / "k" / "ed25519.key"
    assert stat.S_IMODE(os.stat(sk).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(tmp_path / "k").st_mode) == 0o700
    r2 = KeyRing.load_or_create(tmp_path / "k", None, require_pqc=False, pqc=None)
    assert r1.public.ed25519 == r2.public.ed25519
    assert r2.verify(PAYLOAD, r1.sign(PAYLOAD))["valid"]


def test_passphrase_encryption_at_rest(tmp_path):
    d = tmp_path / "k"
    r1 = KeyRing.load_or_create(d, "correct horse", require_pqc=False, pqc=None)
    assert r1.encrypted_at_rest
    raw = (d / "ed25519.key").read_text()
    assert '"encrypted": true' in raw and '"secret"' not in raw
    with pytest.raises(KeyFileError, match="LATTICE_KEY_PASSPHRASE"):
        KeyRing.load_or_create(d, None, require_pqc=False, pqc=None)
    with pytest.raises(KeyFileError, match="wrong passphrase"):
        KeyRing.load_or_create(d, "wrong", require_pqc=False, pqc=None)
    r2 = KeyRing.load_or_create(d, "correct horse", require_pqc=False, pqc=None)
    assert r2.public.ed25519 == r1.public.ed25519


def test_verifier_with_other_key_rejects(classical_ring, tmp_path):
    other = KeyRing.load_or_create(tmp_path / "other", None, require_pqc=False, pqc=None)
    assert verify_envelope(PAYLOAD, classical_ring.sign(PAYLOAD), other.public, None)["valid"] is False


# ------------------------------------------------------------ real PQC path
@requires_pqc
def test_hybrid_sign_verify(pqc_ring):
    env = pqc_ring.sign(PAYLOAD)
    assert env["pqc"] == "ml-dsa-65" and env["alg"] == "hybrid:Ed25519+ML-DSA-65"
    res = pqc_ring.verify(PAYLOAD, env)
    assert res == {"valid": True, "payload_hash_match": True, "ed25519": True, "ml_dsa_65": True}


@requires_pqc
def test_hybrid_downgrade_and_tamper_detected(pqc_ring):
    env = pqc_ring.sign(PAYLOAD)
    stripped = {**env, "ml_dsa_65": None, "pqc": "unavailable"}
    assert pqc_ring.verify(PAYLOAD, stripped)["valid"] is False
    bad = copy.deepcopy(env)
    sig = bytearray(__import__("base64").b64decode(bad["ml_dsa_65"]["sig"]))
    sig[10] ^= 1
    bad["ml_dsa_65"]["sig"] = __import__("base64").b64encode(bytes(sig)).decode()
    r = pqc_ring.verify(PAYLOAD, bad)
    assert r["valid"] is False and r["ed25519"] is True and r["ml_dsa_65"] is False


@requires_pqc
def test_pqc_keys_persist_encrypted(tmp_path):
    from .conftest import PQC_BACKEND

    d = tmp_path / "pk"
    r1 = KeyRing.load_or_create(d, "pw", require_pqc=True, pqc=PQC_BACKEND)
    r2 = KeyRing.load_or_create(d, "pw", require_pqc=True, pqc=PQC_BACKEND)
    assert r1.public.ml_dsa_65 == r2.public.ml_dsa_65
    assert r2.verify(PAYLOAD, r1.sign(PAYLOAD))["valid"]
    assert stat.S_IMODE(os.stat(d / "ml_dsa_65.key").st_mode) == 0o600
    assert r1.encrypted_at_rest


@requires_pqc
def test_kem_roundtrip_and_aad_binding(pqc_ring):
    env = pqc_ring.kem_encrypt(PAYLOAD, b"RWA-1")
    assert env["status"] == "encrypted" and env["format"] == "lk-kem-v1"
    assert PAYLOAD not in __import__("base64").b64decode(env["ciphertext"])
    assert pqc_ring.kem_decrypt(env) == PAYLOAD
    tampered = {**env, "aad": __import__("base64").b64encode(b"RWA-2").decode()}
    with pytest.raises(Exception):
        pqc_ring.kem_decrypt(tampered)

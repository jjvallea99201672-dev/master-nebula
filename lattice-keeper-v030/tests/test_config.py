import pytest

from lattice_keeper.config import ConfigError, Settings

from .conftest import ENV


def test_missing_key():
    with pytest.raises(ConfigError):
        Settings.from_env({})


def test_short_key_rejected():
    with pytest.raises(ConfigError, match="at least 32"):
        Settings.from_env({"LATTICE_API_KEY": "short"})


def test_bad_anchor_hash():
    with pytest.raises(ConfigError):
        Settings.from_env({**ENV, "REFERENCE_ANCHOR_HASH": "nothex"})


def test_repr_hides_secrets():
    s = Settings.from_env({**ENV, "LATTICE_KEY_PASSPHRASE": "pw-secret"})
    assert ENV["LATTICE_API_KEY"] not in repr(s) and "pw-secret" not in repr(s)


def test_reference_disabled_by_default():
    s = Settings.from_env({"LATTICE_API_KEY": "x" * 32})
    assert not s.reference.enabled

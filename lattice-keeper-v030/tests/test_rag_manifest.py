import pytest

from lattice_keeper.rag import IndexVerificationError, verify_index, write_manifest


def _index(tmp_path):
    d = tmp_path / "idx"
    d.mkdir()
    (d / "index.faiss").write_bytes(b"faiss-bytes")
    (d / "index.pkl").write_bytes(b"pickle-bytes")
    return d


def test_verified_index_passes(tmp_path):
    d = _index(tmp_path)
    pin = write_manifest(d)
    verify_index(d, pin)


def test_no_pin_refuses(tmp_path):
    d = _index(tmp_path)
    write_manifest(d)
    with pytest.raises(IndexVerificationError, match="not set"):
        verify_index(d, None)


def test_tampered_pickle_refused(tmp_path):
    d = _index(tmp_path)
    pin = write_manifest(d)
    (d / "index.pkl").write_bytes(b"malicious")
    with pytest.raises(IndexVerificationError, match="index.pkl"):
        verify_index(d, pin)


def test_rewritten_manifest_refused(tmp_path):
    d = _index(tmp_path)
    pin = write_manifest(d)
    (d / "index.pkl").write_bytes(b"malicious")
    write_manifest(d)  # attacker regenerates manifest
    with pytest.raises(IndexVerificationError, match="pinned"):
        verify_index(d, pin)


def _build_real_index(tmp_path):
    lc = pytest.importorskip("langchain_community.vectorstores")
    pytest.importorskip("langchain_ollama")
    pytest.importorskip("faiss")
    from langchain_core.embeddings import FakeEmbeddings

    d = tmp_path / "real"
    lc.FAISS.from_texts(["alpha", "beta"], FakeEmbeddings(size=8)).save_local(str(d))
    return d, write_manifest(d)


def test_real_faiss_index_loads_only_when_pinned(tmp_path):
    from lattice_keeper.rag import RAGEngine

    from .conftest import make_settings

    d, pin = _build_real_index(tmp_path)
    ok = RAGEngine.from_settings(
        make_settings(RAG_ENABLED="true", RAG_INDEX_DIR=str(d), RAG_INDEX_MANIFEST_SHA256=pin)
    )
    assert ok.status == "enabled" and ok.vectorstore is not None

    unpinned = RAGEngine.from_settings(make_settings(RAG_ENABLED="true", RAG_INDEX_DIR=str(d)))
    assert unpinned.status == "error" and unpinned.vectorstore is None

    (d / "index.pkl").write_bytes(b"cos\nsystem\n(S'echo pwned'\ntR.")  # classic pickle RCE payload
    bad = RAGEngine.from_settings(
        make_settings(RAG_ENABLED="true", RAG_INDEX_DIR=str(d), RAG_INDEX_MANIFEST_SHA256=pin)
    )
    assert bad.status == "error" and bad.vectorstore is None

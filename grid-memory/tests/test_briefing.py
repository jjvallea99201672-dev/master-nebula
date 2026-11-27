import pytest

from gridmem import evidence as ev
from gridmem.briefing import build_briefing


def _fill(store, n=40):
    for i in range(n):
        store.add("fact", f"claimed fact number {i} " + "lorem ipsum " * 20, ["bulk"])


def test_format(store, tmp_path):
    f = tmp_path / "x"; f.write_text("x")
    import hashlib
    v = store.add("fact", "x file hash", ["files"])
    store.attach_evidence(v["id"], ev.hash_check(str(f), hashlib.sha256(b"x").hexdigest()))
    c = store.add("fact", "genesis anchor", ["genesis"])
    old = store.add("fact", "old thing")
    store.retract(old["id"], "wrong")
    store.record_agreement(c["id"], "gemini")
    b = build_briefing(store, model="grok")
    assert b.startswith("=== GRID MEMORY BRIEFING")
    assert "Operator: Jay Vallea (JAY.01, operator ID JVallea99)" in b
    seq, head = store.head()
    assert head[:12] in b and "For: grok" in b
    assert "RULES FOR YOU" in b and "Other models agreeing is not evidence" in b
    assert b.index("[VERIFIED") < b.index("[CLAIMED")
    assert v["id"] in b.split("[CLAIMED")[0]
    assert "agreed by 1 model(s), not evidence" in b
    assert f"{old['id']}(retracted)" in b
    assert "```gridmem\nADD|kind|tags|text\nCORRECT|<id>|text\nQUESTION|text\n```" in b
    assert b.rstrip().endswith("=== END GRID MEMORY BRIEFING ===")


@pytest.mark.parametrize("cap", [2500, 3000, 4000, 8000])
def test_size_cap(store, cap):
    _fill(store)
    b = build_briefing(store, max_chars=cap)
    assert len(b) <= cap
    assert "END GRID MEMORY BRIEFING" in b and "RULES FOR YOU" in b
    if cap < 8000:
        assert "omitted to fit" in b


def test_cap_too_small(store):
    with pytest.raises(ValueError):
        build_briefing(store, max_chars=500)


def test_topic_filter(store):
    store.add("fact", "about physics", ["physics"]); store.add("fact", "about keeper", ["keeper"])
    b = build_briefing(store, topic="physics")
    assert "about physics" in b and "about keeper" not in b and "Topic: physics" in b
    assert "no entries match" in build_briefing(store, topic="zzz")


def test_verified_kept_when_capped(store, tmp_path):
    _fill(store, 60)
    f = tmp_path / "v"; f.write_text("v")
    import hashlib
    v = store.add("fact", "the one verified fact")
    store.attach_evidence(v["id"], ev.hash_check(str(f), hashlib.sha256(b"v").hexdigest()))
    assert "the one verified fact" in build_briefing(store, max_chars=2500)

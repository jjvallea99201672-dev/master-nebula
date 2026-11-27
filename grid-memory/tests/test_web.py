import http.client
import json
import stat
import threading

import pytest

from gridmem.store import Store
from gridmem.web import ensure_token, make_server

REPLY = "answer\n```gridmem\nADD|fact|web|from the web ui\nVERIFY|GM-0001|x\n```"


@pytest.fixture
def srv(tmp_path):
    home = tmp_path / "w"
    s = Store(home); s.add("fact", "seed entry", ["seed"]); s.close()
    token = ensure_token(home)
    server = make_server(home, port=0, token=token)
    t = threading.Thread(target=server.serve_forever, daemon=True); t.start()
    yield home, token, server.server_address[1]
    server.shutdown(); server.server_close()


def req(port, method, path, body=None, headers=None, host=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = {"Host": host or f"127.0.0.1:{port}", "Content-Type": "application/json", **(headers or {})}
    c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
    r = c.getresponse(); data = r.read().decode()
    try:
        data = json.loads(data)
    except json.JSONDecodeError:
        pass
    return r.status, data


def test_token_file_mode(srv):
    home, token, _ = srv
    assert stat.S_IMODE((home / "web_token").stat().st_mode) == 0o600 and len(token) >= 32


def test_reads_open(srv):
    _, _, port = srv
    st, d = req(port, "GET", "/api/entries?q=seed")
    assert st == 200 and d["count"] == 1
    st, d = req(port, "GET", "/api/briefing?max_chars=3000&model=gemini")
    assert st == 200 and len(d["briefing"]) <= 3000 and "For: gemini" in d["briefing"]
    st, page = req(port, "GET", "/")
    assert st == 200 and "Copy briefing" in page


def test_write_needs_token(srv):
    home, token, port = srv
    body = {"text": REPLY, "source": "grok"}
    assert req(port, "POST", "/api/ingest/commit", body)[0] == 401
    assert req(port, "POST", "/api/ingest/commit", body, {"X-GridMem-Token": "wrong"})[0] == 401
    assert req(port, "POST", "/api/ingest/preview", body)[0] == 401
    s = Store(home); assert s.head()[0] == 1; s.close()


def test_preview_then_commit(srv):
    home, token, port = srv
    H = {"X-GridMem-Token": token}
    st, d = req(port, "POST", "/api/ingest/preview", {"text": REPLY, "source": "grok"}, H)
    assert st == 200 and len(d["proposals"]) == 1 and d["rejected"]
    s = Store(home); assert s.head()[0] == 1; s.close()     # preview writes nothing
    st, d = req(port, "POST", "/api/ingest/commit", {"text": REPLY, "source": "grok", "accept": [0]}, H)
    assert st == 200 and d["saved"] == ["GM-0002"]
    s = Store(home)
    e = s.entries()["GM-0002"]
    assert e["status"] == "claimed" and e["source"] == "grok" and s.entries()["GM-0001"]["status"] == "claimed"
    assert s.check()["ok"]; s.close()


def test_host_and_origin_checks(srv):
    _, token, port = srv
    assert req(port, "GET", "/api/entries", host="evil.example:80")[0] == 403
    st, _ = req(port, "POST", "/api/ingest/commit", {"text": REPLY, "source": "x"},
                {"X-GridMem-Token": token, "Origin": "http://evil.example"})
    assert st == 403


def test_binds_localhost_only(tmp_path):
    home = tmp_path / "b"; Store(home).close()
    s = make_server(home, port=0, token="t" * 40)
    assert s.server_address[0] == "127.0.0.1"
    s.server_close()

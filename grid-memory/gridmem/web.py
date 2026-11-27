"""Tiny local web UI (stdlib only). Binds 127.0.0.1 only.

Reads are open on localhost. Writes (ingest commit) need the token from
<home>/web_token (mode 600) in the X-GridMem-Token header. Host and Origin
headers are checked to block DNS-rebinding and cross-site form posts.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .briefing import build_briefing
from .ingest import apply, describe, parse_reply
from .store import GridMemError, Store, clean_text

DEFAULT_PORT = 8792   # 8790/8791 are used by the box's network tunnel
MAX_BODY = 200_000


def ensure_token(home: Path) -> str:
    p = Path(home) / "web_token"
    if not p.exists():
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(32))
    os.chmod(p, 0o600)
    return p.read_text().strip()


PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><title>Grid Memory</title>
<style>body{font:14px system-ui,sans-serif;max-width:1100px;margin:20px auto;padding:0 12px;color:#222}
h1{font-size:20px}textarea{width:100%;font:12px monospace}table{border-collapse:collapse;width:100%}
td,th{border-bottom:1px solid #ddd;padding:4px;text-align:left;vertical-align:top}
.s-verified{color:#070}.s-claimed{color:#a60}.s-check-failed{color:#c00}.s-superseded,.s-retracted{color:#888;text-decoration:line-through}
.box{border:1px solid #ccc;padding:10px;margin:12px 0;border-radius:6px}button{margin:4px 4px 4px 0}</style></head><body>
<h1>Grid Memory</h1><p>A notebook you hand to AI models. They don't learn from it; you paste it in.</p>
<div class="box"><b>Briefing</b> topic <input id="topic"> max chars <input id="mc" value="6000" size="6">
model <input id="model" size="8"> <button onclick="brief()">Build</button><button onclick="copyB()">Copy briefing</button>
<textarea id="brief" rows="12" readonly></textarea></div>
<div class="box"><b>Ingest a model reply</b> (only the ```gridmem block is read)<br>
source model <input id="src" placeholder="grok"> session label <input id="sess" placeholder="optional">
<textarea id="paste" rows="8" placeholder="paste the model's whole reply here"></textarea>
<button onclick="preview()">Preview</button><button onclick="commit()">Save checked proposals</button>
write token <input id="tok" type="password" size="30" placeholder="from data/web_token">
<div id="prev"></div></div>
<div class="box"><b>Entries</b> search <input id="q" oninput="load()"> <span id="head"></span>
<table id="t"></table></div>
<script>
const h=location.hash.match(/token=([^&]+)/); if(h){sessionStorage.gmTok=h[1];history.replaceState(null,'',location.pathname)}
document.getElementById('tok').value=sessionStorage.gmTok||'';
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function load(){const r=await fetch('/api/entries?q='+encodeURIComponent(q.value));const d=await r.json();
 head.textContent='chain head '+d.head.slice(0,12)+'… ('+d.count+' entries)';
 t.innerHTML='<tr><th>id</th><th>status</th><th>kind</th><th>text</th><th>source</th></tr>'+d.entries.map(e=>
 `<tr><td>${esc(e.id)}</td><td class="s-${esc(e.status)}">${esc(e.status)}</td><td>${esc(e.kind)}</td><td>${esc(e.text)}</td><td>${esc(e.source)}</td></tr>`).join('')}
async function brief(){const u='/api/briefing?topic='+encodeURIComponent(topic.value)+'&max_chars='+encodeURIComponent(mc.value)+'&model='+encodeURIComponent(model.value);
 const r=await fetch(u);const d=await r.json();document.getElementById('brief').value=d.briefing||d.error}
async function copyB(){if(!document.getElementById('brief').value)await brief();await navigator.clipboard.writeText(document.getElementById('brief').value)}
function src(){return (document.getElementById('src').value||'unknown model')+(sess.value?' ('+sess.value+')':'')}
async function post(path,body){sessionStorage.gmTok=tok.value;const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-GridMem-Token':tok.value},body:JSON.stringify(body)});return [r.status,await r.json()]}
async function preview(){const [s,d]=await post('/api/ingest/preview',{text:paste.value,source:src()});
 if(s!=200){prev.textContent=d.error;return}
 prev.innerHTML='<p>Source: '+esc(d.source)+' — all saved as <b>claimed</b>.</p>'+d.proposals.map((p,i)=>`<label><input type=checkbox checked value=${i}> ${esc(p)}</label><br>`).join('')+
 (d.rejected.length?'<p>Rejected:</p>'+d.rejected.map(x=>'<div>✗ '+esc(x[0])+' — '+esc(x[1])+'</div>').join(''):'')+d.notes.map(n=>'<div><i>'+esc(n)+'</i></div>').join('')}
async function commit(){const acc=[...prev.querySelectorAll('input:checked')].map(x=>+x.value);
 const [s,d]=await post('/api/ingest/commit',{text:paste.value,source:src(),accept:acc});
 prev.textContent=s==200?('Saved: '+d.saved.join(', ')):d.error;load()}
load();</script></body></html>"""


def make_server(home, port: int = DEFAULT_PORT, token: str = None) -> HTTPServer:
    home = Path(home)
    token = token or ensure_token(home)
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class H(BaseHTTPRequestHandler):
        server_version = "gridmem"

        def log_message(self, *a):
            pass

        def _send(self, code, obj, ctype="application/json"):
            data = obj.encode() if isinstance(obj, str) else json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'")
            self.end_headers()
            self.wfile.write(data)

        def _host_ok(self):
            port_now = self.server.server_address[1]
            return (self.headers.get("Host") or "") in (allowed_hosts | {f"127.0.0.1:{port_now}", f"localhost:{port_now}"})

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, {"error": "bad Host header"})
            u = urlparse(self.path)
            qs = {k: v[0] for k, v in parse_qs(u.query).items()}
            st = Store(home)
            try:
                if u.path == "/":
                    return self._send(200, PAGE, "text/html")
                if u.path == "/api/entries":
                    ents = st.search(qs.get("q", ""))
                    ents.sort(key=lambda e: e["seq"], reverse=True)
                    keep = ("id", "ts", "kind", "text", "tags", "source", "status", "supersedes")
                    return self._send(200, {"head": st.head()[1], "count": len(ents),
                                            "entries": [{k: e[k] for k in keep} for e in ents]})
                if u.path == "/api/briefing":
                    try:
                        mc = int(qs.get("max_chars") or 6000)
                        b = build_briefing(st, topic=qs.get("topic") or None, max_chars=mc,
                                           model=qs.get("model") or None)
                    except ValueError as e:
                        return self._send(400, {"error": str(e)})
                    return self._send(200, {"briefing": b, "chars": len(b)})
                return self._send(404, {"error": "not found"})
            finally:
                st.close()

        def do_POST(self):
            if not self._host_ok():
                return self._send(403, {"error": "bad Host header"})
            origin = self.headers.get("Origin")
            port_now = self.server.server_address[1]
            if origin and origin not in (f"http://127.0.0.1:{port_now}", f"http://localhost:{port_now}"):
                return self._send(403, {"error": "cross-origin write refused"})
            if not hmac.compare_digest((self.headers.get("X-GridMem-Token") or "").encode(), token.encode()):
                return self._send(401, {"error": "write token missing or wrong (see data/web_token)"})
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                return self._send(413, {"error": "too large"})
            try:
                req = json.loads(self.rfile.read(n) or b"{}")
                source = clean_text(req.get("source") or "unknown model", 120)
            except (json.JSONDecodeError, GridMemError, AttributeError):
                return self._send(400, {"error": "bad request"})
            st = Store(home)
            try:
                parsed = parse_reply(req.get("text") or "", known_ids=set(st.entries()))
                if self.path == "/api/ingest/preview":
                    return self._send(200, {"source": source, "proposals": [describe(p) for p in parsed["proposals"]],
                                            "rejected": parsed["rejected"], "notes": parsed["notes"]})
                if self.path == "/api/ingest/commit":
                    idx = req.get("accept")
                    props = parsed["proposals"]
                    if isinstance(idx, list):
                        props = [p for i, p in enumerate(props) if i in idx]
                    saved = apply(st, props, source)
                    return self._send(200, {"saved": [r["id"] for r in saved]})
                return self._send(404, {"error": "not found"})
            except GridMemError as e:
                return self._send(400, {"error": str(e)})
            finally:
                st.close()

    return HTTPServer(("127.0.0.1", port), H)


def serve(home, port: int = DEFAULT_PORT) -> None:
    token = ensure_token(Path(home))
    srv = make_server(home, port, token)
    print(f"Grid Memory UI on http://127.0.0.1:{port}/  (localhost only, Ctrl-C to stop)")
    print(f"Write token is in {Path(home) / 'web_token'} (mode 600). Open with it pre-filled:")
    print(f"  http://127.0.0.1:{port}/#token=<contents of that file>")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()

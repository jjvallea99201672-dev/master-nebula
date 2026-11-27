"""
ether.py — connect to the ether (four faces, one medium), hardened (v6.2).

What this actually does:
    * strict request validation (types, lengths, op allow-list, finite numbers)
    * bounded framing: MAX_PAYLOAD, plus a per-frame deadline (no slowloris)
    * per-connection idle timeout, per-server connection cap
    * rate limiting per peer address, per REQUEST (a rejected request gets an
      error frame before the connection is closed)
    * Akasha: bounded record count, ONE atomic durable write (records + field),
      fsync (disable with --no-fsync; it is the throughput ceiling), lock-scoped, advisory inter-process lock on the data file
    * deterministic disturb: sha256(thought) -> cell
    * resonate results capped so a reply can never exceed MAX_PAYLOAD
    * logging via `logging`, not print
    * graceful shutdown (signal handlers, server_close, serve thread joined;
      handler threads are daemons and are NOT joined)
    * TLS: min TLS 1.2, explicit TLS 1.2 cipher list (TLS 1.3 suites untouched), handshake in handler
      thread with timeout, system CAs loaded for clients without --cafile
    * no shell, no eval, no unpickling; JSON only
    * there is no authentication beyond optional client certificates
    * resonate scores overlap of whole lowercase words, not characters
    * noise perturbs only printable ASCII; non-ASCII is left intact
    * nursery coheres toward neighbours with jitter (spread stays nonzero);
      neighbour lists are cached and refreshed every few ticks
    * resonate uses a per-record word index built once at write/load time,
      capped at MAX_INDEX_WORDS per record and MAX_QUERY_WORDS per query
    * demo writes to a temporary store, not the server's akasha.json

Wire protocol (big-endian, both directions):
    magic(4)='ETHR' | len(4)=uint32 | payload(len bytes of UTF-8 JSON)

CLI:
    python ether.py serve --certfile cert.pem --keyfile key.pem
    python ether.py send "Sabaoth" --noise 0.2 --tls --cafile cert.pem --server-hostname localhost
    python ether.py send --query hosts --tls --cafile cert.pem --server-hostname localhost
    python ether.py demo
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import heapq
import math
import os
import random
import re
import signal
import socket
import socketserver
import ssl
import struct
import sys
import tempfile
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

try:  # advisory inter-process lock (POSIX only)
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

# ---------------------------------------------------------------
# Constants
# ---------------------------------------------------------------
MAGIC = b"ETHR"
HEADER = struct.Struct("!4sI")
MAX_PAYLOAD = 1 * 1024 * 1024
MAX_THOUGHT_LEN = 4 * 1024
MAX_QUERY_LEN = 4 * 1024
MAX_RECORDS = 100_000
MAX_INDEX_WORDS = 256                  # unique words indexed per record (bounds memory)
MAX_QUERY_WORDS = 64                   # unique words used per query (bounds CPU)
_WORD = re.compile(r"\w+", re.UNICODE)
MAX_HITS = 20                          # resonate results per reply (20 * ~4 KiB << MAX_PAYLOAD)
HANDSHAKE_TIMEOUT = 10.0
IDLE_TIMEOUT = 60.0                    # wait for the START of a frame
FRAME_TIMEOUT = 10.0                   # whole frame must complete within this once started
MAX_CONNECTIONS = 256
RATE_LIMIT_WINDOW = 10.0
RATE_LIMIT_MAX = 50
# OpenSSL cipher string applied via set_ciphers(). It governs TLS 1.2 (and
# below) ONLY. TLS 1.3 suites are configured separately in OpenSSL and are
# not affected by this list or by set_ciphers() in Python's ssl module.
TLS12_CIPHER_LIST = "ECDHE+AESGCM:ECDHE+CHACHA20"

log = logging.getLogger("ether")

# ---------------------------------------------------------------
# Errors
# ---------------------------------------------------------------
class FrameError(Exception):
    pass

class ValidationError(Exception):
    pass

# ---------------------------------------------------------------
# Framing (bounded, deadline-aware)
# ---------------------------------------------------------------
def send_frame(sock: socket.socket, obj: dict) -> None:
    payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_PAYLOAD:
        raise FrameError(f"payload {len(payload)} > {MAX_PAYLOAD}")
    sock.sendall(HEADER.pack(MAGIC, len(payload)) + payload)

def recv_exact(sock: socket.socket, n: int, deadline: float | None = None) -> bytes:
    """Read exactly n bytes. If deadline (time.monotonic) is given, the TOTAL
    time is bounded, not just each individual recv."""
    if n < 0 or n > MAX_PAYLOAD:
        raise FrameError(f"refusing to read {n} bytes")
    buf = bytearray(n)
    view = memoryview(buf)
    got = 0
    while got < n:
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("frame deadline exceeded")
            sock.settimeout(remaining)
        chunk = sock.recv_into(view[got:], n - got)
        if chunk == 0:
            raise ConnectionError("peer closed mid-frame")
        got += chunk
    return bytes(buf)

def recv_frame(sock: socket.socket, idle_timeout: float = IDLE_TIMEOUT,
               frame_timeout: float = FRAME_TIMEOUT) -> dict:
    # Wait up to idle_timeout for the first byte; after that the whole
    # frame must arrive within frame_timeout.
    sock.settimeout(idle_timeout)
    first = recv_exact(sock, 1)
    deadline = time.monotonic() + frame_timeout
    header = first + recv_exact(sock, HEADER.size - 1, deadline)
    magic, length = HEADER.unpack(header)
    if magic != MAGIC:
        raise FrameError(f"bad magic {magic!r}")
    if length > MAX_PAYLOAD:
        raise FrameError(f"declared length {length} > {MAX_PAYLOAD}")
    payload = recv_exact(sock, length, deadline)
    try:
        obj = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise FrameError(f"bad JSON: {e}") from e
    if not isinstance(obj, dict):
        raise FrameError("payload is not a JSON object")
    return obj

# ---------------------------------------------------------------
# 1. MEDIUM
# ---------------------------------------------------------------
class Medium:
    def __init__(self, size: int = 256):
        self.size = size
        self.field = [0.0] * size

    def disturb(self, at: int, amplitude: float = 1.0) -> None:
        self.field[at % self.size] += amplitude

    def step(self, damping: float = 0.98) -> None:
        f, n = self.field, self.size
        new = [0.0] * n
        for i in range(n):
            new[i] = damping * 0.5 * (f[(i - 1) % n] + f[(i + 1) % n])
        self.field = new

    def energy(self) -> float:
        return sum(abs(x) for x in self.field)

# ---------------------------------------------------------------
# 2. AKASHA (persistent, bounded) — persists Medium.field in the same write
# ---------------------------------------------------------------
def _words(text: str, cap: int) -> frozenset[str]:
    """Unique lowercase \\w+ tokens, first `cap` in order of appearance."""
    seen: dict[str, None] = {}
    for w in _WORD.findall(text.lower()):
        if w not in seen:
            seen[w] = None
            if len(seen) >= cap:
                break
    return frozenset(seen)

@dataclass(frozen=True)
class Record:
    impression: str
    digest: str
    when: float
    # Search index, computed once; not persisted, not compared, not printed.
    words: frozenset = field(default=frozenset(), compare=False, repr=False)

def _make_record(impression: str, digest: str, when: float) -> Record:
    return Record(impression, digest, when, _words(impression, MAX_INDEX_WORDS))

def _is_real(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)

class Akasha:
    def __init__(self, path: str | Path = "akasha.json", max_records: int = MAX_RECORDS,
                 fsync: bool = True):
        self.path = Path(path)
        self.max_records = max_records
        # PERFORMANCE CEILING: every impress()/save_field() serializes ALL
        # records, writes a temp file, and (if fsync) waits for the disk.
        # Cost per transmit is O(records) CPU + one fsync (~ms on SSD, tens
        # of ms on spinning disks/network storage), all under a global lock,
        # so throughput is roughly 1 / (serialize + fsync) requests/sec
        # regardless of thread count. fsync=False (--no-fsync) removes the
        # disk wait but a power loss can lose or truncate recent writes
        # (a process crash is still safe: os.replace stays atomic).
        self.fsync = fsync
        self._lock = threading.RLock()
        self.records: list[Record] = []
        self.field: list[float] = []  # last persisted Medium.field, [] = none yet
        self._lockfile = None
        self._acquire_process_lock()
        self._load()

    def _acquire_process_lock(self) -> None:
        if fcntl is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(self.path.name + ".lock")
        self._lockfile = open(lock_path, "w")
        try:
            fcntl.flock(self._lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lockfile.close()
            self._lockfile = None
            raise RuntimeError(f"{self.path} is in use by another process")

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("top-level JSON is not an object")
            recs = []
            raw_recs = data.get("records", [])
            if not isinstance(raw_recs, list):
                raise ValueError("records is not a list")
            for r in raw_recs:
                if not isinstance(r, dict):
                    continue
                imp, dig, when = r.get("impression"), r.get("digest"), r.get("when")
                if isinstance(imp, str) and isinstance(dig, str) and _is_real(when):
                    recs.append(_make_record(imp, dig, float(when)))
            # Keep the NEWEST records, same policy as impress().
            self.records = recs[-self.max_records:]
            f = data.get("field", [])
            if isinstance(f, list) and all(_is_real(x) for x in f):
                self.field = [float(x) for x in f]
            log.info("akasha loaded: %d records, field=%d cells", len(self.records), len(self.field))
        except (ValueError, OSError) as e:  # ValueError covers JSON + Unicode errors
            backup = self.path.with_suffix(f".corrupt.{int(time.time())}")
            try:
                self.path.rename(backup)
            except OSError:
                pass
            log.warning("akasha corrupt, moved to %s: %s", backup, e)
            self.records = []
            self.field = []

    def _save_locked(self) -> None:
        payload = {
            "records": [{"impression": r.impression, "digest": r.digest, "when": r.when}
                        for r in self.records],
            "field": self.field,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".akasha.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
                f.flush()
                if self.fsync:
                    os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def save_field(self, field: list[float]) -> None:
        with self._lock:
            self.field = [float(x) for x in field]
            self._save_locked()

    def impress(self, thought: str, field: list[float] | None = None) -> Record:
        """Append a record and (optionally) the field in ONE atomic write."""
        digest = hashlib.sha256(thought.encode("utf-8")).hexdigest()[:16]
        rec = _make_record(thought, digest, time.time())
        with self._lock:
            self.records.append(rec)
            if len(self.records) > self.max_records:
                self.records = self.records[-self.max_records:]
            if field is not None:
                self.field = [float(x) for x in field]
            self._save_locked()
        return rec

    def resonate(self, query: str, limit: int = MAX_HITS) -> list[Record]:
        # Score = fraction of query words present in the record (whole
        # lowercase \\w+ tokens). Records carry a prebuilt word set, so a
        # query is one set intersection per record, no per-query regex.
        q = _words(query, MAX_QUERY_WORDS)
        if not q:
            return []
        limit = max(1, min(limit, MAX_HITS))
        with self._lock:
            snapshot = list(self.records)
        nq = len(q)
        scored = []
        for r in snapshot:
            hit = len(q & r.words)
            if hit:
                scored.append((hit / nq, r))
        # nlargest is stable: ties keep insertion (oldest-first) order.
        return [r for _, r in heapq.nlargest(limit, scored, key=lambda t: t[0])]

    def count(self) -> int:
        with self._lock:
            return len(self.records)

# ---------------------------------------------------------------
# 3. WIRE
# ---------------------------------------------------------------
class RateLimiter:
    def __init__(self, window: float = RATE_LIMIT_WINDOW, limit: int = RATE_LIMIT_MAX):
        self.window = window
        self.limit = limit
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True

    def prune(self) -> None:
        now = time.monotonic()
        with self._lock:
            empty = []
            for k, q in self._hits.items():
                while q and now - q[0] > self.window:
                    q.popleft()
                if not q:
                    empty.append(k)
            for k in empty:
                del self._hits[k]

class WireHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        server: EtherServer = self.server           # type: ignore[assignment]
        ether = server.ether
        if ether is None:
            return
        peer = self.client_address[0] if self.client_address else "?"

        sock: socket.socket = self.request
        if server.tls_ctx is not None:
            sock.settimeout(HANDSHAKE_TIMEOUT)
            try:
                sock = server.tls_ctx.wrap_socket(sock, server_side=True)
            except (ssl.SSLError, OSError) as e:
                log.info("tls handshake failed from %s: %s", peer, e)
                return

        try:
            while True:
                try:
                    req = recv_frame(sock)
                except (ConnectionError, FrameError, OSError, ssl.SSLError) as e:
                    log.debug("recv from %s ended: %s", peer, e)
                    return
                # Charge the limiter only once a real request has arrived,
                # so idle/closing connections don't burn tokens.
                if not server.limiter.allow(peer):
                    log.warning("rate-limited %s", peer)
                    try:
                        send_frame(sock, {"ok": False, "error": "rate limited"})
                    except (OSError, FrameError, ssl.SSLError):
                        pass
                    return
                try:
                    resp = ether.handle_request(req)
                except ValidationError as e:
                    resp = {"ok": False, "error": f"invalid request: {e}"}
                except Exception:
                    log.exception("handler error for %s", peer)
                    resp = {"ok": False, "error": "internal error"}
                try:
                    send_frame(sock, resp)
                except (OSError, FrameError, ssl.SSLError) as e:
                    log.debug("send to %s failed: %s", peer, e)
                    return
        finally:
            try:
                sock.close()
            except OSError:
                pass

class EtherServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 64

    def __init__(self, addr, handler, *, tls_ctx: ssl.SSLContext | None = None):
        super().__init__(addr, handler)
        self.ether: Ether | None = None
        self.tls_ctx = tls_ctx
        self.limiter = RateLimiter()
        self._sem = threading.BoundedSemaphore(MAX_CONNECTIONS)

    def process_request(self, request, client_address):
        if not self._sem.acquire(blocking=False):
            log.warning("connection cap reached, rejecting %s", client_address)
            try:
                request.close()
            except OSError:
                pass
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._sem.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._sem.release()

def make_server_tls(certfile: str, keyfile: str,
                    cafile: str | None = None,
                    require_client_cert: bool = False) -> ssl.SSLContext:
    for p in (certfile, keyfile):
        if not Path(p).is_file():
            raise FileNotFoundError(p)
    if require_client_cert and not cafile:
        raise ValueError("--require-client-cert needs --cafile to verify clients against")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.options |= ssl.OP_NO_COMPRESSION
    ctx.set_ciphers(TLS12_CIPHER_LIST)  # TLS 1.2 only; does not constrain TLS 1.3
    ctx.load_cert_chain(certfile=certfile, keyfile=keyfile)
    if cafile:
        if not Path(cafile).is_file():
            raise FileNotFoundError(cafile)
        ctx.load_verify_locations(cafile)
    if require_client_cert:
        ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx

class WireClient:
    def __init__(self, host="127.0.0.1", port=7777, timeout=10.0,
                 tls: ssl.SSLContext | None = None,
                 server_hostname: str | None = None):
        self.host, self.port, self.timeout = host, port, timeout
        self.tls = tls
        self.server_hostname = server_hostname or host

    def _connect(self) -> socket.socket:
        raw = socket.create_connection((self.host, self.port), timeout=self.timeout)
        try:
            if self.tls is not None:
                raw = self.tls.wrap_socket(raw, server_hostname=self.server_hostname)
            return raw
        except BaseException:
            try:
                raw.close()
            except OSError:
                pass
            raise

    def _call(self, payload: dict) -> dict:
        with self._connect() as s:
            s.settimeout(self.timeout)
            send_frame(s, payload)
            return recv_frame(s, idle_timeout=self.timeout, frame_timeout=self.timeout)

    def transmit(self, thought: str, noise: float = 0.0) -> dict:
        return self._call({"op": "transmit", "thought": thought, "noise": noise})

    def resonate(self, query: str, limit: int = MAX_HITS) -> dict:
        return self._call({"op": "resonate", "query": query, "limit": limit})

    def state(self) -> dict:
        return self._call({"op": "state"})

def make_client_tls(cafile: str | None, require: bool = True) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    if cafile:
        if not Path(cafile).is_file():
            raise FileNotFoundError(cafile)
        ctx.load_verify_locations(cafile)
    elif require:
        ctx.load_default_certs()  # fall back to the system trust store
    else:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx

# ---------------------------------------------------------------
# 4. NURSERY
# ---------------------------------------------------------------
@dataclass
class Particle:
    x: float
    y: float

class Nursery:
    """Particles drift toward their nearest neighbours with random jitter.

    Neighbour lists are cached and recomputed every REFRESH_EVERY calls
    (positions move slowly), which keeps a tick cheap even though a full
    neighbour search is O(n^2).
    """
    NEIGHBOURS = 3
    JITTER = 0.03
    REFRESH_EVERY = 8
    BOUND = 2.0

    def __init__(self, n: int = 200, seed: int | None = None):
        self.rng = random.Random(seed)
        self.particles = [
            Particle(self.rng.uniform(-1, 1), self.rng.uniform(-1, 1))
            for _ in range(n)
        ]
        self._neigh: list[list[int]] = []
        self._age = 0

    def _refresh_neighbours(self, k: int) -> None:
        xs = [p.x for p in self.particles]
        ys = [p.y for p in self.particles]
        n = len(xs)
        neigh = []
        for i in range(n):
            xi, yi = xs[i], ys[i]
            neigh.append(heapq.nsmallest(
                k, (j for j in range(n) if j != i),
                key=lambda j: (xs[j] - xi) ** 2 + (ys[j] - yi) ** 2,
            ))
        self._neigh = neigh

    def cohere(self, pull: float = 0.1) -> None:
        """One tick: attract each particle toward the centroid of its k
        nearest neighbours by `pull`, plus jitter. No global centroid pull,
        so clusters form and spread settles at a nonzero floor."""
        pts = self.particles
        n = len(pts)
        if not n:
            return
        k = min(self.NEIGHBOURS, n - 1)
        if k > 0 and (len(self._neigh) != n or self._age % self.REFRESH_EVERY == 0):
            self._refresh_neighbours(k)
        self._age += 1
        xs = [p.x for p in pts]
        ys = [p.y for p in pts]
        jit, bound, uni = self.JITTER, self.BOUND, self.rng.uniform
        for i, p in enumerate(pts):
            nx, ny = xs[i], ys[i]
            if k > 0:
                idx = self._neigh[i]
                nx += pull * (sum(xs[j] for j in idx) / k - nx)
                ny += pull * (sum(ys[j] for j in idx) / k - ny)
            nx += uni(-jit, jit)
            ny += uni(-jit, jit)
            p.x = max(-bound, min(bound, nx))
            p.y = max(-bound, min(bound, ny))

    def spread(self) -> float:
        n = len(self.particles)
        if not n:
            return 0.0
        cx = sum(p.x for p in self.particles) / n
        cy = sum(p.y for p in self.particles) / n
        return math.sqrt(
            sum((p.x - cx) ** 2 + (p.y - cy) ** 2 for p in self.particles) / n
        )

# ---------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------
def _require_str(req: dict, key: str, *, max_len: int, allow_empty: bool = False) -> str:
    v = req.get(key)
    if not isinstance(v, str):
        raise ValidationError(f"{key} must be a string")
    if not allow_empty and not v:
        raise ValidationError(f"{key} must not be empty")
    if len(v) > max_len:
        raise ValidationError(f"{key} exceeds {max_len} chars")
    return v

def _require_noise(req: dict) -> float:
    v = req.get("noise", 0.0)
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        raise ValidationError("noise must be a number")
    v = float(v)
    if not math.isfinite(v) or not (0.0 <= v <= 1.0):
        raise ValidationError("noise must be in [0.0, 1.0]")
    return v

def _optional_limit(req: dict) -> int:
    v = req.get("limit", MAX_HITS)
    if not isinstance(v, int) or isinstance(v, bool):
        raise ValidationError("limit must be an integer")
    if not (1 <= v <= MAX_HITS):
        raise ValidationError(f"limit must be in [1, {MAX_HITS}]")
    return v

def _thought_cell(thought: str, size: int) -> int:
    return int(hashlib.sha256(thought.encode("utf-8")).hexdigest(), 16) % size

# ---------------------------------------------------------------
# THE CONNECTION
# ---------------------------------------------------------------
class Ether:
    def __init__(self, seed: int | None = None, akasha_path: str = "akasha.json",
                 fsync: bool = True):
        self.medium = Medium()
        self.akasha = Akasha(akasha_path, fsync=fsync)
        if self.akasha.field and len(self.akasha.field) == self.medium.size:
            self.medium.field = list(self.akasha.field)
        self.nursery = Nursery(seed=seed)
        self._rng = random.Random(seed)
        # Guards medium + nursery mutation AND the persist that follows, so
        # snapshots reach disk in the same order they were produced.
        self._lock = threading.Lock()

    def _noisy(self, signal: str, noise: float) -> tuple[str, float]:
        # Printable ASCII is 0x20..0x7E. A noise hit on those characters
        # shifts within that range so the result stays visible text and
        # never becomes a control character. Non-ASCII is left as-is:
        # wrapping it through a short ASCII range would mangle it.
        lo, hi = 0x20, 0x7E
        span = hi - lo + 1  # 95
        out = []
        for ch in signal:
            if self._rng.random() < noise:
                code = ord(ch)
                if lo <= code <= hi:
                    shift = self._rng.randint(1, span - 1)
                    code = lo + (code - lo + shift) % span
                    out.append(chr(code))
                else:
                    out.append(ch)
            else:
                out.append(ch)
        received = "".join(out)
        fidelity = sum(a == b for a, b in zip(signal, received)) / max(len(signal), 1)
        return received, fidelity

    def access(self, thought: str, noise: float = 0.0) -> dict:
        with self._lock:
            self.medium.disturb(_thought_cell(thought, self.medium.size))
            received, fidelity = self._noisy(thought, noise)
            self.nursery.cohere()
            # One atomic write: record + field together.
            rec = self.akasha.impress(thought, field=self.medium.field)
            return {
                "impression": rec.digest,
                "received": received,
                "fidelity": round(fidelity, 3),
                "field_energy": round(self.medium.energy(), 3),
                "nebula_spread": round(self.nursery.spread(), 3),
            }

    def drift(self, ticks: int = 5) -> None:
        with self._lock:
            for _ in range(ticks):
                self.medium.step()
                self.nursery.cohere()
            self.akasha.save_field(self.medium.field)

    def handle_request(self, req: dict) -> dict:
        op = req.get("op")
        if not isinstance(op, str):
            raise ValidationError("op must be a string")
        if op == "transmit":
            thought = _require_str(req, "thought", max_len=MAX_THOUGHT_LEN)
            noise = _require_noise(req)
            return {"ok": True, **self.access(thought, noise)}
        if op == "resonate":
            query = _require_str(req, "query", max_len=MAX_QUERY_LEN)
            limit = _optional_limit(req)
            hits = [
                {"digest": r.digest, "impression": r.impression, "when": r.when}
                for r in self.akasha.resonate(query, limit)
            ]
            return {"ok": True, "hits": hits}
        if op == "state":
            with self._lock:
                return {
                    "ok": True,
                    "records": self.akasha.count(),
                    "field_energy": round(self.medium.energy(), 3),
                    "nebula_spread": round(self.nursery.spread(), 3),
                }
        raise ValidationError(f"unknown op {op!r}")

# ---------------------------------------------------------------
# CLI
# ---------------------------------------------------------------
def _setup_logging(verbosity: int) -> None:
    level = logging.WARNING if verbosity == 0 else logging.INFO if verbosity == 1 else logging.DEBUG
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

def _serve(args) -> int:
    if args.certfile or args.keyfile:
        if not (args.certfile and args.keyfile):
            log.error("both --certfile and --keyfile required for TLS")
            return 2
    elif args.cafile or args.require_client_cert:
        log.error("--cafile/--require-client-cert only make sense with TLS")
        return 2
    tls_ctx = None
    if args.certfile:
        tls_ctx = make_server_tls(
            args.certfile, args.keyfile,
            cafile=args.cafile,
            require_client_cert=args.require_client_cert,
        )
        log.info("TLS enabled (min %s)", tls_ctx.minimum_version.name)
    ether = Ether(seed=args.seed, akasha_path=args.akasha, fsync=not args.no_fsync)
    server = EtherServer((args.host, args.port), WireHandler, tls_ctx=tls_ctx)
    server.ether = ether
    scheme = "tls" if tls_ctx else "tcp"
    log.warning("listening %s://%s:%d, akasha=%s", scheme, args.host, args.port, args.akasha)

    stop = threading.Event()

    def _shutdown(signum, _frame):
        log.warning("signal %s received, shutting down", signum)
        stop.set()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    t = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True)
    t.start()
    try:
        while not stop.is_set():
            stop.wait(1.0)
            server.limiter.prune()
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=5.0)
        log.warning("ether dissipated")
    return 0

def _send(args) -> int:
    if args.insecure and not args.tls:
        log.error("--insecure requires --tls")
        return 2
    tls_ctx = None
    if args.tls:
        tls_ctx = make_client_tls(args.cafile, require=not args.insecure)
    client = WireClient(args.host, args.port, tls=tls_ctx,
                        server_hostname=args.server_hostname)
    try:
        if args.query is not None:
            out = client.resonate(args.query)
        elif args.thought is None:
            out = client.state()
        else:
            out = client.transmit(args.thought, noise=args.noise)
    except (OSError, FrameError, ssl.SSLError, json.JSONDecodeError) as e:
        log.error("wire error: %s", e)
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0

def _demo(args) -> int:
    # Fresh temp store so demo never touches the server's akasha.json
    # (or its .lock). Close the advisory lock before deleting the dir.
    tmpdir = tempfile.TemporaryDirectory(prefix="ether_demo_")
    ether = None
    try:
        path = str(Path(tmpdir.name) / "demo_akasha.json")
        ether = Ether(seed=42, akasha_path=path, fsync=False)  # throwaway store
        for thought in ["Sabaoth", "Lord of Hosts", "the unformed many", "light"]:
            echo = ether.access(thought)
            print(f"> {thought!r}")
            for k, v in echo.items():
                print(f"    {k:14} {v}")
            ether.drift()
        print("\nresonance with 'hosts':")
        for r in ether.akasha.resonate("hosts"):
            print(f"    {r.digest}  {r.impression!r}")
        return 0
    finally:
        if ether is not None:
            lf = ether.akasha._lockfile
            if lf is not None:
                try:
                    lf.close()
                except OSError:
                    pass
                ether.akasha._lockfile = None
        tmpdir.cleanup()

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="connect to the ether")
    p.add_argument("--akasha", default="akasha.json")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=7777)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("-v", "--verbose", action="count", default=0)
    sub = p.add_subparsers(dest="cmd")

    ps = sub.add_parser("serve")
    ps.add_argument("--certfile")
    ps.add_argument("--keyfile")
    ps.add_argument("--cafile")
    ps.add_argument("--require-client-cert", action="store_true")
    ps.add_argument("--no-fsync", action="store_true",
                    help="skip fsync on writes: faster, but a power loss may lose recent data")
    ps.set_defaults(func=_serve)

    pc = sub.add_parser("send")
    pc.add_argument("thought", nargs="?")
    pc.add_argument("--noise", type=float, default=0.0)
    pc.add_argument("--query")
    pc.add_argument("--tls", action="store_true")
    pc.add_argument("--cafile")
    pc.add_argument("--server-hostname", default=None)
    pc.add_argument("--insecure", action="store_true")
    pc.set_defaults(func=_send)

    pd = sub.add_parser("demo")
    pd.set_defaults(func=_demo)

    args = p.parse_args(argv)
    _setup_logging(args.verbose)
    if not getattr(args, "func", None):
        args.func = _demo
    try:
        return args.func(args)
    except FileNotFoundError as e:
        log.error("missing file: %s", e)
        return 2
    except (ValidationError, ValueError, RuntimeError, ssl.SSLError) as e:
        log.error("%s", e)
        return 2

if __name__ == "__main__":
    sys.exit(main())

"""Append-only, hash-chained, signed SQLite log of memory events.

Every row is an *event*. Memory entries are `op=entry` rows. Evidence, operator
confirmations and model agreement are separate rows that point at an entry, so
nothing is ever edited or deleted. The status you see is computed by replaying
the log (see `Store.entries`).

Tamper-EVIDENT, not tamper-PROOF: whoever holds the hub's private key could
rewrite the whole chain. `check` detects edits, deletions, reordering and
re-hashing without the key; keep a copy of the head hash somewhere else
(e.g. a briefing you pasted) to detect truncation of the newest rows.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import OPERATOR, VERSION
from .keeper import MerkleTree, PQCSigner, canon, pqc_available, sha256_hex

ZERO_HASH = "0" * 64
KINDS = ("fact", "decision", "correction", "retraction", "note", "question")
OPS = ("entry", "evidence", "confirm", "agree")
# Computed statuses, strongest first in briefings.
STATUSES = ("verified", "operator-confirmed", "claimed", "check-failed", "superseded", "retracted")
MAX_TEXT = 4000
ID_RE = re.compile(r"^GM-\d{4,}$")
TAG_RE = re.compile(r"[^a-z0-9._-]+")


class GridMemError(Exception):
    pass


def default_home() -> Path:
    return Path(os.environ.get("GRIDMEM_HOME", str(Path(__file__).resolve().parent.parent / "data")))


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clean_text(s: str, limit: int = MAX_TEXT) -> str:
    s = "".join(ch for ch in str(s) if ch in "\n\t" or (ord(ch) >= 32 and ord(ch) != 127))
    s = s.strip()
    if not s:
        raise GridMemError("text is empty")
    return s[:limit]


def clean_tags(tags) -> List[str]:
    if tags is None:
        return []
    if isinstance(tags, str):
        tags = tags.split(",")
    out = []
    for t in tags:
        t = TAG_RE.sub("-", str(t).strip().lower()).strip("-")[:40]
        if t and t not in out:
            out.append(t)
    return out[:12]


def event_hash(prev_hash: str, row: Dict[str, Any]) -> str:
    body = {k: row[k] for k in ("seq", "id", "ts", "op", "target", "body")}
    return sha256_hex(prev_hash.encode() + canon(body))


class Store:
    def __init__(self, home: Optional[os.PathLike] = None, sig_alg: Optional[str] = None):
        self.home = Path(home) if home else default_home()
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.home, 0o700)
        self.db_path = self.home / "grid.db"
        self.identity_path = self.home / "hub_identity.json"
        alg = sig_alg or os.environ.get("GRIDMEM_SIG_ALG")
        if not alg and self.identity_path.exists():
            alg = json.loads(self.identity_path.read_text())["alg"]
        if not alg:
            alg = "ed25519+ml-dsa-44" if pqc_available() else "ed25519"
        self.signer = PQCSigner(alg, str(self.home / "keys"))
        self._init_identity()
        self.conn = sqlite3.connect(self.db_path, isolation_level=None, timeout=10, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL, ts TEXT NOT NULL,
                op TEXT NOT NULL, target TEXT, body TEXT NOT NULL,
                prev_hash TEXT NOT NULL, entry_hash TEXT NOT NULL, signature TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
        """)
        self.conn.execute("INSERT OR IGNORE INTO meta VALUES ('format', ?)", (f"gridmem/{VERSION}",))

    # ---------- identity ----------
    def _init_identity(self) -> None:
        ident = {"alg": self.signer.alg, "algorithm": self.signer.algorithm,
                 "public_key": self.signer.public_key, "key_id": self.signer.key_id,
                 "operator": OPERATOR}
        if self.identity_path.exists():
            old = json.loads(self.identity_path.read_text())
            if old.get("public_key") != ident["public_key"]:
                raise GridMemError("hub key does not match hub_identity.json (keys replaced?)")
        else:
            self.identity_path.write_text(json.dumps(ident, indent=2))
        self.identity = ident

    def close(self) -> None:
        self.conn.close()

    # ---------- low-level append ----------
    def head(self):
        r = self.conn.execute("SELECT seq, entry_hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        return (r[0], r[1]) if r else (0, ZERO_HASH)

    def _append(self, op: str, body: Dict[str, Any], target: Optional[str] = None) -> Dict[str, Any]:
        if op not in OPS:
            raise GridMemError(f"unknown op {op}")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            seq, prev = self.head()
            row = {"seq": seq + 1, "id": f"GM-{seq + 1:04d}", "ts": utc_now(), "op": op,
                   "target": target, "body": json.loads(json.dumps(body))}
            h = event_hash(prev, row)
            sig = self.signer.sign(h.encode())
            self.conn.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?)",
                              (row["seq"], row["id"], row["ts"], op, target,
                               json.dumps(row["body"], sort_keys=True), prev, h, sig))
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        row.update(prev_hash=prev, entry_hash=h)
        return row

    def events(self) -> List[Dict[str, Any]]:
        out = []
        for seq, id_, ts, op, target, body, prev, h, sig in self.conn.execute(
                "SELECT seq,id,ts,op,target,body,prev_hash,entry_hash,signature FROM events ORDER BY seq"):
            out.append({"seq": seq, "id": id_, "ts": ts, "op": op, "target": target,
                        "body": json.loads(body), "prev_hash": prev, "entry_hash": h, "signature": sig})
        return out

    # ---------- public write API ----------
    def add(self, kind: str, text: str, tags=None, source: str = "Jay", supersedes: Optional[str] = None,
            references: Optional[List[Dict]] = None, status_note: str = "") -> Dict[str, Any]:
        if kind not in KINDS:
            raise GridMemError(f"kind must be one of {KINDS}")
        if kind in ("correction", "retraction") and not supersedes:
            raise GridMemError(f"a {kind} must name the entry it supersedes")
        if supersedes:
            self._require_entry(supersedes)
        body = {"kind": kind, "text": clean_text(text), "tags": clean_tags(tags),
                "source": clean_text(source, 120), "status": "claimed", "supersedes": supersedes,
                "references": references or [], "status_note": clean_text(status_note, 300) if status_note else ""}
        return self._append("entry", body)

    def correct(self, target: str, text: str, source: str = "Jay", tags=None) -> Dict[str, Any]:
        old = self._require_entry(target)
        return self.add("correction", text, tags if tags is not None else old["tags"], source, supersedes=target)

    def retract(self, target: str, reason: str, source: str = "Jay") -> Dict[str, Any]:
        old = self._require_entry(target)
        return self.add("retraction", reason, old["tags"], source, supersedes=target)

    def attach_evidence(self, target: str, evidence: Dict[str, Any]) -> Dict[str, Any]:
        """`evidence` must come from gridmem.evidence (it carries the recomputed result)."""
        self._require_entry(target)
        if evidence.get("result") not in ("pass", "fail"):
            raise GridMemError("evidence must carry a recomputed result")
        return self._append("evidence", {"evidence": evidence, "result": evidence["result"]}, target)

    def operator_confirm(self, target: str, note: str = "", by: str = OPERATOR["operator_id"]) -> Dict[str, Any]:
        self._require_entry(target)
        return self._append("confirm", {"by": by, "note": clean_text(note, 500) if note else "",
                                        "label": "operator-confirmed (Jay's word, no machine check)"}, target)

    def record_agreement(self, target: str, model: str, note: str = "") -> Dict[str, Any]:
        """Model agreement is recorded for context only. It NEVER changes status."""
        self._require_entry(target)
        return self._append("agree", {"model": clean_text(model, 80), "note": clean_text(note, 500) if note else "",
                                      "label": "agreement is not evidence"}, target)

    # ---------- views ----------
    def _require_entry(self, id_: str) -> Dict[str, Any]:
        e = self.entries(include_all=True).get(id_)
        if not e:
            raise GridMemError(f"no memory entry {id_}")
        return e

    def entries(self, include_all: bool = True) -> Dict[str, Dict[str, Any]]:
        ev = self.events()
        ents: Dict[str, Dict[str, Any]] = {}
        for r in ev:
            if r["op"] == "entry":
                b = r["body"]
                ents[r["id"]] = {"id": r["id"], "seq": r["seq"], "ts": r["ts"], **b,
                                 "stored_status": b.get("status", "claimed"),
                                 "evidence": [], "confirmations": [], "agreed_by": [],
                                 "superseded_by": [], "retracted_by": None, "entry_hash": r["entry_hash"]}
        for r in ev:
            t = ents.get(r["target"]) if r["target"] else None
            if r["op"] == "entry" and r["body"].get("supersedes") in ents:
                old = ents[r["body"]["supersedes"]]
                if r["body"]["kind"] == "retraction":
                    old["retracted_by"] = r["id"]
                else:
                    old["superseded_by"].append(r["id"])
            elif t is None:
                continue
            elif r["op"] == "evidence":
                t["evidence"].append({"event": r["id"], "ts": r["ts"], **r["body"]["evidence"]})
            elif r["op"] == "confirm":
                t["confirmations"].append({"event": r["id"], "ts": r["ts"], **r["body"]})
            elif r["op"] == "agree":
                t["agreed_by"].append({"event": r["id"], "ts": r["ts"], **r["body"]})
        for e in ents.values():
            e["status"] = compute_status(e)
        return ents

    def search(self, q: str = "", status: Optional[str] = None, kind: Optional[str] = None,
               tag: Optional[str] = None) -> List[Dict[str, Any]]:
        q = (q or "").lower()
        out = []
        for e in self.entries().values():
            hay = " ".join([e["id"], e["text"], e["source"], " ".join(e["tags"])]).lower()
            if q and q not in hay:
                continue
            if status and e["status"] != status:
                continue
            if kind and e["kind"] != kind:
                continue
            if tag and tag.lower() not in e["tags"]:
                continue
            out.append(e)
        return out

    def history(self, id_: str) -> List[Dict[str, Any]]:
        """All events touching an entry, plus the full supersession chain both ways."""
        ents = self.entries()
        if id_ not in ents:
            raise GridMemError(f"no memory entry {id_}")
        family = {id_}
        changed = True
        while changed:
            changed = False
            for e in ents.values():
                if e.get("supersedes") in family and e["id"] not in family:
                    family.add(e["id"]); changed = True
                if e["id"] in family and e.get("supersedes") and e["supersedes"] not in family:
                    family.add(e["supersedes"]); changed = True
        return [r for r in self.events() if r["id"] in family or r["target"] in family]

    # ---------- integrity ----------
    def check(self) -> Dict[str, Any]:
        ident = json.loads(self.identity_path.read_text())
        alg, pk = ident["algorithm"], ident["public_key"]
        prev, expected, tree = ZERO_HASH, 1, MerkleTree()
        for r in self.events():
            where = {"bad_seq": r["seq"], "rows_checked": expected - 1}
            if r["seq"] != expected:
                return {"ok": False, **where, "reason": f"sequence gap: expected {expected} (row deleted or reordered)"}
            if r["id"] != f"GM-{r['seq']:04d}":
                return {"ok": False, **where, "reason": "id does not match sequence"}
            if r["prev_hash"] != prev:
                return {"ok": False, **where, "reason": "prev_hash does not link to previous row"}
            if event_hash(prev, r) != r["entry_hash"]:
                return {"ok": False, **where, "reason": "row contents do not match entry_hash (row edited)"}
            if not PQCSigner.verify(alg, pk, r["entry_hash"].encode(), r["signature"]):
                return {"ok": False, **where, "reason": "signature invalid (row re-hashed without the hub key)"}
            tree.append(bytes.fromhex(r["entry_hash"]))
            prev, expected = r["entry_hash"], expected + 1
        return {"ok": True, "rows_checked": expected - 1, "head": prev,
                "merkle_root_rfc6962": tree.root() if tree.size else None,
                "signature_algorithm": alg, "key_id": ident["key_id"],
                "note": "Detects edits, deletions, reordering and re-hashing without the key. "
                        "Truncating the newest rows is only detectable against a head hash saved elsewhere."}


def compute_status(e: Dict[str, Any]) -> str:
    if e.get("retracted_by"):
        return "retracted"
    if e.get("superseded_by"):
        return "superseded"
    if e.get("evidence"):
        return "verified" if e["evidence"][-1]["result"] == "pass" else "check-failed"
    if e.get("confirmations"):
        return "operator-confirmed"
    return "claimed"   # agreed_by is deliberately ignored here

"""Parse ONLY the ```gridmem block from a pasted model reply.

Prompt-injection posture: everything outside the block is ignored; inside the
block only ADD / CORRECT / QUESTION are understood; nothing is executed,
nothing is auto-verified, and every accepted line becomes a 'claimed' entry
whose source is the model. Anything else is reported as rejected.
"""
from __future__ import annotations

import re
from typing import Dict, List

from .store import ID_RE, Store, clean_tags

FENCE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})[ \t]*gridmem[ \t]*$(.*?)^[ \t]*\1[ \t]*$", re.M | re.S | re.I)
ALLOWED_ADD_KINDS = ("fact", "decision", "note", "question")
MAX_LINES = 40
MAX_TEXT = 1000


def extract_blocks(reply: str) -> List[str]:
    return [m.group(2) for m in FENCE_RE.finditer(reply or "")]


def parse_reply(reply: str, known_ids=None) -> Dict:
    """Returns {"proposals": [...], "rejected": [(line, reason)], "notes": [...]}.
    Uses the LAST gridmem block (the reply is told to end with it)."""
    blocks = extract_blocks(reply)
    res = {"proposals": [], "rejected": [], "notes": []}
    if not blocks:
        res["notes"].append("no ```gridmem block found; nothing to ingest")
        return res
    if len(blocks) > 1:
        res["notes"].append(f"{len(blocks)} gridmem blocks found; only the last one is used")
    lines = [ln.strip() for ln in blocks[-1].splitlines() if ln.strip()]
    if len(lines) > MAX_LINES:
        res["notes"].append(f"block has {len(lines)} lines; only the first {MAX_LINES} are considered")
        lines = lines[:MAX_LINES]
    for ln in lines:
        p, why = _parse_line(ln, known_ids)
        if p:
            res["proposals"].append(p)
        else:
            res["rejected"].append((ln[:200], why))
    return res


def _text(s: str):
    s = "".join(ch for ch in s if ord(ch) >= 32 and ord(ch) != 127).strip()
    if not s:
        return None
    return s[:MAX_TEXT]


TEMPLATE_LINES = {"ADD|kind|tags|text", "CORRECT|<id>|text", "QUESTION|text"}


def _parse_line(ln: str, known_ids):
    if ln in TEMPLATE_LINES:
        return None, "template line copied from the briefing, not a proposal"
    parts = ln.split("|")
    op = parts[0].strip().upper()
    if op == "ADD":
        if len(parts) < 4:
            return None, "ADD needs ADD|kind|tags|text"
        kind = parts[1].strip().lower()
        if kind not in ALLOWED_ADD_KINDS:
            return None, f"kind '{kind[:30]}' not allowed (use fact, decision, note or question)"
        text = _text("|".join(parts[3:]))
        if not text:
            return None, "empty text"
        return {"op": "ADD", "kind": kind, "tags": clean_tags(parts[2]), "text": text}, None
    if op == "CORRECT":
        if len(parts) < 3:
            return None, "CORRECT needs CORRECT|<id>|text"
        target = parts[1].strip().upper()
        if not ID_RE.match(target):
            return None, f"'{target[:30]}' is not an entry id"
        if known_ids is not None and target not in known_ids:
            return None, f"{target} is not a memory entry"
        text = _text("|".join(parts[2:]))
        if not text:
            return None, "empty text"
        return {"op": "CORRECT", "target": target, "text": text}, None
    if op == "QUESTION":
        text = _text("|".join(parts[1:]))
        if not text:
            return None, "empty question"
        return {"op": "QUESTION", "kind": "question", "tags": [], "text": text}, None
    return None, f"operation '{op[:30]}' is not allowed (only ADD, CORRECT, QUESTION)"


def describe(p: Dict) -> str:
    if p["op"] == "CORRECT":
        return f"CORRECT {p['target']} -> {p['text']}"
    tags = ",".join(p.get("tags") or [])
    return f"{p['op']} [{p['kind']}{'; ' + tags if tags else ''}] {p['text']}"


def apply(store: Store, proposals: List[Dict], source: str) -> List[Dict]:
    """Append accepted proposals. Always 'claimed'; source = the model."""
    out = []
    for p in proposals:
        if p["op"] == "CORRECT":
            out.append(store.correct(p["target"], p["text"], source=source))
        else:
            out.append(store.add(p["kind"], p["text"], p.get("tags"), source=source))
    return out

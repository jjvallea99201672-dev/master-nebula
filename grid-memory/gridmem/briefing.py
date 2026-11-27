"""Phase 1: a compact, paste-ready briefing for any chat model."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from . import OPERATOR, VERSION
from .store import Store

DEFAULT_MAX_CHARS = 6000
ENTRY_TEXT_LIMIT = 320
SECTION_ORDER = [
    ("verified", "VERIFIED - a reproducible check was run and recorded"),
    ("operator-confirmed", "OPERATOR-CONFIRMED - Jay's word, no machine check"),
    ("check-failed", "CHECK-FAILED - a recorded check did NOT pass; treat as false or unresolved"),
    ("claimed", "CLAIMED - not verified; never present these as fact"),
]

RULES = """RULES FOR YOU (the receiving model)
1. This is a notebook Jay keeps and pastes into chats. You are not connected to it and it does not train you.
2. VERIFIED: a check was run and recorded (hash, test output, command, or a source Jay confirmed). You may rely on it.
3. OPERATOR-CONFIRMED: Jay says it is true; no machine check. Attribute it to Jay.
4. CLAIMED / CHECK-FAILED: unverified or failed. Say "claimed" if you mention it. Do not restate it as fact.
5. Never say you verified, certified, audited or approved anything unless you ran a check and show it. Other models agreeing is not evidence.
6. Superseded/retracted entries are listed by id only. Do not use them.
7. Propose memory changes ONLY in the gridmem block described at the end. Jay reviews every line; nothing you write is saved automatically, and nothing you propose can mark an entry verified."""

FOOTER = """HOW TO PROPOSE MEMORY CHANGES
End your reply with one fenced block in exactly this format (one item per line, omit the block if you have nothing to propose):
```gridmem
ADD|kind|tags|text
CORRECT|<id>|text
QUESTION|text
```
kind = fact, decision, note or question. tags = comma-separated words. Everything you propose is stored as "claimed" with you as the source.
=== END GRID MEMORY BRIEFING ==="""


def _line(e: Dict) -> str:
    text = " ".join(e["text"].split())
    if len(text) > ENTRY_TEXT_LIMIT:
        text = text[:ENTRY_TEXT_LIMIT - 1] + "…"
    bits = [f"{e['id']} [{e['kind']}"]
    if e["tags"]:
        bits.append("; " + ",".join(e["tags"][:5]))
    s = "".join(bits) + f"] {text} (src: {e['source']}"
    if e.get("supersedes"):
        s += f"; replaces {e['supersedes']}"
    if e["status"] == "verified":
        s += f"; evidence: {e['evidence'][-1]['type']}"
    if e.get("agreed_by"):
        s += f"; agreed by {len(e['agreed_by'])} model(s), not evidence"
    if e.get("status_note"):
        s += f"; {e['status_note']}"
    return s + ")"


def _matches(e: Dict, topic: Optional[str]) -> bool:
    if not topic:
        return True
    t = topic.lower()
    return t in e["tags"] or t in e["text"].lower() or t in e["id"].lower()


def build_briefing(store: Store, topic: Optional[str] = None, max_chars: int = DEFAULT_MAX_CHARS,
                   model: Optional[str] = None, now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    seq, head = store.head()
    ents = [e for e in store.entries().values() if _matches(e, topic)]
    header = (f"=== GRID MEMORY BRIEFING (gridmem v{VERSION}) ===\n"
              f"Operator: {OPERATOR['name']} ({OPERATOR['handle']}, operator ID {OPERATOR['operator_id']})\n"
              f"Date: {now.strftime('%Y-%m-%d %H:%M UTC')} | For: {model or 'any model'}"
              f"{' | Topic: ' + topic if topic else ''}\n"
              f"Chain head: {head[:12]}…{head[-6:]} (event {seq}, signed {store.identity['algorithm']})\n\n"
              f"{RULES}\n")
    footer = "\n" + FOOTER
    budget = max_chars - len(header) - len(footer)
    if budget < 120:
        raise ValueError(f"--max-chars {max_chars} is too small; header and rules alone need "
                         f"{len(header) + len(footer) + 120}")

    # priority: verified > operator-confirmed > check-failed > claimed; newest first within a group
    groups: Dict[str, List[Dict]] = {k: [] for k, _ in SECTION_ORDER}
    gone = []
    for e in ents:
        if e["status"] in groups:
            groups[e["status"]].append(e)
        else:
            gone.append(e)
    for g in groups.values():
        g.sort(key=lambda e: e["seq"], reverse=True)

    body, omitted = [], 0
    used = 0
    for status, title in SECTION_ORDER:
        lines = []
        for e in groups[status]:
            ln = _line(e)
            need = len(ln) + 1 + (len(title) + 4 if not lines else 0)
            if used + need > budget - 90:      # keep room for the omitted/gone lines
                omitted += 1
                continue
            lines.append(ln)
            used += need
        if lines:
            body.append(f"\n[{title}]\n" + "\n".join(lines))
    if gone:
        g = "\n[NOT CURRENT - do not use] " + ", ".join(
            f"{e['id']}({e['status']})" for e in sorted(gone, key=lambda e: e["seq"]))
        if used + len(g) <= budget - 70:
            body.append(g)
            used += len(g)
        else:
            omitted += len(gone)
    if not ents:
        body.append("\n(no entries" + (f" match topic '{topic}'" if topic else "") + ")")
    if omitted:
        body.append(f"\n({omitted} lower-priority entries omitted to fit {max_chars} chars; ask Jay.)")
    out = header + "".join(body) + "\n" + footer
    assert len(out) <= max_chars, (len(out), max_chars)
    return out

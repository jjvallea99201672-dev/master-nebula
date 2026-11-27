"""Phase 2: send briefing + question to several models, store replies as notes,
queue their gridmem proposals for review, and summarise where they differ."""
from __future__ import annotations

import json
import os
import re
import shutil
import textwrap
import uuid
from itertools import combinations
from typing import Dict, List, Optional

from .briefing import build_briefing
from .ingest import parse_reply
from .providers import Adapter, KeyNotSet, ProviderError
from .store import Store, utc_now

NUM_RE = re.compile(r"(?<![\w.])-?\d+(?:[.,]\d+)?%?")
WORD_RE = re.compile(r"[a-z0-9']{3,}")
STOP = set("the and for that this with are was you not but have from they will can its into than then "
           "there their which what when also about would could should been being any all".split())


# ---------- pending proposals (not part of memory until accepted) ----------
def _pending_path(store: Store):
    return store.home / "pending.json"


def load_pending(store: Store) -> List[Dict]:
    p = _pending_path(store)
    return json.loads(p.read_text()) if p.exists() else []


def save_pending(store: Store, items: List[Dict]) -> None:
    p = _pending_path(store)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=2))
    os.replace(tmp, p)


def queue_proposals(store: Store, source: str, reply_entry: Optional[str], proposals: List[Dict]) -> List[Dict]:
    items = load_pending(store)
    new = []
    for p in proposals:
        item = {"pid": "P-" + uuid.uuid4().hex[:8], "source": source, "reply_entry": reply_entry,
                "queued_at": utc_now(), "proposal": p}
        items.append(item); new.append(item)
    save_pending(store, items)
    return new


def resolve_pending(store: Store, pids: List[str], accept: bool) -> List[Dict]:
    from .ingest import apply
    items = load_pending(store)
    chosen = [i for i in items if i["pid"] in pids or "all" in pids]
    missing = set(pids) - {i["pid"] for i in chosen} - {"all"}
    if missing:
        raise ValueError(f"no pending proposal(s): {', '.join(sorted(missing))}")
    written = []
    if accept:
        for i in chosen:
            written += apply(store, [i["proposal"]], source=i["source"])
    save_pending(store, [i for i in items if i not in chosen])
    return written


# ---------- asking ----------
def ask_models(store: Store, adapters: List[Adapter], question: str, topic: Optional[str] = None,
               max_chars: int = 6000, save: bool = True) -> Dict:
    results = []
    for a in adapters:
        system = build_briefing(store, topic=topic, max_chars=max_chars, model=a.name)
        r = {"model": a.name, "label": a.label, "model_id": getattr(a, "model", ""), "ok": False}
        try:
            reply = a.ask(system, question)
            r.update(ok=True, reply=reply)
        except KeyNotSet as e:
            r.update(error=str(e), skipped=True)
        except ProviderError as e:
            r.update(error=f"{a.label}: {e}")
        except Exception as e:  # network libs can raise odd things; never crash the batch
            r.update(error=f"{a.label}: {type(e).__name__}: {e}")
        if r["ok"]:
            source = f"{a.name} (api:{r['model_id'] or a.name})"
            parsed = parse_reply(r["reply"], known_ids=set(store.entries()))
            r["parsed"] = parsed
            if save:
                note = store.add("note", f"Q: {question}\nA ({a.label}): {r['reply']}",
                                 tags=["ask-reply", a.name] + ([topic] if topic else []), source=source)
                r["entry"] = note["id"]
                r["pending"] = queue_proposals(store, source, note["id"], parsed["proposals"])
        results.append(r)
    return {"question": question, "results": results, "summary": disagreement_summary(results)}


def _strip_block(t: str) -> str:
    return re.sub(r"```gridmem.*?```", "", t or "", flags=re.S | re.I).strip()


def disagreement_summary(results: List[Dict]) -> Dict:
    ok = [r for r in results if r["ok"]]
    out = {"answered": [r["model"] for r in ok],
           "failed": [r["model"] for r in results if not r["ok"]],
           "pairwise_word_overlap": {}, "numbers": {}, "numbers_not_shared": {}, "first_lines": {},
           "label": "Heuristic text comparison only. It does not judge who is right; overlap is not evidence."}
    words, nums = {}, {}
    for r in ok:
        t = _strip_block(r["reply"])
        words[r["model"]] = {w for w in WORD_RE.findall(t.lower()) if w not in STOP}
        nums[r["model"]] = set(NUM_RE.findall(t))
        out["numbers"][r["model"]] = sorted(nums[r["model"]])
        first = next((ln.strip() for ln in t.splitlines() if ln.strip()), "")
        out["first_lines"][r["model"]] = first[:160]
    for a, b in combinations(words, 2):
        u = words[a] | words[b]
        out["pairwise_word_overlap"][f"{a}~{b}"] = round(len(words[a] & words[b]) / len(u), 2) if u else 1.0
    if len(nums) > 1:
        allnums = set().union(*nums.values())
        for m, s in nums.items():
            others = set().union(*(v for k, v in nums.items() if k != m))
            diff = sorted(s - others)
            if diff:
                out["numbers_not_shared"][m] = diff
        out["all_numbers_agree"] = all(s == allnums for s in nums.values())
    return out


def render(res: Dict, width: Optional[int] = None) -> str:
    width = width or shutil.get_terminal_size((160, 40)).columns
    rs = res["results"]
    lines = [f"QUESTION: {res['question']}", ""]
    ncol = max(1, min(len(rs), max(1, width // 48)))
    for i in range(0, len(rs), ncol):
        chunk = rs[i:i + ncol]
        colw = (width - 3 * (len(chunk) - 1)) // len(chunk)
        cols = []
        for r in chunk:
            head = f"{r['label']} [{r.get('model_id') or '-'}]"
            body = _strip_block(r["reply"]) if r["ok"] else f"(no answer) {r.get('error', '')}"
            wrapped = [head[:colw], "-" * min(colw, len(head))]
            for para in body.splitlines() or [""]:
                wrapped += textwrap.wrap(para, colw) or [""]
            if r["ok"]:
                n = len(r.get("pending") or r.get("parsed", {}).get("proposals", []))
                wrapped += ["", f"-> saved as {r.get('entry', '(not saved)')}; {n} proposal(s) pending"[:colw]]
            cols.append(wrapped)
        h = max(len(c) for c in cols)
        for row in range(h):
            lines.append(" | ".join((c[row] if row < len(c) else "").ljust(colw) for c in cols).rstrip())
        lines.append("")
    s = res["summary"]
    lines.append("DISAGREEMENT SUMMARY (heuristic, not a judgement of who is right)")
    lines.append(f"  answered: {', '.join(s['answered']) or 'none'}; no answer: {', '.join(s['failed']) or 'none'}")
    for pair, v in s["pairwise_word_overlap"].items():
        tag = "very different" if v < 0.2 else "partly overlapping" if v < 0.45 else "similar wording"
        lines.append(f"  word overlap {pair}: {v} ({tag})")
    for m, d in s["numbers_not_shared"].items():
        lines.append(f"  numbers only {m} gave: {', '.join(d[:10])}")
    if len(s["answered"]) > 1 and not s["numbers_not_shared"]:
        lines.append("  no numbers unique to one model")
    lines.append("  Agreement between models is not evidence; nothing here changes any entry's status.")
    lines.append("  Review proposals with: gridmem pending list")
    return "\n".join(lines)

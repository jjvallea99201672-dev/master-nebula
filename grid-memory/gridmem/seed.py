"""Minimal, accurate starter entries. Runs only on an empty store."""
from __future__ import annotations

import sys
from pathlib import Path

from . import evidence as ev
from .keeper import CLAIMED_GENESIS_ANCHOR, KEEPER_DIR, KEEPER_VERSION
from .store import GridMemError, Store

AUDIT_DIR = Path("/workspace/derivation-audit")


def seed(st: Store, run_keeper_tests: bool = False):
    if st.head()[0]:
        raise GridMemError("store is not empty; seed runs only once")
    out = []
    g = st.add("fact",
               f"Lattice Keeper genesis anchor is {CLAIMED_GENESIS_ANCHOR}. No input that hashes to it has been "
               f"found: a search of about 362,000 candidates (sha256, sha3-256, blake2s, double-sha256) on "
               f"2026-10-25 had no match. Keeper nodes report FORENSIC_FAIL because their genesis file does not hash to it.",
               tags=["genesis", "keeper", "anchor"], source="Grok Bot (forensic search 2026-10-25)",
               status_note="claimed, unverified (no known preimage; ~362k candidates tested)",
               references=[{"type": "search-record", "path": str(KEEPER_DIR / "forensic/preimage_search_result.txt"),
                            "note": "a failed preimage search; this does not verify the anchor"}])
    out.append(f"{g['id']} genesis anchor: claimed, unverified")

    n = st.add("fact",
               f"Lattice Keeper v{KEEPER_VERSION} runs as 3 nodes on Grok Bot's box (127.0.0.1 ports 8765-8767). "
               f"It is a local test network, not a public network or mainnet.",
               tags=["keeper", "nodes"], source="Grok Bot (build notes 2026-10-25)",
               status_note="point-in-time; check with curl http://127.0.0.1:8765/health")
    out.append(f"{n['id']} keeper nodes: claimed (running state changes over time)")
    k = st.add("fact",
               "The Lattice Keeper test suite has 88 tests (65 for v0.34.1 plus 23 for the v0.40.1 drift detector) "
               "and they pass.",
               tags=["keeper", "tests"], source="Grok Bot (build notes 2026-10-25)")
    if run_keeper_tests:
        print("running Keeper test suite (read-only; uses temp dirs and free ports)...", file=sys.stderr)
        e = ev.command_check([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
                             cwd=str(KEEPER_DIR), expect="88 passed", timeout=900)
        st.attach_evidence(k["id"], e)
        out.append(f"{k['id']} keeper tests: command check {e['result']} ({e['output_tail'][-1] if e['output_tail'] else ''})")
    else:
        out.append(f"{k['id']} keeper tests: claimed (verify with: gridmem verify {k['id']} --command "
                   f"'python -m pytest -q tests' --cwd {KEEPER_DIR} --expect '88 passed')")

    f = st.add("fact",
               "Corrected formulas 17'-21' are in /workspace/derivation-audit/formulas_17_21_v2.md, with the "
               "originals kept and marked RETRACTED. Their check script reports 34/34 checks passed. That shows "
               "internal consistency only: the double-well potential is still assumed, and items 12 and 16 still "
               "use the old exponents.",
               tags=["physics", "formulas", "derivation"], source="Grok Bot (derivation audit 2026-10-25)")
    out_file = AUDIT_DIR / "formulas_17_21_v2_checks_output.txt"
    if out_file.exists():
        e = ev.output_check(str(out_file), "34/34 checks passed (final)")
        st.attach_evidence(f["id"], e)
        out.append(f"{f['id']} formulas 17'-21': output check {e['result']} (file sha256 {e['file_sha256'][:12]}…)")
    else:
        out.append(f"{f['id']} formulas: claimed (checks file not found)")

    d = st.add("decision",
               "Grid Memory rule: agreement between models never upgrades a status. Only a recorded, reproducible "
               "check makes an entry verified; Jay's word alone is recorded as operator-confirmed.",
               tags=["gridmem", "rules"], source="Grok Bot (design, 2026-10-25)")
    out.append(f"{d['id']} status rule: claimed (design decision)")
    b = st.add("decision", "Build order: Phase 1 (copy-paste briefings) first, then Phase 2 (API connectors).",
               tags=["gridmem", "plan"], source="Jay (chat 2026-10-25, recorded by Grok Bot)")
    out.append(f"{b['id']} build order: claimed (Jay can run --operator-confirm)")
    return out

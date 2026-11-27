"""Precursor isomorphic bridge (Series 4).

Verifies and, when authorized, maps the ancient Sector 51 bismuth-quartz
beacon into Cell 65,500 of the toroidal matrix.

Unlike the toy script, this does not always return True. It returns True
only if:

1. The payload carries a valid ML-DSA-65 signature over canonical JSON
   plus the New Brunswick genesis anchor, AND
2. Either the naturally derived cell is 65,500 (a true hash landing),
   OR the payload requests reserved_cell 65,500 and is signed by the
   root node NB-ROOT (reserved-cell placement).

Otherwise it returns False and records a reason in last_result.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from constants import GENESIS_ANCHOR, LEDGER_SIZE, PRECURSOR_TARGET_CELL, ROOT_NODE_ID
from crypto_utils import anchored_hash
from ledger import ToroidalLedger, preferred_cell
from pq_relay_security import InterstellarPQCManager, _parse_dsa_pk, verify_signature

log = logging.getLogger("toroidal.bridge")


class PrecursorLedgerBridge:
    def __init__(
        self,
        ledger: Optional[ToroidalLedger] = None,
        target_cell: int = PRECURSOR_TARGET_CELL,
        root_node_id: str = ROOT_NODE_ID,
    ):
        self.new_brunswick_genesis = GENESIS_ANCHOR
        self.total_toroidal_cells = LEDGER_SIZE
        self.target_cell = target_cell
        self.root_node_id = root_node_id
        self.ledger = ledger
        self.last_result: dict[str, Any] | None = None

    def derived_hash_and_cell(self, precursor_payload: dict) -> tuple[str, int]:
        derived_hash = anchored_hash(precursor_payload, self.new_brunswick_genesis)
        return derived_hash, preferred_cell(derived_hash, self.total_toroidal_cells)

    def verify_precursor_isomorphism(
        self,
        precursor_payload: dict,
        signature: dict | None = None,
        public_key: bytes | str | None = None,
        signer_id: str | None = None,
        ledger: Optional[ToroidalLedger] = None,
        manager: Optional[InterstellarPQCManager] = None,
    ) -> bool:
        """Verifies that the precursor beacon's structural signature nests within the Genesis Anchor."""
        derived_hash, derived_cell = self.derived_hash_and_cell(precursor_payload)
        signer_id = signer_id or (signature or {}).get("signer") or (manager.node_id if manager else None)
        ledger = ledger if ledger is not None else self.ledger

        if not signature:
            return self._reject(
                "missing ML-DSA-65 signature", derived_hash, derived_cell, signer_id
            )

        # signer_id is only a CLAIM (it comes from the sender's signature dict).
        # A key is "trusted" only if it is registered for that identity in the
        # ledger, or belongs to the manager whose node_id is that identity.
        trusted = self._trusted_key(signer_id, ledger, manager)
        if public_key is not None and trusted is not None:
            try:
                same = _parse_dsa_pk(public_key) == _parse_dsa_pk(trusted)
            except Exception:
                same = False
            if not same:
                return self._reject(
                    f"supplied public key does not match the registered key for {signer_id}",
                    derived_hash, derived_cell, signer_id,
                )
        pk = trusted if trusted is not None else public_key
        if pk is None:
            return self._reject(
                "no public key available for signer", derived_hash, derived_cell, signer_id
            )

        if not verify_signature(precursor_payload, signature, pk, self.new_brunswick_genesis):
            return self._reject(
                "ML-DSA-65 signature verification failed",
                derived_hash,
                derived_cell,
                signer_id,
            )

        mode = None
        if derived_cell == self.target_cell:
            mode = "natural"
        elif (
            precursor_payload.get("reserved_cell") == self.target_cell
            and signer_id == self.root_node_id
            and trusted is not None
        ):
            mode = "reserved"
        else:
            if (
                precursor_payload.get("reserved_cell") == self.target_cell
                and signer_id == self.root_node_id
            ):
                reason = (
                    f"reserved placement needs a registered key for {self.root_node_id}; "
                    "an unverified public_key is not accepted"
                )
            elif precursor_payload.get("reserved_cell") == self.target_cell:
                reason = (
                    f"reserved cell {self.target_cell} requires signer "
                    f"{self.root_node_id}, got {signer_id}"
                )
            else:
                reason = (
                    f"derived cell {derived_cell} != target {self.target_cell} "
                    "and no authorized reserved placement"
                )
            return self._reject(reason, derived_hash, derived_cell, signer_id)

        placed_cell = derived_cell if mode == "natural" else self.target_cell
        if ledger is not None and ledger.get_entry_at(placed_cell) is not None:
            return self._reject(
                f"target cell {placed_cell} is already occupied",
                derived_hash,
                derived_cell,
                signer_id,
            )

        entry = None
        if ledger is not None and manager is not None:
            reserved = placed_cell if mode == "reserved" else None
            record = {
                "beacon": precursor_payload.get("beacon") or "Sector 51 bismuth-quartz",
                "precursor_payload": precursor_payload,
                "derived_hash": derived_hash,
                "derived_cell": derived_cell,
                "mode": mode,
                "target_cell": self.target_cell,
                "reserved_cell": precursor_payload.get("reserved_cell"),
            }
            if reserved is not None:
                record["reserved_cell"] = reserved
            entry = ledger.commit("precursor_bridge", record, manager, reserved_cell=reserved)
            placed_cell = entry["cell"]

        self.last_result = {
            "ok": True,
            "reason": f"isomorphism confirmed via {mode} placement",
            "derived_hash": derived_hash,
            "derived_cell": derived_cell,
            "target_cell": self.target_cell,
            "placed_cell": placed_cell,
            "signer": signer_id,
            "mode": mode,
            "entry": entry,
        }
        log.info("Precursor beacon successfully anchored to Toroidal Cell: %s", placed_cell)
        log.info("Isomorphic Hash Match Confirmed [Root: %s...]", derived_hash[:16])
        return True

    @staticmethod
    def _trusted_key(signer_id, ledger, manager):
        """Key bound to this identity by the ledger registry or the manager, else None."""
        if not signer_id:
            return None
        if ledger is not None:
            record = ledger.public_keys.get(signer_id) or {}
            hexkey = record.get("ml_dsa_public_key_hex")
            if hexkey:
                return hexkey
        if manager is not None and manager.node_id == signer_id:
            return manager.ml_dsa_public_key
        return None

    def _reject(
        self,
        reason: str,
        derived_hash: str,
        derived_cell: int,
        signer_id: str | None,
    ) -> bool:
        self.last_result = {
            "ok": False,
            "reason": reason,
            "derived_hash": derived_hash,
            "derived_cell": derived_cell,
            "target_cell": self.target_cell,
            "signer": signer_id,
            "mode": None,
        }
        log.info("PRECURSOR BRIDGE REJECTED: %s [derived cell %05d]", reason, derived_cell)
        return False

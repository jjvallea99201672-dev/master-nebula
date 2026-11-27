"""65,536-cell toroidal ledger with a hash chain and ML-DSA-65 signatures.

Cell assignment is int(payload_hash, 16) % 65536, then linear probing on
collision (wrap-around). Neighbors are the four orthogonal cells on a
256 x 256 torus. Each commit is signed by the submitting node and chained
to the previous entry hash. The first prev_hash is the New Brunswick
genesis anchor.

Reserved-cell placement (used by the precursor bridge) is allowed only
when the signer is the root node and the payload requests that cell
explicitly.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from constants import (
    DATA_DIR,
    GENESIS_ANCHOR,
    GRID_SIDE,
    LEDGER_PATH,
    LEDGER_SIZE,
    ROOT_NODE_ID,
)
from crypto_utils import anchored_hash, canonical_json, sha256_hex, short_hex
from pq_relay_security import InterstellarPQCManager, verify_signature

log = logging.getLogger("toroidal.ledger")


def preferred_cell(payload_hash: str, size: int = LEDGER_SIZE) -> int:
    return int(payload_hash, 16) % size


def cell_to_row_col(cell: int, side: int = GRID_SIDE) -> tuple[int, int]:
    return divmod(cell, side)


def row_col_to_cell(row: int, col: int, side: int = GRID_SIDE) -> int:
    return (row % side) * side + (col % side)


def torus_neighbors(cell: int, side: int = GRID_SIDE) -> dict[str, int]:
    """Four orthogonal neighbors with wrap-around on a side x side torus."""
    row, col = cell_to_row_col(cell, side)
    return {
        "n": row_col_to_cell(row - 1, col, side),
        "s": row_col_to_cell(row + 1, col, side),
        "w": row_col_to_cell(row, col - 1, side),
        "e": row_col_to_cell(row, col + 1, side),
        "row": row,
        "col": col,
    }


class ToroidalLedger:
    def __init__(
        self,
        path: Path | str | None = None,
        size: int = LEDGER_SIZE,
        genesis_anchor: str = GENESIS_ANCHOR,
        root_node_id: str = ROOT_NODE_ID,
    ):
        self.path = Path(path) if path else LEDGER_PATH
        self.size = size
        self.genesis_anchor = genesis_anchor
        self.root_node_id = root_node_id
        self.entries: list[dict[str, Any]] = []
        self.cells: dict[int, int] = {}  # cell -> index in entries
        self.public_keys: dict[str, dict] = {}
        if self.path.exists():
            self.load()

    @property
    def head_hash(self) -> str:
        if not self.entries:
            return self.genesis_anchor
        return self.entries[-1]["entry_hash"]

    @property
    def n_occupied(self) -> int:
        return len(self.entries)

    def allocate_cell(self, preferred: int, occupied: set[int] | None = None) -> tuple[int, int]:
        """Linear probe forward from preferred, wrapping around the torus."""
        taken = occupied if occupied is not None else set(self.cells)
        cell = preferred % self.size
        steps = 0
        while cell in taken:
            cell = (cell + 1) % self.size
            steps += 1
            if steps >= self.size:
                raise RuntimeError("toroidal ledger is full")
        return cell, steps

    def register_public_key(self, manager: InterstellarPQCManager) -> None:
        self.public_keys[manager.node_id] = {
            "node_id": manager.node_id,
            "sector": manager.sector,
            "ml_kem_public_key_hex": manager.ml_kem_public_key.hex(),
            "ml_dsa_public_key_hex": manager.ml_dsa_public_key.hex(),
        }

    def commit(
        self,
        module: str,
        payload: dict,
        signer: InterstellarPQCManager,
        reserved_cell: int | None = None,
    ) -> dict[str, Any]:
        """Hash, place, sign and append a record. Returns the ledger entry."""
        self.register_public_key(signer)
        payload_hash = anchored_hash(payload, self.genesis_anchor)
        natural = preferred_cell(payload_hash, self.size)
        reserved = False
        if reserved_cell is not None:
            self._authorize_reserved(signer.node_id, payload, reserved_cell)
            if reserved_cell in self.cells:
                raise ValueError(f"reserved cell {reserved_cell} is already occupied")
            cell, steps = reserved_cell, 0
            reserved = True
        else:
            cell, steps = self.allocate_cell(natural)

        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        signed_body = {
            "cell": cell,
            "preferred_cell": natural,
            "probe_steps": steps,
            "reserved": reserved,
            "module": module,
            "payload": payload,
            "payload_hash": payload_hash,
            "prev_hash": self.head_hash,
            "signer": signer.node_id,
            "timestamp": timestamp,
        }
        signature = signer.sign_toroidal_state(signed_body)
        entry_hash = sha256_hex(canonical_json(signed_body) + signature["quantum_signature"])
        entry = {
            **signed_body,
            "signature": signature,
            "entry_hash": entry_hash,
            "neighbors": torus_neighbors(cell),
        }
        self.cells[cell] = len(self.entries)
        self.entries.append(entry)
        self.save()
        log.info(
            "LEDGER COMMIT: module=%s signer=%s cell=%05d preferred=%05d probe=%d hash=%s",
            module,
            signer.node_id,
            cell,
            natural,
            steps,
            short_hex(entry_hash),
        )
        return entry

    def _authorize_reserved(self, signer_id: str, payload: dict, reserved_cell: int) -> None:
        if signer_id != self.root_node_id:
            raise PermissionError(
                f"reserved cell {reserved_cell} requires signer {self.root_node_id}, "
                f"not {signer_id}"
            )
        requested = payload.get("reserved_cell")
        if requested != reserved_cell:
            raise PermissionError(
                "reserved placement requires payload['reserved_cell'] to match "
                f"the requested cell {reserved_cell}, got {requested!r}"
            )

    def verify_chain(self) -> dict[str, Any]:
        """Recheck every hash-chain link, payload hash, placement and ML-DSA signature."""
        errors: list[str] = []
        occupied: set[int] = set()
        prev = self.genesis_anchor
        for i, entry in enumerate(self.entries):
            prefix = f"entry[{i}] cell {entry.get('cell')}"
            payload = entry.get("payload")
            payload_hash = entry.get("payload_hash")
            if payload_hash != anchored_hash(payload, self.genesis_anchor):
                errors.append(f"{prefix}: payload hash mismatch")
            natural = preferred_cell(payload_hash or "0", self.size)
            if entry.get("preferred_cell") != natural:
                errors.append(
                    f"{prefix}: preferred_cell {entry.get('preferred_cell')} != natural {natural}"
                )
            if entry.get("reserved"):
                expected_cell = payload.get("reserved_cell") if isinstance(payload, dict) else None
                if entry.get("signer") != self.root_node_id:
                    errors.append(f"{prefix}: reserved cell not signed by root")
                if expected_cell != entry.get("cell"):
                    errors.append(f"{prefix}: reserved cell does not match payload request")
                if entry.get("cell") in occupied:
                    errors.append(f"{prefix}: reserved cell already occupied")
            else:
                expected_cell, expected_steps = self.allocate_cell(natural, occupied)
                if entry.get("cell") != expected_cell or entry.get("probe_steps") != expected_steps:
                    errors.append(
                        f"{prefix}: placement {entry.get('cell')} steps {entry.get('probe_steps')} "
                        f"!= expected {expected_cell} steps {expected_steps}"
                    )
            if entry.get("prev_hash") != prev:
                errors.append(f"{prefix}: prev_hash break (expected {short_hex(prev)})")
            signed_body = {
                "cell": entry.get("cell"),
                "preferred_cell": entry.get("preferred_cell"),
                "probe_steps": entry.get("probe_steps"),
                "reserved": entry.get("reserved"),
                "module": entry.get("module"),
                "payload": payload,
                "payload_hash": payload_hash,
                "prev_hash": entry.get("prev_hash"),
                "signer": entry.get("signer"),
                "timestamp": entry.get("timestamp"),
            }
            recomputed_hash = sha256_hex(
                canonical_json(signed_body) + (entry.get("signature") or {}).get("quantum_signature", "")
            )
            if entry.get("entry_hash") != recomputed_hash:
                errors.append(f"{prefix}: entry_hash mismatch")
            signer_id = entry.get("signer")
            pk_hex = (self.public_keys.get(signer_id) or {}).get("ml_dsa_public_key_hex")
            if not pk_hex:
                errors.append(f"{prefix}: no public key on file for signer {signer_id}")
            elif not verify_signature(signed_body, entry.get("signature") or {}, pk_hex, self.genesis_anchor):
                errors.append(f"{prefix}: ML-DSA-65 signature failed")
            cell = entry.get("cell")
            if not isinstance(cell, int) or not (0 <= cell < self.size):
                errors.append(f"{prefix}: cell out of range")
            else:
                occupied.add(cell)
            prev = entry.get("entry_hash") or ""
        ok = not errors
        result = {
            "ok": ok,
            "entries": len(self.entries),
            "occupied": len(occupied),
            "errors": errors,
            "head_hash": self.head_hash,
        }
        if ok:
            log.info("LEDGER VERIFY: chain intact (%d entries, head %s).", len(self.entries), short_hex(self.head_hash))
        else:
            log.info("LEDGER VERIFY: FAILED with %d error(s).", len(errors))
            for err in errors:
                log.info("  - %s", err)
        return result

    def get_entry_at(self, cell: int) -> dict | None:
        idx = self.cells.get(cell)
        if idx is None:
            return None
        return self.entries[idx]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ledger_size": self.size,
            "grid": [GRID_SIDE, GRID_SIDE],
            "genesis_anchor": self.genesis_anchor,
            "root_node_id": self.root_node_id,
            "head_hash": self.head_hash,
            "occupied": self.n_occupied,
            "public_keys": self.public_keys,
            "entries": self.entries,
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def load(self) -> None:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.size = int(data.get("ledger_size") or self.size)
        self.genesis_anchor = data.get("genesis_anchor") or self.genesis_anchor
        self.root_node_id = data.get("root_node_id") or self.root_node_id
        self.public_keys = data.get("public_keys") or {}
        self.entries = data.get("entries") or []
        self.cells = {}
        for i, entry in enumerate(self.entries):
            self.cells[entry["cell"]] = i

    def copy_to(self, path: Path | str) -> "ToroidalLedger":
        """Write a snapshot and return a ledger loaded from that path."""
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
        return ToroidalLedger(path=dest, size=self.size, genesis_anchor=self.genesis_anchor, root_node_id=self.root_node_id)


def tamper_entry(ledger: ToroidalLedger, index: int, mutator) -> None:
    """Apply mutator(entry) in place and save. For tests and the demo only."""
    mutator(ledger.entries[index])
    ledger.save()

"""Ledger hash-chain integrity, collision probing and torus neighbors."""

from __future__ import annotations

import unittest

from helpers import make_ledger, make_node, make_store, temp_dir
from constants import GENESIS_ANCHOR, LEDGER_SIZE, ROOT_NODE_ID
from crypto_utils import anchored_hash
from ledger import preferred_cell, tamper_entry, torus_neighbors


class TestLedger(unittest.TestCase):
    def test_chain_integrity_and_genesis_link(self):
        with temp_dir() as td:
            store = make_store(td)
            ledger = make_ledger(td)
            root = make_node(store, ROOT_NODE_ID)
            first = ledger.commit("beacon_deployment", {"n": 1}, root)
            second = ledger.commit("exoplanet_audit", {"n": 2}, root)
            self.assertEqual(first["prev_hash"], GENESIS_ANCHOR)
            self.assertEqual(second["prev_hash"], first["entry_hash"])
            result = ledger.verify_chain()
            self.assertTrue(result["ok"], result["errors"])
            self.assertEqual(result["entries"], 2)

    def test_reload_from_disk_still_verifies(self):
        with temp_dir() as td:
            store = make_store(td)
            ledger = make_ledger(td)
            node = make_node(store, ROOT_NODE_ID)
            ledger.commit("seed_dispersal", {"crop_strain": "mix"}, node)
            path = ledger.path
            reloaded = type(ledger)(path=path)
            self.assertTrue(reloaded.verify_chain()["ok"])

    def test_tampered_entry_fails_verify(self):
        with temp_dir() as td:
            store = make_store(td)
            ledger = make_ledger(td)
            node = make_node(store, ROOT_NODE_ID)
            ledger.commit("beacon_deployment", {"n": 1}, node)
            ledger.commit("beacon_deployment", {"n": 2}, node)
            tamper_entry(ledger, 0, lambda e: e["payload"].update({"n": 99}))
            result = ledger.verify_chain()
            self.assertFalse(result["ok"])
            self.assertTrue(any("payload hash" in err or "signature" in err for err in result["errors"]))

    def test_broken_prev_hash_fails(self):
        with temp_dir() as td:
            store = make_store(td)
            ledger = make_ledger(td)
            node = make_node(store, ROOT_NODE_ID)
            ledger.commit("beacon_deployment", {"n": 1}, node)
            ledger.commit("beacon_deployment", {"n": 2}, node)
            # Breaking prev_hash also breaks the signed body / entry_hash.
            tamper_entry(ledger, 1, lambda e: e.update({"prev_hash": "00" * 32}))
            result = ledger.verify_chain()
            self.assertFalse(result["ok"])
            self.assertTrue(any("prev_hash" in err or "signature" in err or "entry_hash" in err for err in result["errors"]))

    def test_linear_probe_on_hash_collision(self):
        with temp_dir() as td:
            store = make_store(td)
            ledger = make_ledger(td)
            node = make_node(store, ROOT_NODE_ID)
            first_payload, second_payload, cell = _two_payloads_same_cell()
            a = ledger.commit("beacon_deployment", first_payload, node)
            b = ledger.commit("beacon_deployment", second_payload, node)
            self.assertEqual(a["preferred_cell"], cell)
            self.assertEqual(b["preferred_cell"], cell)
            self.assertEqual(a["cell"], cell)
            self.assertEqual(a["probe_steps"], 0)
            self.assertEqual(b["cell"], (cell + 1) % LEDGER_SIZE)
            self.assertEqual(b["probe_steps"], 1)
            self.assertTrue(ledger.verify_chain()["ok"])

    def test_linear_probe_wraps_around(self):
        with temp_dir() as td:
            ledger = make_ledger(td)
            # Occupy the last cell and cell 0; a prefer of 65535 should land at 1.
            last = LEDGER_SIZE - 1
            occupied = {last, 0}
            cell, steps = ledger.allocate_cell(last, occupied)
            self.assertEqual(cell, 1)
            self.assertEqual(steps, 2)

    def test_torus_neighbors_corners(self):
        n0 = torus_neighbors(0)
        self.assertEqual(n0["row"], 0)
        self.assertEqual(n0["col"], 0)
        self.assertEqual(n0["n"], 255 * 256)  # 65280
        self.assertEqual(n0["s"], 256)
        self.assertEqual(n0["w"], 255)
        self.assertEqual(n0["e"], 1)
        last = torus_neighbors(LEDGER_SIZE - 1)
        self.assertEqual(last["row"], 255)
        self.assertEqual(last["col"], 255)
        self.assertEqual(last["n"], 254 * 256 + 255)
        self.assertEqual(last["s"], 255)
        self.assertEqual(last["w"], 255 * 256 + 254)
        self.assertEqual(last["e"], 255 * 256)  # wrap col to 0

    def test_reserved_cell_rejected_for_non_root(self):
        with temp_dir() as td:
            store = make_store(td)
            ledger = make_ledger(td)
            other = make_node(store, "OUTPOST-SABAOTH")
            with self.assertRaises(PermissionError):
                ledger.commit(
                    "precursor_bridge",
                    {"reserved_cell": 65500},
                    other,
                    reserved_cell=65500,
                )


def _two_payloads_same_cell() -> tuple[dict, dict, int]:
    seen: dict[int, dict] = {}
    for i in range(200_000):
        payload = {"n": i, "tag": "collision-search"}
        cell = preferred_cell(anchored_hash(payload))
        if cell in seen:
            return seen[cell], payload, cell
        seen[cell] = payload
    raise RuntimeError("failed to find a preferred-cell collision")


if __name__ == "__main__":
    unittest.main()

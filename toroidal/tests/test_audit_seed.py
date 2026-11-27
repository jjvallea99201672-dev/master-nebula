"""Habitability thresholds and seeding records."""

from __future__ import annotations

import unittest

from helpers import make_ledger, make_node, make_store, temp_dir
from constants import ROOT_NODE_ID
from exoplanet_audit import (
    BASELINE_HZ,
    STATUS_READY,
    STATUS_STABILIZE,
    ExoplanetHabitibilityAuditor,
)
from seed_dispersal import ExoplanetSeedingProtocol


class TestAuditAndSeed(unittest.TestCase):
    def test_habitability_thresholds(self):
        auditor = ExoplanetHabitibilityAuditor("Nyx", "KV-17")
        # Both conditions just inside the window.
        auditor.audit_habitability(
            {"tectonic_stability_score": 0.8001, "natural_resonance_hz": BASELINE_HZ + 4.999}
        )
        self.assertTrue(auditor.last_habitable)
        self.assertEqual(auditor.last_status, STATUS_READY)
        # Resonance delta of exactly 5.0 is not strictly less than 5.
        auditor.audit_habitability(
            {"tectonic_stability_score": 1.0, "natural_resonance_hz": BASELINE_HZ + 5.0}
        )
        self.assertFalse(auditor.last_habitable)
        self.assertEqual(auditor.last_status, STATUS_STABILIZE)
        # Tectonic score of exactly 0.8 is not strictly greater than 0.8.
        auditor.audit_habitability(
            {"tectonic_stability_score": 0.8, "natural_resonance_hz": BASELINE_HZ}
        )
        self.assertFalse(auditor.last_habitable)

    def test_nyx_like_ice_giant_requires_stabilization(self):
        auditor = ExoplanetHabitibilityAuditor("Nyx", "KV-17")
        digest = auditor.audit_habitability(
            {
                "tectonic_stability_score": 0.31,
                "natural_resonance_hz": 419.0,
                "body_class": "ice giant",
            }
        )
        self.assertEqual(len(digest), 64)
        self.assertEqual(auditor.last_status, STATUS_STABILIZE)
        self.assertAlmostEqual(auditor.last_delta, 13.0)

    def test_audit_and_seed_commit_signed_records(self):
        with temp_dir() as td:
            store = make_store(td)
            ledger = make_ledger(td)
            node = make_node(store, ROOT_NODE_ID)
            auditor = ExoplanetHabitibilityAuditor("Nyx", "KV-17", ledger=ledger, signer=node)
            auditor.audit_habitability(
                {"tectonic_stability_score": 0.31, "natural_resonance_hz": 419.0}
            )
            seeder = ExoplanetSeedingProtocol("Nyx", "KV-17", ledger=ledger, signer=node)
            checksum = seeder.execute_seeding_drop(
                {
                    "crop_strain": "New Brunswick Heirloom Heritage Mix",
                    "mycorrhizal_spores_millions": 50.0,
                }
            )
            self.assertEqual(len(checksum), 64)
            self.assertEqual(ledger.n_occupied, 2)
            modules = [e["module"] for e in ledger.entries]
            self.assertEqual(modules, ["exoplanet_audit", "seed_dispersal"])
            self.assertTrue(ledger.verify_chain()["ok"])
            self.assertEqual(ledger.entries[0]["payload"]["status"], STATUS_STABILIZE)
            self.assertTrue(ledger.entries[0]["payload"]["criterion_is_fiction"])
            self.assertEqual(
                ledger.entries[1]["payload"]["crop_strain"],
                "New Brunswick Heirloom Heritage Mix",
            )


if __name__ == "__main__":
    unittest.main()

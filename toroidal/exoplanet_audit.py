"""Exoplanet habitability auditor (Series 4).

NOTE: The 432 Hz "natural resonance" habitability criterion is fiction.
Real habitability assessments use insolation, atmosphere, liquid water
and geochemistry — not a single acoustic frequency. The tectonic
threshold and resonance window below are worldbuilding rules, not
science. Hashes, ML-DSA signatures and ledger commits are real.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from constants import GENESIS_ANCHOR
from crypto_utils import anchored_hash
from ledger import ToroidalLedger
from pq_relay_security import InterstellarPQCManager

log = logging.getLogger("toroidal.audit")

# Worldbuilding thresholds (fiction). Kept exact with the source toolkit.
BASELINE_HZ = 432.0
RESONANCE_WINDOW_HZ = 5.0
TECTONIC_MINIMUM = 0.8
STATUS_READY = "HABITABLE_CORE_READY"
STATUS_STABILIZE = "REQUIRES_PIEZOELECTRIC_STABILIZATION"


class ExoplanetHabitibilityAuditor:
    def __init__(
        self,
        planet_id: str,
        star_system: str,
        ledger: Optional[ToroidalLedger] = None,
        signer: Optional[InterstellarPQCManager] = None,
    ):
        self.planet_id = planet_id
        self.star_system = star_system
        self.genesis_anchor = GENESIS_ANCHOR
        self.baseline_hz = BASELINE_HZ
        self.ledger = ledger
        self.signer = signer
        self.last_status: str | None = None
        self.last_delta: float | None = None
        self.last_habitable: bool | None = None

    def audit_habitability(self, environmental_data: dict) -> str:
        """Evaluates seismic, atmospheric, and hydrological resonance suitability."""
        tectonic_stability = float(environmental_data.get("tectonic_stability_score", 0.0))
        fluid_dynamics_hz = float(environmental_data.get("natural_resonance_hz", 0.0))

        log.info("[%s @ %s] Auditing acoustic habitability...", self.planet_id, self.star_system)
        resonance_delta = abs(fluid_dynamics_hz - self.baseline_hz)
        is_habitable = resonance_delta < RESONANCE_WINDOW_HZ and tectonic_stability > TECTONIC_MINIMUM

        status = STATUS_READY if is_habitable else STATUS_STABILIZE
        log.info("AUDIT RESULT: %s [Resonance Delta: %.2f Hz]", status, resonance_delta)

        checksum = anchored_hash(environmental_data, self.genesis_anchor)
        record = {
            "planet_id": self.planet_id,
            "star_system": self.star_system,
            "environmental_data": environmental_data,
            "status": status,
            "resonance_delta_hz": resonance_delta,
            "baseline_hz": self.baseline_hz,
            "is_habitable": is_habitable,
            "criterion_is_fiction": True,
            "checksum": checksum,
        }
        if self.ledger is not None and self.signer is not None:
            self.ledger.commit("exoplanet_audit", record, self.signer)

        self.last_status = status
        self.last_delta = resonance_delta
        self.last_habitable = is_habitable
        return checksum

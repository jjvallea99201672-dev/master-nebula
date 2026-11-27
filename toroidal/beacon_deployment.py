"""Exploratory wave beacon cartography (Series 4).

Deploys mobile acoustic probes into uncharted star systems and commits
spatial nodes to the toroidal ledger.

NOTE: A 432 Hz sound wave cannot travel through interstellar vacuum. In
the fiction, "acoustic" pings stand in for gravitational or plasma-wave
sweeps that the ship transduces into sound — the same trick real
astronomers use with LIGO and spacecraft plasma-wave data. The hashing,
cell placement and signatures are real.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from constants import GENESIS_ANCHOR
from crypto_utils import anchored_hash, short_hex
from ledger import ToroidalLedger, preferred_cell
from pq_relay_security import InterstellarPQCManager

log = logging.getLogger("toroidal.beacon")


class ExploratoryWaveBeacon:
    def __init__(
        self,
        beacon_id: str,
        target_system: str,
        ledger: Optional[ToroidalLedger] = None,
        signer: Optional[InterstellarPQCManager] = None,
    ):
        self.beacon_id = beacon_id
        self.target_system = target_system
        self.genesis_anchor = GENESIS_ANCHOR
        self.carrier_frequency_hz = 432.0
        self.ledger = ledger
        self.signer = signer
        self.last_result: dict[str, Any] | None = None

    def map_gravitational_topography(self, sensor_readouts: dict) -> str:
        """Pings local space with 432 Hz acoustic sweeps and measures stellar density responses."""
        mass_density = sensor_readouts.get("gravitational_gradient", 0.0)
        ion_flux = sensor_readouts.get("ion_flux_ev", 0.0)

        log.info(
            "[%s] Emitting 432 Hz acoustic standing wave across %s...",
            self.beacon_id,
            self.target_system,
        )
        log.info("Backscatter received: Gradient %s | Ion Flux %s eV", mass_density, ion_flux)

        checksum = anchored_hash(sensor_readouts, self.genesis_anchor)
        cell = preferred_cell(checksum)
        record = {
            "beacon_id": self.beacon_id,
            "target_system": self.target_system,
            "carrier_frequency_hz": self.carrier_frequency_hz,
            "sensor_readouts": sensor_readouts,
            "checksum": checksum,
        }
        committed = None
        if self.ledger is not None and self.signer is not None:
            committed = self.ledger.commit("beacon_deployment", record, self.signer)
            cell = committed["cell"]
        log.info(
            "CHART MAPPED: Spatial node anchored to Toroidal Ledger [Checksum: %s | Cell: %05d]",
            checksum[:8],
            cell,
        )
        preferred = committed["preferred_cell"] if committed is not None else preferred_cell(checksum)
        self.last_result = {
            "checksum": checksum,
            "payload_hash": committed["payload_hash"] if committed is not None else checksum,
            "cell": cell,
            "preferred_cell": preferred,
            "entry": committed,
            "sensor_readouts": sensor_readouts,
        }
        return checksum

    def map_gravitational_anomaly(self, sensor_readouts: dict) -> str:
        """Alias for compatibility with /workspace/code/beacons/beacon_mapping.py."""
        return self.map_gravitational_topography(sensor_readouts)

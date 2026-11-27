"""Configured reference-value check (formerly "ForensicAnchor").

Copyright (c) 2026 Jay Vallea. All Rights Reserved.

Honest description: this compares fields in a request against values the
operator configured in the environment. It is a consistency check, not an
audit, and it proves nothing about the underlying asset or report.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .config import ReferenceConfig

NOTE = "Configured reference-value match only; not an audit or proof of any real-world fact."


@dataclass(frozen=True)
class ReferenceResult:
    ok: bool
    status: str  # "passed" | "failed" | "not_configured"
    reason: str | None = None

    def to_json(self) -> dict:
        d = {"status": self.status, "note": NOTE}
        if self.reason:
            d["reason"] = self.reason
        return d


def check_reference(
    cfg: ReferenceConfig,
    control_variable: str | None,
    report_id: str | None,
    valuation_cad: Decimal | None,
) -> ReferenceResult:
    if not cfg.enabled:
        return ReferenceResult(True, "not_configured")
    if cfg.control_variable is not None and control_variable != cfg.control_variable:
        return ReferenceResult(False, "failed", "control_variable does not match configured value")
    if cfg.report_id is not None and report_id != cfg.report_id:
        return ReferenceResult(False, "failed", "report_id does not match configured value")
    if cfg.valuation_cad is not None:
        if valuation_cad is None:
            return ReferenceResult(False, "failed", "valuation_cad is required")
        if abs(valuation_cad - cfg.valuation_cad) > cfg.valuation_tolerance:
            return ReferenceResult(False, "failed", "valuation_cad does not match configured value")
    return ReferenceResult(True, "passed")

"""
Validation layer.

Validation runs AFTER an extractor produces a candidate value but BEFORE
the value is committed to a DataPoint.  If validation fails the value is
rejected, the failure is recorded as the DataPoint's error, and no value
is returned.  There is no "soft pass" — values that don't validate don't
go into the table.
"""

from __future__ import annotations
from typing import Optional
from .models import METRICS


class ValidationFailure(Exception):
    """Raised by validate_value when a candidate value fails a rule."""

    def __init__(self, reason: str, candidate: Optional[float] = None):
        super().__init__(reason)
        self.reason = reason
        self.candidate = candidate


def validate_value(metric: str, value: Optional[float], unit: str) -> float:
    """Return the value if it passes all rules, otherwise raise
    ValidationFailure.  Never silently substitutes."""

    meta = METRICS.get(metric)
    if not meta:
        raise ValidationFailure(f"unknown metric: {metric}", value)

    if value is None:
        raise ValidationFailure("value is None", value)

    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValidationFailure(f"value is not numeric: {value!r}", value)

    if v != v:  # NaN check
        raise ValidationFailure("value is NaN", value)

    expected_unit = meta["unit"]
    if unit != expected_unit:
        raise ValidationFailure(
            f"unit mismatch: got {unit!r}, expected {expected_unit!r}", v
        )

    if v < meta["plausible_min"]:
        raise ValidationFailure(
            f"value {v} below plausible min {meta['plausible_min']} for {metric}", v
        )
    if v > meta["plausible_max"]:
        raise ValidationFailure(
            f"value {v} above plausible max {meta['plausible_max']} for {metric}", v
        )

    return v


def cross_check_against_peers(
    metric: str, company: str, value: float, peer_values: list[float]
) -> Optional[str]:
    """Optional second pass: flag values that are extreme relative to peers.
    Returns a warning string or None.  Never modifies the value — this is
    advisory only and surfaces in the audit, not in validation rejection.
    """
    if len(peer_values) < 3:
        return None
    sorted_peers = sorted(peer_values)
    median = sorted_peers[len(sorted_peers) // 2]
    if median <= 0:
        return None
    ratio = value / median
    # Known structural exception: integrated generators (Duke, Southern,
    # Dominion) emit ~10× more Scope 1 CO₂ than pure distributors.  Don't
    # flag carbon outliers — the audit step in ai_layer handles structural
    # context.
    if metric == "carbon_emissions":
        return None
    if ratio > 5.0:
        return f"{value} is {ratio:.1f}× peer median ({median}); review for unit mismatch"
    if ratio < 0.2 and ratio > 0:
        return f"{value} is only {ratio:.1f}× peer median ({median}); may be partial / wrong scope"
    return None

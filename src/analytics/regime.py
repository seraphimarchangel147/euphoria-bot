"""Volatility regime, measured independently of our own model.

The regime label exists so calibration can learn separately for quiet and busy
tape. It only works if the label is independent of the prediction. It was not:
the bucket came from our own fitted sigma, the same number that drives the
probability, so conditioning on a bucket selected the moments our sigma read
high or low. The buckets then had to be miscalibrated by construction, and the
measured error was severe -- `p9|MED` predicted 0.26 and delivered 0.066.

The house publishes its own volatility estimate in every quote frame. It is
produced by a different model with far more history, it never sees our sigma,
and it is therefore a legitimate conditioning variable.

Thresholds are quantiles of the house's own recent history rather than fixed
numbers, because the absolute scale of that figure is not documented and would
drift anyway. Until enough history exists to place a quantile, the regime is
reported as unknown and callers fall back -- a wrong label is worse than no
label, since it silently splits one honest bucket into two dishonest ones.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

KEEP = 4000
# Enough samples that a tercile means something. Below this, no label.
MIN_SAMPLES = 120
LOW_Q, HIGH_Q = 1.0 / 3.0, 2.0 / 3.0
UNKNOWN = "UNKNOWN"
BUCKETS = ("LOW", "MED", "HIGH", UNKNOWN)


def _quantile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return sorted_values[lo] * (1.0 - frac) + sorted_values[hi] * frac


@dataclass
class VolRegime:
    """Terciles of the house's own published volatility."""

    samples: deque = field(default_factory=lambda: deque(maxlen=KEEP))
    last_value: float | None = None
    last_bucket: str = UNKNOWN
    updated_at: float = 0.0

    def observe(self, value: Any, *, now: float | None = None) -> None:
        try:
            vol = float(value)
        except (TypeError, ValueError):
            return
        if not (vol > 0) or vol != vol or vol in (float("inf"), float("-inf")):
            return
        self.samples.append(vol)
        self.last_value = vol
        self.updated_at = time.time() if now is None else now

    @property
    def ready(self) -> bool:
        return len(self.samples) >= MIN_SAMPLES

    def thresholds(self) -> tuple[float, float] | None:
        if not self.ready:
            return None
        ordered = sorted(self.samples)
        return _quantile(ordered, LOW_Q), _quantile(ordered, HIGH_Q)

    def bucket(self, value: Any = None) -> str:
        """Regime for `value` (defaults to the most recent reading)."""
        edges = self.thresholds()
        if edges is None:
            self.last_bucket = UNKNOWN
            return UNKNOWN
        try:
            vol = float(value) if value is not None else self.last_value
        except (TypeError, ValueError):
            vol = self.last_value
        if vol is None or not (vol > 0):
            self.last_bucket = UNKNOWN
            return UNKNOWN
        low, high = edges
        self.last_bucket = "LOW" if vol <= low else ("HIGH" if vol > high else "MED")
        return self.last_bucket

    def stats(self) -> dict[str, Any]:
        edges = self.thresholds()
        # Compute the label rather than report a cached one: a view that only
        # refreshes when something else happens to ask for a bucket will show
        # UNKNOWN long after the regime is known.
        current = self.bucket()
        return {
            "source": "house",
            "ready": self.ready,
            "samples": len(self.samples),
            "min_samples": MIN_SAMPLES,
            "value": round(self.last_value, 6) if self.last_value is not None else None,
            "bucket": current,
            "low_edge": round(edges[0], 6) if edges else None,
            "high_edge": round(edges[1], 6) if edges else None,
            "updated_at": self.updated_at,
        }

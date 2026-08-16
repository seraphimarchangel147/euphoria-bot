"""Rolling-window house-vol terciles. Sibling of the live VolRegime.

The live VolRegime takes terciles over the whole retained history
(KEEP=4000). When house vol trends, almost every new print sits above
the historical 2/3 quantile, so the label saturates on HIGH. Calibration
then dumps everything into HIGH and the LOW/MED buckets freeze.

This module does not replace regime.py (that file lives only on the
owner's machine). It is a pure sibling: same house source, same KEEP
cap, thresholds computed only on samples inside a wall-clock window.

WINDOW_S = 20 minutes
    Midpoint of the 15–30 minute band that is long enough for
    MIN_SAMPLES (120) at a ~10 s house-vol print, and short enough
    that a multi-hour drift cannot pin the 2/3 cut the way KEEP=4000
    history does. Time-based, not count-based, so a bursty observe
    rate does not silently shrink the lookback to a few seconds of
    panic prints. This is a prior, not a fit: no live shadow window
    was measured here.

Clock: every method that needs time takes ``now``. This module never
reads the wall clock.

Source is always ``"house"`` — never our sigma.
"""

from __future__ import annotations

from collections import deque
from statistics import quantiles
from typing import Deque

KEEP = 4000
MIN_SAMPLES = 120
WINDOW_S = 20 * 60  # 1200 s; see module docstring
SOURCE = "house"

LOW = "LOW"
MED = "MED"
HIGH = "HIGH"
UNKNOWN = "UNKNOWN"

_BUCKETS = (LOW, MED, HIGH)


def _assign(value: float, q1: float, q2: float) -> str:
    if value <= q1:
        return LOW
    if value <= q2:
        return MED
    return HIGH


class RollingVolRegime:
    """Tercile house-vol regime over a rolling wall-clock window."""

    KEEP = KEEP
    MIN_SAMPLES = MIN_SAMPLES
    WINDOW_S = WINDOW_S
    SOURCE = SOURCE

    def __init__(
        self,
        *,
        window_s: float = WINDOW_S,
        keep: int = KEEP,
        min_samples: int = MIN_SAMPLES,
    ) -> None:
        if window_s <= 0:
            raise ValueError(f"window_s must be positive, got {window_s}")
        if keep < 1:
            raise ValueError(f"keep must be >= 1, got {keep}")
        if min_samples < 2:
            # statistics.quantiles(n=3) needs at least two points
            raise ValueError(f"min_samples must be >= 2, got {min_samples}")
        self.window_s = float(window_s)
        self.keep = int(keep)
        self.min_samples = int(min_samples)
        self.source = SOURCE
        self._samples: Deque[tuple[float, float]] = deque(maxlen=self.keep)

    def observe(self, value: float, *, now: float) -> None:
        """Store ``(now, value)``. ``now`` is the only clock."""
        self._samples.append((float(now), float(value)))

    def _window(self, now: float) -> list[tuple[float, float]]:
        now = float(now)
        cutoff = now - self.window_s
        # Closed interval [now - WINDOW_S, now]. The lower bound is the
        # bug fix (drop older KEEP history). The upper bound is causality:
        # a stamp after `now` is look-ahead, not a regime.
        return [(ts, v) for ts, v in self._samples if cutoff <= ts <= now]

    def _values(self, now: float) -> list[float]:
        return [v for _, v in self._window(now)]

    def thresholds(self, *, now: float) -> tuple[float, float] | None:
        """1/3 and 2/3 cuts of samples with ``now - WINDOW_S <= ts <= now``.

        Returns None until the rolling window has MIN_SAMPLES points.
        Whole-history samples older than the window are ignored even
        if they are still inside the KEEP cap.
        """
        values = self._values(now)
        if len(values) < self.min_samples:
            return None
        q1, q2 = quantiles(values, n=3)
        return (q1, q2)

    def bucket(self, value: float, *, now: float) -> str:
        """Label ``value`` against the rolling-window terciles.

        UNKNOWN until the rolling window is full. A wrong label is
        worse than no label.

        Note: on a strictly monotone series the newest print is always
        the window max, so the *latest* label is still HIGH. That is
        not the saturation bug. The bug is whole-history cuts labelling
        the entire recent window HIGH. Use ``stats()`` for occupancy.
        """
        cuts = self.thresholds(now=now)
        if cuts is None:
            return UNKNOWN
        return _assign(value, cuts[0], cuts[1])

    def stats(self, *, now: float) -> dict[str, float | int | str]:
        """Window occupancy so saturation is visible later.

        Exposes ``window_s``, ``window_n``, ``source``, and the
        fraction of in-window samples in LOW / MED / HIGH. Fractions
        are 0.0 while the window is short of MIN_SAMPLES (UNKNOWN).
        """
        window = self._window(now)
        n = len(window)
        out: dict[str, float | int | str] = {
            "window_s": self.window_s,
            "window_n": n,
            "source": self.source,
            LOW: 0.0,
            MED: 0.0,
            HIGH: 0.0,
        }
        cuts = self.thresholds(now=now)
        if cuts is None or n == 0:
            return out
        q1, q2 = cuts
        counts = {LOW: 0, MED: 0, HIGH: 0}
        for _, value in window:
            counts[_assign(value, q1, q2)] += 1
        for name in _BUCKETS:
            out[name] = counts[name] / n
        return out

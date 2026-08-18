"""The winner's curse, measured on the cells we actually picked.

The ranking is not the problem. Over 111,381 labelled cells the rank
correlation between predicted and realised is **+0.996** -- the model orders the
grid almost perfectly. The problem is the *level*, and specifically the level of
the one cell we act on.

We take the argmax of EV over ~180 quoted cells. Even if every per-cell
probability were unbiased, the maximum of N noisy estimates is biased upward by
roughly ``s * mu_N``, where ``mu_N ~ sqrt(2 log N)``. At N=200 with per-cell
error s = 0.08-0.12 that is +0.21 to +0.31 in absolute probability -- enough to
turn a true 0.35 into a displayed 0.60. Our own reliability table reads
predicted 0.60 -> actual 0.365. Two routes, same number.

That is why per-bucket calibration does not rescue this. The calibrator is right
*on average over all cells*, but the cell we bet is not an average draw. It is
an extreme order statistic, and conditioning on "this was the best cell on the
board" is exactly the conditioning the calibrator never sees.

**Why this measures the bias rather than computing it.** The closed form assumes
independent errors. Ours are not: every cell in a window rides one price path,
so the effective N is far below the cell count and ``mu_N`` overstates the
correction. Rather than model that dependence, this records what actually
happened to the cells we ranked first and lets the answer come out of the tape.

**Why it is keyed on rank, not on probability.** A cell at p=0.6 that ranked
20th and a cell at p=0.6 that ranked 1st are different bets: only the second one
had to win a maximisation to get there. Rank is the conditioning variable that
carries the curse.

It can only ever lower a probability. Everything in this codebase that has cost
money did so by making the numbers look better (AGENTS.md section 2), so a
correction that can only subtract cannot join that list.
"""
from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

# Rank bands. Rank 0 is the cell we would actually bet; the rest are kept as a
# control -- if the shrink is real it should fade as rank rises, and a flat
# profile across bands means we are measuring something other than selection.
BANDS = ((0, 1), (1, 4), (4, 16))
KEEP = 20_000
# Below this a band has no opinion and returns the incoming probability
# untouched. Thin evidence here would be a haircut invented from noise.
MIN_OBSERVATIONS = 200
# Never shrink by more than this in one step. A band that claims the model is
# wrong by more than half is more likely to be a broken feed than a discovery.
MAX_SHRINK = 0.5


@dataclass
class SelectionBias:
    """Predicted-minus-realised for cells, split by how they ranked."""

    seen: dict[int, list[float]] = field(
        default_factory=lambda: defaultdict(lambda: [0.0, 0.0, 0.0])
    )
    recent: deque = field(default_factory=lambda: deque(maxlen=KEEP))

    @staticmethod
    def band_of(rank: int) -> int | None:
        for i, (lo, hi) in enumerate(BANDS):
            if lo <= rank < hi:
                return i
        return None

    def observe(self, p_model: float, rank: int, touched: bool) -> None:
        band = self.band_of(int(rank))
        if band is None:
            return
        try:
            p = float(p_model)
        except (TypeError, ValueError):
            return
        if not (0.0 <= p <= 1.0):
            return
        cell = self.seen[band]
        cell[0] += p                      # sum of predicted
        cell[1] += 1.0 if touched else 0.0   # sum of realised
        cell[2] += 1.0                    # n
        self.recent.append((band, p, bool(touched)))

    def observe_many(self, rows: Any) -> int:
        n = 0
        for row in rows or ():
            try:
                p_model, rank, touched = row
            except (TypeError, ValueError):
                continue
            self.observe(p_model, rank, touched)
            n += 1
        return n

    def bias(self, band: int) -> float | None:
        """Mean(predicted) - mean(realised) for this band. None = no opinion."""
        pred, hit, n = self.seen.get(band, (0.0, 0.0, 0.0))
        if n < MIN_OBSERVATIONS:
            return None
        return (pred / n) - (hit / n)

    def adjust(self, p_model: float, rank: int) -> float:
        """Shrink a probability by the bias measured at its own rank.

        One-directional by construction: a band whose realised rate came in
        *above* prediction is not evidence that the next pick will, so a
        negative bias is floored at zero rather than used as a lift.
        """
        band = self.band_of(int(rank))
        if band is None:
            return p_model
        b = self.bias(band)
        if b is None or b <= 0:
            return p_model
        shrink = min(b, MAX_SHRINK)
        return max(0.0, min(1.0, p_model - shrink))

    def stats(self) -> dict[str, Any]:
        rows = []
        for i, (lo, hi) in enumerate(BANDS):
            pred, hit, n = self.seen.get(i, (0.0, 0.0, 0.0))
            rows.append({
                "band": f"{lo}-{hi - 1}" if hi - lo > 1 else str(lo),
                "n": int(n),
                "predicted": round(pred / n, 5) if n else None,
                "realised": round(hit / n, 5) if n else None,
                "bias": round(self.bias(i), 5) if self.bias(i) is not None else None,
                "warm": n >= MIN_OBSERVATIONS,
            })
        return {"bands": rows, "min_observations": MIN_OBSERVATIONS,
                "max_shrink": MAX_SHRINK}

    # -- persistence --------------------------------------------------------
    def to_json(self) -> dict[str, Any]:
        return {"version": 1,
                "seen": [[b, p, h, n] for b, (p, h, n) in self.seen.items()]}

    @classmethod
    def from_json(cls, data: Any) -> "SelectionBias":
        out = cls()
        rows = (data or {}).get("seen") if isinstance(data, dict) else None
        for row in rows or ():
            try:
                b, p, h, n = int(row[0]), float(row[1]), float(row[2]), float(row[3])
            except (TypeError, ValueError, IndexError):
                continue
            if n <= 0 or h < 0 or h > n or p < 0 or p > n:
                continue
            out.seen[b] = [p, h, n]
        return out

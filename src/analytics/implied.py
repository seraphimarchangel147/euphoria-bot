"""Read volatility off the house's own quote grid instead of our tick tape.

Our fitted sigma was measured at 0.0105 while the house's grid, at the same
instant, was pricing about 0.199 -- roughly **19x** apart. That is not a noisy
estimate, it is a broken one, and it collapsed the whole probability surface:
`p_model` came out exactly 1.0 at distance 0 and exactly 0.0 at distance 1, with
no gradient anywhere between, while the house priced a smooth curve across the
same cells.

The decisive check needed no model at all. For distance-1 cells the house
implied 0.10-0.15, the reachability ledger had **observed 0.154** over 1006
graded cells, and we were saying 0.0000. Two independent sources agreed with
each other and not with us.

So stop fitting sigma from our own ticks and hoping. The house publishes an
implied probability for every cell of the grid every five seconds, from a model
with far more history than ours, and it is precisely the number that prices the
bet we are about to make. This is the same move that fixed the volatility regime
label -- take the house's number rather than our own -- applied one level down,
to the value rather than the bucket.

**How the anchor is derived matters.** Rather than invert a textbook barrier
formula and hope it matches, this solves for the sigma at which *our own*
`band_touch_prob` reproduces the house's prices. The anchor therefore lands in
our model's units and any quirk of our formula cancels out instead of being
smuggled in as a bias.

Two properties worth knowing before trusting it:

* **It is biased HIGH, which is the dangerous direction, so it needs a
  backstop.** The house profits when `p_true * m < 1`, i.e. when `p_true <
  1/m`, so the implied probability sits *above* the truth by roughly their
  margin, and the sigma that reproduces it sits above true sigma. Anchoring
  here therefore overstates how far price can travel and makes far cells look
  more reachable than they are -- the exact failure this project has paid for
  repeatedly. It is still enormously better than a fit that is 19x low, but it
  is not conservative and must not be treated as such. Two things catch it: the
  reachability ledger vetoes on measured touch rates rather than any model, and
  the calibrator shrinks toward observed frequency. Neither is optional while
  this anchor is in use.
* **It only speaks when the grid is informative.** At-price cells barely move
  with sigma and saturated quotes carry no information, so both are excluded. If
  too few usable cells remain, this returns None and the caller keeps whatever
  it had.
"""
from __future__ import annotations

from typing import Any, Sequence

from src.analytics.forecast import band_touch_prob

# A quote this thin or this fat tells us nothing: the first is priced at
# near-certainty, the second is a lottery whose probability is swamped by the
# house's margin.
MIN_HOUSE_P = 0.02
MAX_HOUSE_P = 0.90
# At-price cells are almost insensitive to sigma -- the band already contains
# the price, so the probability stays near 1 across a wide range of sigma and
# the solve learns nothing from them.
MIN_EDGE_CELLS = 0.15
# Below this there is not enough of the grid to trust a median.
MIN_CELLS = 6
# Search range for the solve. Wide enough to contain both our 0.01 and the
# house's 0.2 with room either side, and deliberately capped well below the
# region where the objective stops behaving (see below).
SIGMA_LO, SIGMA_HI = 1e-4, 2.0
SCAN = 48          # log-spaced coarse points
REFINE = 24        # golden-section steps inside the winning interval
# Cap the work per pass. The most informative cells are the ones the house
# prices near the middle of the range; a cell quoted at 0.03 or 0.85 barely
# moves as sigma changes and costs the same to evaluate.
MAX_CELLS = 24
MOST_INFORMATIVE_P = 0.30
# How close the solve must get to the house's prices before we call it a
# measurement rather than a least-bad miss.
MAX_RESIDUAL = 0.05


def _house_p(multiplier: Any) -> float | None:
    try:
        m = float(multiplier)
    except (TypeError, ValueError):
        return None
    if m <= 1.0:
        return None
    p = 1.0 / m
    return p if MIN_HOUSE_P <= p <= MAX_HOUSE_P else None


def usable_cells(cells: Sequence[Any], *, price: float) -> list[tuple[float, float, float, float, float]]:
    """(lo_offset, hi_offset, t_start, t_end, house_p) for cells with signal."""
    out: list[tuple[float, float, float, float, float]] = []
    for c in cells or ():
        hp = _house_p(getattr(c, "multiplier", None))
        if hp is None:
            continue
        try:
            lo = float(c.lo) - price
            hi = float(c.hi) - price
            t_start = float(c.t_start)
            t_end = float(c.t_end)
        except (TypeError, ValueError, AttributeError):
            continue
        if t_end <= 0 or hi <= lo:
            continue
        # Distance from the price to the near edge of the band, in cells.
        edge = abs(getattr(c, "edge_cells", 0.0) or 0.0)
        if edge < MIN_EDGE_CELLS:
            continue
        out.append((lo, hi, t_start, t_end, hp))
    return out


def implied_sigma(cells: Sequence[Any], diffusion: Any, *, price: float) -> dict[str, Any]:
    """Sigma at which our own model reproduces the house's quoted prices.

    Bisects on the median signed error across the usable grid, so a handful of
    oddly-priced cells cannot drag the answer the way a mean would.
    """
    rows = usable_cells(cells, price=price)
    if len(rows) < MIN_CELLS:
        return {"sigma": None, "cells": len(rows), "reason": "grid too thin"}
    # Keep the cells that discriminate best, so the cost per pass is bounded.
    rows.sort(key=lambda r: abs(r[4] - MOST_INFORMATIVE_P))
    rows = rows[:MAX_CELLS]

    def median_error(sigma: float) -> float:
        errs = []
        for lo, hi, t_start, t_end, hp in rows:
            probe = _with_sigma(diffusion, sigma)
            errs.append(band_touch_prob(lo, hi, t_start, t_end, probe) - hp)
        errs.sort()
        mid = len(errs) // 2
        return errs[mid] if len(errs) % 2 else 0.5 * (errs[mid - 1] + errs[mid])

    # Bisection would be the obvious solve and it is *unsound here*: touch
    # probability is not monotonic in sigma. For a narrow band in a future
    # window it peaks and then falls again -- at enormous volatility the price
    # is spread so wide by the time the column opens that it is unlikely to be
    # anywhere near a half-dollar band. Measured on one cell: p rises 0.00 ->
    # 0.396 as sigma goes 1e-5 -> 1.0, then *decreases* to 0.376 by sigma 20.
    # So minimise |median error| over a scan instead of bracketing a sign
    # change, and keep the range below the turn.
    import math

    def loss(sigma: float) -> float:
        return abs(median_error(sigma))

    lo_l, hi_l = math.log(SIGMA_LO), math.log(SIGMA_HI)
    best_i, best = 0, float("inf")
    grid = [math.exp(lo_l + (hi_l - lo_l) * i / (SCAN - 1)) for i in range(SCAN)]
    for i, s in enumerate(grid):
        v = loss(s)
        if v < best:
            best_i, best = i, v

    lo_s = grid[max(0, best_i - 1)]
    hi_s = grid[min(SCAN - 1, best_i + 1)]
    phi = 0.5 * (math.sqrt(5.0) - 1.0)
    a, b = math.log(lo_s), math.log(hi_s)
    c, d = b - phi * (b - a), a + phi * (b - a)
    fc, fd = loss(math.exp(c)), loss(math.exp(d))
    for _ in range(REFINE):
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - phi * (b - a)
            fc = loss(math.exp(c))
        else:
            a, c, fc = c, d, fd
            d = a + phi * (b - a)
            fd = loss(math.exp(d))
    sigma = math.exp(0.5 * (a + b))
    residual = median_error(sigma)

    # An argmin is not a fit. Two ways this "succeeds" while having measured
    # nothing, both seen live within nine minutes of switching it on:
    #
    # 1. **It lands on the boundary.** Because touch probability peaks and then
    #    falls, a grid our model simply cannot reproduce has no interior
    #    minimum, and the scan slides to the end of its own range. Observed
    #    anchor values of exactly 2.00000 -- SIGMA_HI to five decimals -- which
    #    is a search bound reported as a measurement.
    # 2. **It stops far from the house's prices.** If half the selected cells
    #    are unreachable at any sigma, the median error never crosses zero and
    #    the minimum is merely the least-bad miss.
    #
    # Either way the honest answer is "no anchor this pass", and the caller
    # falls back to the tick fit. Anchoring to a bound produced a 54.5x range
    # with a median step of 3.61x -- worse than the estimator it replaced.
    if abs(residual) > MAX_RESIDUAL:
        return {"sigma": None, "cells": len(rows), "residual": round(residual, 5),
                "reason": "no match"}
    if sigma <= SIGMA_LO * 1.01 or sigma >= SIGMA_HI * 0.99:
        return {"sigma": None, "cells": len(rows), "residual": round(residual, 5),
                "reason": "at bound"}
    return {"sigma": sigma, "cells": len(rows), "residual": round(residual, 5),
            "reason": ""}


def _with_sigma(diffusion: Any, sigma: float) -> Any:
    from dataclasses import replace
    return replace(diffusion, sigma=sigma, ok=True)

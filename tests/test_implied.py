"""Sigma comes off the house's grid, not our tick tape. No network.

Our fitted sigma read 0.0105 while the house's grid at the same instant priced
about 0.199 -- roughly 19x apart. That is not a noisy estimate, it is a broken
one, and it collapsed the surface: p_model came out exactly 1.0 at distance 0
and exactly 0.0 at distance 1, with no gradient in between, while the house
priced a smooth curve across those same cells.

The decisive check needed no model. At distance 1 the house implied 0.10-0.15,
the reachability ledger had observed 0.154 over 1006 graded cells, and we were
saying 0.0000. Two independent sources agreed with each other and not with us.
"""
from src.analytics.forecast import Diffusion, band_touch_prob
from src.analytics.implied import (
    MIN_CELLS,
    MIN_EDGE_CELLS,
    implied_sigma,
    usable_cells,
)


class Cell:
    """Just enough of a CellForecast for the solver."""

    def __init__(self, lo, hi, t_start, t_end, multiplier, edge_cells=1.0):
        self.lo, self.hi = lo, hi
        self.t_start, self.t_end = t_start, t_end
        self.multiplier, self.edge_cells = multiplier, edge_cells


def _diff(sigma=0.05, hurst=0.30):
    return Diffusion(mu=0.0, mu_raw=0.0, sigma=sigma, pairs=100, span_s=45.0,
                     ok=True, hurst=hurst, hurst_pairs=40, hurst_fitted_to_s=32.0)


def _grid_priced_at(true_sigma, price=1000.0):
    """A quote grid generated from a known sigma, so the answer is checkable.

    Swept across distance AND horizon rather than pairing them, because pairing
    walks straight out of the money: every cell lands deep in the tail and the
    grid carries no information at any sigma.
    """
    d = _diff(sigma=true_sigma)
    cells = []
    for dist in (0.25, 0.5, 0.75, 1.0, 1.5, 2.0):
        for t_start in (5.0, 10.0, 20.0, 30.0, 45.0, 60.0):
            p = band_touch_prob(dist, dist + 0.5, t_start, t_start + 5.0, d)
            if not (0.02 <= p <= 0.90):
                continue
            cells.append(Cell(price + dist, price + dist + 0.5, t_start,
                              t_start + 5.0, multiplier=1.0 / p,
                              edge_cells=dist / 0.5))
    return cells


# --- it recovers a sigma it was given ------------------------------------
def test_it_recovers_the_sigma_the_grid_was_priced_with():
    for true_sigma in (0.08, 0.2, 0.5):
        cells = _grid_priced_at(true_sigma)
        out = implied_sigma(cells, _diff(sigma=0.0105), price=1000.0)
        assert out["sigma"] is not None, out["reason"]
        assert abs(out["sigma"] / true_sigma - 1.0) < 0.10, (true_sigma, out)


def test_a_genuinely_dead_market_yields_no_anchor_rather_than_a_guess():
    """At sigma 0.02 on a $0.50 grid, the nearest band is eight sigma out and
    every quote saturates, so there is nothing to solve against. Reporting
    "too thin" is correct -- inventing a number from saturated quotes is how
    a bad anchor would get in."""
    out = implied_sigma(_grid_priced_at(0.02), _diff(), price=1000.0)
    assert out["sigma"] is None
    assert "thin" in out["reason"]


def test_the_answer_does_not_depend_on_the_sigma_we_walked_in_with():
    """The tick fit must not leak into the anchor -- that is the bias we are
    trying to get out from under."""
    cells = _grid_priced_at(0.15)
    a = implied_sigma(cells, _diff(sigma=0.001), price=1000.0)["sigma"]
    b = implied_sigma(cells, _diff(sigma=5.0), price=1000.0)["sigma"]
    assert abs(a / b - 1.0) < 0.01


def test_it_finds_the_live_gap():
    """The measured case: a grid the house prices near 0.2 while our fit says
    0.0105. The anchor must report the house's number, not ours."""
    cells = _grid_priced_at(0.199)
    out = implied_sigma(cells, _diff(sigma=0.0105), price=1000.0)
    assert out["sigma"] > 0.1
    assert out["sigma"] / 0.0105 > 10


# --- it refuses to answer without an informative grid --------------------
def test_no_answer_from_too_few_cells():
    out = implied_sigma(_grid_priced_at(0.1)[:MIN_CELLS - 1], _diff(), price=1000.0)
    assert out["sigma"] is None
    assert "thin" in out["reason"]


def test_no_answer_from_an_empty_grid():
    for empty in ((), None, []):
        assert implied_sigma(empty, _diff(), price=1000.0)["sigma"] is None


def test_at_price_cells_are_excluded_because_they_carry_no_signal():
    """A band already containing the price stays near certainty across a wide
    range of sigma, so it cannot discriminate."""
    flat = [Cell(1000.0, 1000.5, 5.0, 10.0, 1.05, edge_cells=0.0) for _ in range(20)]
    assert usable_cells(flat, price=1000.0) == []
    assert implied_sigma(flat, _diff(), price=1000.0)["sigma"] is None


def test_saturated_quotes_are_excluded():
    """1.01x is priced at near-certainty and 200x is swamped by the margin."""
    junk = [Cell(1001.0, 1001.5, 5.0, 10.0, m, edge_cells=2.0)
            for m in (1.005, 1.01, 200.0, 5000.0)] * 5
    assert usable_cells(junk, price=1000.0) == []


def test_unquoted_and_malformed_cells_are_skipped():
    bad = [Cell(1001.0, 1001.5, 5.0, 10.0, m, edge_cells=2.0)
           for m in (None, "x", 0.0, 1.0, -3.0)]
    assert usable_cells(bad, price=1000.0) == []


def test_a_closed_or_inverted_cell_is_skipped():
    assert usable_cells([Cell(1001.0, 1001.5, -10.0, -5.0, 5.0, edge_cells=2.0)],
                        price=1000.0) == []
    assert usable_cells([Cell(1001.5, 1001.0, 5.0, 10.0, 5.0, edge_cells=2.0)],
                        price=1000.0) == []


def test_a_few_absurd_quotes_cannot_drag_the_answer():
    """Median, not mean: the grid occasionally carries a nonsense print."""
    cells = _grid_priced_at(0.15)
    clean = implied_sigma(cells, _diff(), price=1000.0)["sigma"]
    cells += [Cell(1000.5, 1001.0, 5.0, 10.0, 1.02, edge_cells=1.0),
              Cell(1000.5, 1001.0, 5.0, 10.0, 40.0, edge_cells=1.0)]
    dirty = implied_sigma(cells, _diff(), price=1000.0)["sigma"]
    assert abs(dirty / clean - 1.0) < 0.25


# --- the smoother carries the anchor -------------------------------------
def test_the_smoother_targets_the_anchor_when_one_exists():
    from src.analytics.forecast import DiffusionSmoother
    s = DiffusionSmoother()
    out = s.update(_diff(sigma=0.0105), now=1000.0, anchor=0.199)
    assert out.sigma == 0.199, "first reading is taken whole"
    assert out.raw_sigma == 0.0105, "our own fit stays visible"
    assert out.anchored is True


def test_the_anchor_is_smoothed_not_snapped():
    from src.analytics.forecast import DiffusionSmoother
    s = DiffusionSmoother(half_life_s=30.0)
    s.update(_diff(), now=1000.0, anchor=0.10)
    out = s.update(_diff(), now=1015.0, anchor=0.40)
    assert 0.10 < out.sigma < 0.40


def test_it_falls_back_to_our_own_fit_when_the_grid_is_silent():
    """No quotes, no anchor. The tick fit is worse but it is what we have."""
    from src.analytics.forecast import DiffusionSmoother
    s = DiffusionSmoother()
    out = s.update(_diff(sigma=0.033), now=1000.0, anchor=None)
    assert out.sigma == 0.033
    assert out.anchored is False


def test_a_nonsense_anchor_is_ignored():
    from src.analytics.forecast import DiffusionSmoother
    for bad in (0.0, -1.0):
        s = DiffusionSmoother()
        out = s.update(_diff(sigma=0.033), now=1000.0, anchor=bad)
        assert out.sigma == 0.033
        assert out.anchored is False


def test_stats_report_that_the_anchor_is_in_use():
    from src.analytics.forecast import DiffusionSmoother
    s = DiffusionSmoother()
    s.update(_diff(), now=1000.0, anchor=0.2)
    st = s.stats()
    assert st["anchored"] == 1
    assert st["last_anchor"] == 0.2


# --- the property that made bisection wrong ------------------------------
def test_touch_probability_is_not_monotonic_in_sigma():
    """Pinned because it is surprising and it invalidated the obvious solver.

    For a narrow band in a *future* window, probability rises with sigma, peaks,
    and then falls: at enormous volatility the price is spread so wide by the
    time the column opens that it is unlikely to be anywhere near a half-dollar
    band. Bisecting on a sign change assumes monotonicity and is unsound here,
    which is why the solve minimises |median error| over a scan instead.
    """
    band = dict(lo=1.0, hi=1.5, t_start=20.0, t_end=25.0)
    probs = [band_touch_prob(band["lo"], band["hi"], band["t_start"],
                             band["t_end"], _diff(sigma=s))
             for s in (1e-5, 0.08, 0.2, 1.0, 5.0, 20.0)]
    assert probs[0] < probs[2] < probs[3], "it rises through the useful range"
    assert probs[-1] < probs[3], "and falls again past the peak"


def test_the_solver_survives_the_non_monotonic_region():
    """A grid priced at a plausible sigma must not be matched by some absurd
    one on the far side of the peak."""
    cells = _grid_priced_at(0.2)
    out = implied_sigma(cells, _diff(sigma=5.0), price=1000.0)
    assert out["sigma"] < 1.0
    assert abs(out["residual"]) < 0.05


# --- an argmin is not a fit ----------------------------------------------
def test_a_grid_we_cannot_reproduce_yields_no_anchor():
    """Seen live within nine minutes of switching this on: the anchor reported
    exactly 2.00000, which is SIGMA_HI to five decimals -- a search bound
    reported as a measurement. Because touch probability peaks and falls, a
    grid our model cannot match has no interior minimum and the scan slides to
    the end of its own range. Anchoring to that produced a 54.5x range with a
    median step of 3.61x, worse than the estimator it replaced."""
    # Quotes claiming near-certainty on a distant band at a short horizon --
    # no sigma reproduces this, because raising sigma eventually lowers p.
    impossible = [Cell(1004.0, 1004.5, 2.0, 4.0, 1.15, edge_cells=8.0)
                  for _ in range(12)]
    out = implied_sigma(impossible, _diff(), price=1000.0)
    assert out["sigma"] is None
    assert out["reason"] in ("no match", "at bound")


def test_the_anchor_is_never_a_search_bound():
    from src.analytics.implied import SIGMA_HI, SIGMA_LO
    for cells in (_grid_priced_at(0.08), _grid_priced_at(0.2), _grid_priced_at(0.5)):
        s = implied_sigma(cells, _diff(), price=1000.0)["sigma"]
        if s is None:
            continue
        assert s > SIGMA_LO * 1.01 and s < SIGMA_HI * 0.99


def test_a_good_fit_still_reports_a_small_residual():
    out = implied_sigma(_grid_priced_at(0.2), _diff(), price=1000.0)
    assert out["sigma"] is not None
    assert abs(out["residual"]) < 0.01


def test_the_anchor_can_be_switched_off_without_editing_code():
    """The two arms of an A/B must run identical code, or the comparison is
    between two codebases rather than between two settings."""
    import importlib, os
    from config import settings
    prev = os.environ.get("EUPHORIA_HOUSE_ANCHOR")
    try:
        os.environ["EUPHORIA_HOUSE_ANCHOR"] = "0"
        importlib.reload(settings)
        assert settings.USE_HOUSE_ANCHOR is False
        os.environ["EUPHORIA_HOUSE_ANCHOR"] = "1"
        importlib.reload(settings)
        assert settings.USE_HOUSE_ANCHOR is True
    finally:
        if prev is None:
            os.environ.pop("EUPHORIA_HOUSE_ANCHOR", None)
        else:
            os.environ["EUPHORIA_HOUSE_ANCHOR"] = prev
        importlib.reload(settings)

"""Diffusion fit and touch-probability maths. No network."""
import math

from src.analytics.forecast import (
    Diffusion,
    band_touch_prob,
    estimate_diffusion,
    forecast_grid,
    hit_prob_down,
    hit_prob_up,
    neighbourhood,
    norm_cdf,
)
from src.analytics.signal import Tick


def _ticks(prices, t0=1000.0, step=0.5):
    return [Tick("ETH", p, t0 + i * step, source="test") for i, p in enumerate(prices)]


def _flat_tape(n=61, px=3000.0, t0=1000.0, step=0.5):
    """Deterministic zig-zag: known volatility, exactly zero drift."""
    return [Tick("ETH", px + (0.1 if i % 2 else -0.1), t0 + i * step, source="test")
            for i in range(n)]


# --- barrier law ----------------------------------------------------------
def test_driftless_barrier_matches_the_reflection_identity():
    """With no drift, P(max >= d) is exactly 2*P(X_T >= d)."""
    sigma, T = 1.0, 4.0
    for d in (0.5, 1.0, 2.0, 3.0):
        expected = 2.0 * (1.0 - norm_cdf(d / (sigma * math.sqrt(T))))
        assert abs(hit_prob_up(d, 0.0, sigma, T) - expected) < 1e-12


def test_barrier_is_monotone_in_distance_and_horizon():
    near = hit_prob_up(1.0, 0.0, 1.0, 4.0)
    far = hit_prob_up(3.0, 0.0, 1.0, 4.0)
    longer = hit_prob_up(3.0, 0.0, 1.0, 16.0)
    assert near > far
    assert longer > far


def test_upward_drift_helps_up_and_hurts_down():
    up = hit_prob_up(1.0, 0.4, 1.0, 4.0)
    flat = hit_prob_up(1.0, 0.0, 1.0, 4.0)
    down = hit_prob_down(1.0, 0.4, 1.0, 4.0)
    assert up > flat > down


def test_barrier_edges_are_sane():
    assert hit_prob_up(-1.0, 0.0, 1.0, 5.0) == 1.0      # already past it
    assert hit_prob_up(1.0, 0.0, 1.0, 0.0) == 0.0       # no time
    assert hit_prob_up(1.0, 0.0, 0.0, 5.0) == 0.0       # no volatility, no drift
    assert hit_prob_up(1.0, 1.0, 0.0, 5.0) == 1.0       # pure drift carries it


# --- band probability -----------------------------------------------------
def test_band_containing_the_current_price_is_certain_right_now():
    d = Diffusion(mu=0.0, mu_raw=0.0, sigma=1.0, pairs=10, span_s=10.0, ok=True)
    assert band_touch_prob(-0.5, 0.5, 0.0, 5.0, d) == 1.0


def test_a_closed_column_cannot_be_touched():
    d = Diffusion(mu=0.0, mu_raw=0.0, sigma=1.0, pairs=10, span_s=10.0, ok=True)
    assert band_touch_prob(1.0, 2.0, -10.0, -5.0, d) == 0.0


def test_further_columns_are_wider_but_not_automatically_likelier():
    """A distant band gets more diffusion time yet stays a low-probability cell."""
    d = Diffusion(mu=0.0, mu_raw=0.0, sigma=0.2, pairs=40, span_s=40.0, ok=True)
    near = band_touch_prob(0.5, 1.0, 0.0, 5.0, d)
    far = band_touch_prob(5.0, 5.5, 25.0, 30.0, d)
    assert 0.0 < far < near < 1.0


def test_band_probability_never_leaves_the_unit_interval():
    d = Diffusion(mu=0.9, mu_raw=1.0, sigma=0.4, pairs=30, span_s=30.0, ok=True)
    for lo in (-8.0, -1.0, 0.0, 1.0, 9.0):
        p = band_touch_prob(lo, lo + 0.5, 2.0, 7.0, d)
        assert 0.0 <= p <= 1.0


# --- diffusion fit --------------------------------------------------------
def test_fit_recovers_a_known_volatility_and_shrinks_noise_drift():
    ticks = _flat_tape()
    d = estimate_diffusion(ticks, now=ticks[-1].ts)
    assert d.ok
    # |dp| = 0.2 every 0.5s -> sigma^2 = 0.04/0.5 = 0.08
    assert abs(d.sigma - math.sqrt(0.08)) < 1e-9
    # An odd-length zig-zag starts and ends on the same price: no trend at all.
    assert abs(d.mu_raw) < 1e-12
    assert abs(d.mu) < 1e-12


def test_drift_shrink_is_weaker_than_the_raw_slope():
    ticks = _ticks([3000.0 + i * 0.1 for i in range(40)])
    d = estimate_diffusion(ticks, now=ticks[-1].ts)
    assert d.ok
    assert 0.0 < abs(d.mu) <= abs(d.mu_raw)


def test_too_few_ticks_reports_not_ok_rather_than_zero_probability():
    d = estimate_diffusion(_ticks([3000.0, 3000.1]), now=1002.0)
    assert not d.ok
    assert d.reason


def test_stale_ticks_fall_out_of_the_fit_window():
    old = _ticks([3000.0 + i for i in range(20)], t0=0.0)
    d = estimate_diffusion(old, now=100_000.0)
    assert not d.ok


def test_vol_bucket_tracks_cells_per_root_second():
    d = Diffusion(mu=0.0, mu_raw=0.0, sigma=0.02, pairs=20, span_s=20.0, ok=True)
    assert d.vol_bucket(0.5) == "LOW"
    assert Diffusion(0.0, 0.0, 0.1, 20, 20.0, True).vol_bucket(0.5) == "MED"
    assert Diffusion(0.0, 0.0, 1.0, 20, 20.0, True).vol_bucket(0.5) == "HIGH"


# --- the grid surface -----------------------------------------------------
def test_neighbourhood_covers_columns_rows_and_diagonals():
    cells = neighbourhood(100, 50, forward=3, radius=2)
    assert len(cells) == 3 * 5
    assert {c["cell_x"] for c in cells} == {101, 102, 103}
    assert {c["cell_y"] for c in cells} == {48, 49, 50, 51, 52}
    # a genuine diagonal: two columns out and two rows up
    assert any(c["cell_x"] == 102 and c["cell_y"] == 52 for c in cells)


def test_forecast_grid_prices_every_cell_with_timing_and_multipliers():
    d = Diffusion(mu=0.0, mu_raw=0.0, sigma=0.3, pairs=40, span_s=40.0, ok=True)
    cells = [
        {"cell_x": 11, "cell_y": 6001, "forward": 1, "row_offset": 1, "multiplier": 3.0},
        {"cell_x": 13, "cell_y": 6004, "forward": 3, "row_offset": 4, "multiplier": 40.0},
    ]
    out = forecast_grid(
        cells, price=3000.2, dpl=0.5, column_s=5.0,
        current_x=10, now_offset_s=2.0, diffusion=d,
    )
    assert len(out) == 2
    near, far = out
    assert near.lo == 6001 * 0.5 and near.hi == 6001 * 0.5 + 0.5
    assert near.t_start == 3.0 and near.t_end == 8.0     # 5 - 2 elapsed
    assert far.t_start == 13.0
    assert near.breakeven == round(1 / 3.0, 6)
    assert near.p_touch > far.p_touch                    # nearer and sooner
    assert far.side == "up" and far.distance == 4


def test_forecast_grid_refuses_nonsense_geometry():
    d = Diffusion(mu=0.0, mu_raw=0.0, sigma=0.3, pairs=9, span_s=9.0, ok=True)
    assert forecast_grid([{"cell_x": 1, "cell_y": 1}], price=0.0, dpl=0.5,
                         column_s=5.0, current_x=0, now_offset_s=0.0, diffusion=d) == []
    assert forecast_grid([{"cell_x": 1, "cell_y": 1}], price=100.0, dpl=0.0,
                         column_s=5.0, current_x=0, now_offset_s=0.0, diffusion=d) == []


def test_forecast_grid_reanchors_closed_cells_via_forward():
    """A live quote whose absolute cell_x already looks closed still prices."""
    d = Diffusion(mu=0.0, mu_raw=0.0, sigma=0.3, pairs=40, span_s=40.0, ok=True)
    cells = [{"cell_x": 1, "cell_y": 6000, "forward": 1, "row_offset": 0, "multiplier": 2.06}]
    out = forecast_grid(
        cells, price=3000.2, dpl=0.5, column_s=5.0,
        current_x=100, now_offset_s=1.0, diffusion=d,
    )
    assert len(out) == 1
    assert out[0].multiplier == 2.06
    assert out[0].t_end > 0
    assert out[0].forward == 1
    assert out[0].cell_x == 1

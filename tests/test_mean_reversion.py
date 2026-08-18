"""Displacement scaling. No network.

Price over 5-90 seconds is not a random walk. The house prices that directly:
its quotes imply a movement scale growing as roughly T**0.09, nearly flat in
time, which is the signature of mean reversion. Assuming T**0.5 while measuring
volatility correctly would overstate reach at long horizons and manufacture an
edge on far cells the tape never reaches.
"""
import math

from src.analytics.forecast import (
    DEFAULT_HURST,
    HURST_MAX,
    HURST_MIN,
    Diffusion,
    band_touch_prob,
    estimate_diffusion,
    estimate_hurst,
    hit_prob_up,
    spread,
)
from src.analytics.signal import Tick


def _walk(steps, *, px=1000.0, t0=0.0, dt=0.25, source="page"):
    """Build a tape from an explicit list of per-step increments."""
    ticks = [Tick("ETH", px, t0, source=source)]
    for i, step in enumerate(steps, start=1):
        px += step
        ticks.append(Tick("ETH", px, t0 + i * dt, source=source))
    return ticks


def _random_walk(n=1200, *, seed=7, size=0.05, dt=0.25):
    """Deterministic pseudo-random walk: displacement should grow as sqrt(T)."""
    state = seed
    steps = []
    for _ in range(n):
        state = (1103515245 * state + 12345) % 2147483648
        steps.append(size if (state >> 16) & 1 else -size)
    return _walk(steps, dt=dt)


def _reverting(n=1200, *, seed=11, size=0.05, dt=0.25, theta=0.15, px=1000.0):
    """Ornstein-Uhlenbeck: shocks decay, so displacement saturates with time.

    Deliberately not strict alternation -- that has exactly zero displacement
    at every even lag, which is a degenerate signal rather than a market.
    """
    state = seed
    x = 0.0
    ticks = [Tick("ETH", px, 0.0, source="page")]
    for i in range(1, n + 1):
        state = (1103515245 * state + 12345) % 2147483648
        shock = size if (state >> 16) & 1 else -size
        x = x * (1.0 - theta) + shock
        ticks.append(Tick("ETH", px + x, i * dt, source="page"))
    return ticks


# --- the spread function --------------------------------------------------
def test_hurst_one_half_reproduces_brownian_scaling():
    assert spread(2.0, 9.0, 0.5) == 6.0                      # 2 * sqrt(9)
    assert spread(1.0, 100.0, 0.5) == 10.0


def test_a_lower_exponent_travels_less_as_the_horizon_grows():
    near_brownian = spread(1.0, 90.0, 0.5)
    reverting = spread(1.0, 90.0, 0.1)
    assert reverting < near_brownian
    # At one second the two agree by construction; they only diverge with time.
    assert spread(1.0, 1.0, 0.1) == spread(1.0, 1.0, 0.5) == 1.0


def test_spread_is_zero_at_zero_horizon():
    assert spread(1.0, 0.0, 0.3) == 0.0
    assert spread(1.0, -5.0, 0.3) == 0.0


# --- measuring the exponent ----------------------------------------------
def test_a_random_walk_measures_close_to_one_half():
    h, pairs, fitted = estimate_hurst(_random_walk())
    assert pairs > 0
    assert fitted > 0
    assert 0.38 < h < 0.62, f"expected ~0.5 for a random walk, got {h}"


def test_a_mean_reverting_tape_measures_well_below_one_half():
    h, _, _ = estimate_hurst(_reverting())
    assert h < 0.3, f"expected strong reversion, got {h}"


def test_the_reverting_tape_scores_lower_than_the_walk():
    walk, _, _ = estimate_hurst(_random_walk())
    revert, _, _ = estimate_hurst(_reverting())
    assert revert < walk


def test_a_thin_tape_falls_back_to_brownian_rather_than_guessing():
    h, pairs, fitted = estimate_hurst([Tick("ETH", 1000.0, float(i)) for i in range(3)])
    assert h == DEFAULT_HURST
    assert pairs == 0 and fitted == 0.0


def test_the_exponent_is_clamped_to_a_sane_band():
    # A pure ramp has displacement growing linearly (H -> 1); clamp holds.
    ramp = _walk([0.05] * 1200)
    h, _, _ = estimate_hurst(ramp)
    assert HURST_MIN <= h <= HURST_MAX


def test_the_fit_reports_how_far_it_was_actually_measured():
    ticks = _reverting()
    diff = estimate_diffusion(ticks, now=ticks[-1].ts)
    assert diff.hurst_fitted_to_s > 0
    assert diff.hurst_fitted_to_s <= 32.0


# --- extrapolation beyond the evidence ------------------------------------
def test_beyond_the_fitted_range_the_model_widens_rather_than_narrows():
    """Compounding strong mean reversion past its evidence is how the model
    concluded price moves 2.6 cents in ninety seconds, while the tape said
    cells rated under 0.02% touched 1.96% of the time."""
    sigma, hurst, fitted = 0.015, 0.12, 32.0
    naive = sigma * (90.0 ** hurst)                  # old behaviour
    guarded = spread(sigma, 90.0, hurst, fitted)
    assert guarded > naive
    # Inside the fitted range nothing changes.
    assert spread(sigma, 20.0, hurst, fitted) == sigma * (20.0 ** hurst)
    assert spread(sigma, 32.0, hurst, fitted) == sigma * (32.0 ** hurst)


def test_the_widening_is_bounded_by_brownian():
    sigma, hurst, fitted = 0.015, 0.12, 32.0
    guarded = spread(sigma, 200.0, hurst, fitted)
    brownian = sigma * (200.0 ** 0.5)
    assert guarded < brownian, "must not overshoot a random walk"


def test_no_fitted_range_means_the_old_behaviour():
    assert spread(0.02, 60.0, 0.3, 0.0) == 0.02 * (60.0 ** 0.3)


def test_a_reachability_estimate_rises_at_long_horizons():
    near = Diffusion(mu=0.0, mu_raw=0.0, sigma=0.015, pairs=300, span_s=45.0,
                     ok=True, hurst=0.12, hurst_fitted_to_s=32.0)
    naive = Diffusion(mu=0.0, mu_raw=0.0, sigma=0.015, pairs=300, span_s=45.0,
                      ok=True, hurst=0.12, hurst_fitted_to_s=0.0)
    # A band within a couple of standard deviations, so the difference is
    # measurable rather than both rounding to zero.
    guarded_p = band_touch_prob(0.05, 0.10, 85.0, 90.0, near)
    naive_p = band_touch_prob(0.05, 0.10, 85.0, 90.0, naive)
    assert guarded_p > naive_p > 0.0


def test_the_fit_reports_the_exponent_it_measured():
    ticks = _reverting()
    diff = estimate_diffusion(ticks, now=ticks[-1].ts)
    assert diff.ok
    assert diff.hurst < 0.3
    assert diff.hurst_pairs > 0
    assert diff.to_dict()["hurst"] == diff.hurst


# --- what it does to the surface -----------------------------------------
def _diff(hurst):
    return Diffusion(mu=0.0, mu_raw=0.0, sigma=0.05, pairs=200, span_s=60.0,
                     ok=True, hurst=hurst)


def test_mean_reversion_cuts_long_horizon_reach_not_short():
    """The correction must bite at 90s and barely register at 5s."""
    brownian, reverting = _diff(0.5), _diff(0.1)
    near_b = hit_prob_up(0.25, 0.0, 0.05, 5.0, brownian.hurst)
    near_r = hit_prob_up(0.25, 0.0, 0.05, 5.0, reverting.hurst)
    far_b = hit_prob_up(0.25, 0.0, 0.05, 90.0, brownian.hurst)
    far_r = hit_prob_up(0.25, 0.0, 0.05, 90.0, reverting.hurst)
    assert far_r < far_b, "reversion must reduce far-horizon reach"
    assert abs(near_r - near_b) < abs(far_r - far_b), "short horizons barely change"


def test_a_far_cell_is_less_reachable_under_reversion():
    lo, hi = 1.5, 2.0          # three rows out, at 0.5 per row
    brownian = band_touch_prob(lo, hi, 60.0, 65.0, _diff(0.5))
    reverting = band_touch_prob(lo, hi, 60.0, 65.0, _diff(0.1))
    assert reverting < brownian


def test_the_at_the_money_cell_is_barely_affected():
    """Price already sits in the band; reversion cannot make that less true."""
    both = [band_touch_prob(-0.25, 0.25, 0.0, 5.0, _diff(h)) for h in (0.5, 0.1)]
    assert both[0] == both[1] == 1.0


def test_probabilities_stay_in_range_across_the_exponent_band():
    for hurst in (HURST_MIN, 0.2, 0.35, 0.5, HURST_MAX):
        for t_start in (0.0, 5.0, 45.0):
            p = band_touch_prob(0.6, 1.1, t_start, t_start + 5.0, _diff(hurst))
            assert 0.0 <= p <= 1.0
            assert not math.isnan(p)

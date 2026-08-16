"""OU first-passage and robust fit. Sentence names; live numbers in docstrings."""

from __future__ import annotations

import math
import random

import pytest

from src.analytics.ou import OuFit, fit_ou, one_sided_touch, two_sided_touch


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _bm_one_sided(x0: float, barrier: float, T: float, sigma: float) -> float:
    return min(1.0, 2.0 * _phi(-abs(barrier - x0) / (sigma * math.sqrt(T))))


def _mc_one_sided(
    x0: float,
    barrier: float,
    T: float,
    theta: float,
    sigma: float,
    mu: float = 0.0,
    n_paths: int = 2500,
    n_steps: int = 400,
    seed: int = 7,
) -> float:
    """Cheap Euler hit-or-cross. Tests only — not the hot function."""
    rng = random.Random(seed)
    dt = T / n_steps
    sdt = math.sqrt(dt)
    hits = 0
    for _ in range(n_paths):
        x = x0
        prev = x
        hit = False
        for _ in range(n_steps):
            x = x + (-theta * (x - mu)) * dt + sigma * sdt * rng.gauss(0.0, 1.0)
            if (x - barrier) * (prev - barrier) <= 0.0:
                hit = True
                break
            prev = x
        if hit:
            hits += 1
    return hits / n_paths


def test_vanishing_theta_recovers_brownian_one_sided_touch():
    """θ → 0 is the 5s-cell BM reflection formula 2Φ(−|b−x0|/(σ√T)).

    Driftless BM, not a 'calm OU'. A tiny θ must not invent mean reversion
    that shrinks a middle-band cell (the 0.26 → 0.08 hole is a *finite*-θ
    effect, not a vanishing-θ one).
    """
    x0, barrier, T, sigma = 0.0, 1.150349380, 1.0, 1.0
    expected = _bm_one_sided(x0, barrier, T, sigma)
    got = one_sided_touch(x0, barrier, T, theta=1e-8, sigma=sigma, mu=0.0)
    assert 0.0 <= got <= 1.0
    assert abs(got - expected) < 0.015
    assert abs(expected - 0.25) < 0.002


def test_large_theta_does_not_overflow_or_return_nan():
    """θT = 20, 50, 100. A prior series path overflowed exp(θT) / erf(1e8).

    Those NaNs got coerced to a 'sure' cell. Finite and in [0, 1] only.
    """
    for tau in (20.0, 50.0, 100.0):
        p = one_sided_touch(0.0, 1.0, T=1.0, theta=tau, sigma=1.0, mu=0.0)
        assert math.isfinite(p), f"non-finite at θT={tau}: {p!r}"
        assert 0.0 <= p <= 1.0


def test_large_theta_kills_a_barrier_beyond_stationary_scale():
    """Barrier at 4 σ_stat, large θ. P near 0.

    This is why OU should kill far-column BM overrates. BM with the same
    σ, T still prints 0.5–0.8 at this distance (4 / √(2θT) is only a
    fraction of a BM scale). Far lottery cells stayed tappable on BM.
    """
    theta, T, sigma = 20.0, 1.0, 1.0
    sigma_stat = sigma / math.sqrt(2.0 * theta)
    barrier = 4.0 * sigma_stat
    p_ou = one_sided_touch(0.0, barrier, T, theta, sigma, mu=0.0)
    p_bm = _bm_one_sided(0.0, barrier, T, sigma)
    assert math.isfinite(p_ou)
    assert p_ou < 0.08
    assert p_bm > 0.45
    assert p_ou < 0.25 * p_bm


def test_near_barrier_stays_high_even_at_moderate_theta():
    """p > 0.75 is calibrated / saturated on the live book.

    A 5s Euphoria window, θT ≈ 1.2, gap ≈ 0.25 σ√T: you are already on
    the cell. Mean reversion must not talk this down into a 'maybe'.
    """
    T, theta, sigma = 5.0, 0.24, 1.0
    x0, barrier = 0.0, 0.25
    p = one_sided_touch(x0, barrier, T, theta, sigma, mu=0.0)
    assert p > 0.75
    mc = _mc_one_sided(x0, barrier, T, theta, sigma, n_paths=1800, n_steps=300, seed=3)
    assert abs(p - mc) < 0.08


def test_a_failed_fit_does_not_read_as_stillness():
    """Dead feed → ten tappable cells at ev_lcb 0.51.

    A frozen 45s / 2 Hz tape is not σ = 0 calm OU. σ = 0 made nearby
    squares look already-touched (p = 1) and the LCB sat at 0.51.
    Failed fit: ok=False, reason='thin', σ is NaN (not 0), and touch
    raises instead of returning p = 1.
    """
    n = 91  # 45s at 2 Hz
    t0 = 1_700_000_000.0
    times = [t0 + i * 0.5 for i in range(n)]
    prices = [2473.80] * n  # last live mid before the feed died
    fit = fit_ou(times, prices)
    assert isinstance(fit, OuFit)
    assert fit.ok is False
    assert fit.reason == "thin"
    assert fit.n == n
    assert fit.sigma != 0.0
    assert math.isnan(fit.sigma)
    assert math.isnan(fit.theta)
    with pytest.raises(ValueError, match="finite|sigma"):
        one_sided_touch(2473.80, 2474.00, 5.0, fit.theta, fit.sigma, fit.mu)


def test_fit_on_a_16x_sigma_spike_does_not_follow_the_spike():
    """σ estimator swung 16.6× while price moved six rows.

    One 16× increment in a 45s 2 Hz series. MAD / trimmed QV must not
    follow that tick. A last-increment σ did, and the book widened.
    """
    rng = random.Random(11)
    n = 91
    t0 = 1_700_000_000.0
    dt = 0.5
    times = [t0 + i * dt for i in range(n)]
    sigma_true = 0.40
    prices = [2000.0]
    for _ in range(n - 1):
        prices.append(prices[-1] + sigma_true * math.sqrt(dt) * rng.gauss(0.0, 1.0))
    clean = fit_ou(times, prices)
    assert clean.ok or clean.reason == "anti-reverting"
    spiked = list(prices)
    # Typical |Δx| ≈ σ √dt; inject 16× that at the midpoint.
    typical = sigma_true * math.sqrt(dt)
    spiked[45] = spiked[44] + 16.0 * typical
    spiked_fit = fit_ou(times, spiked)
    assert spiked_fit.sigma > 0.0 and math.isfinite(spiked_fit.sigma)
    # Must not jump 16×. Live failure was 16.6×.
    assert spiked_fit.sigma / clean.sigma < 2.5
    assert spiked_fit.sigma / sigma_true < 3.0


def test_two_sided_is_at_least_each_one_sided_and_at_most_one():
    """Images are wrong for OU. PDE two-sided sits in the inclusion interval.

    max(P_lo, P_hi) ≤ P_two ≤ 1. A union P_lo+P_hi is only an upper
    bound and is not used as the answer.
    """
    x0, T, theta, sigma, mu = 0.0, 1.0, 0.8, 1.0, 0.0
    b_lo, b_hi = -1.0, 1.2
    p_lo = one_sided_touch(x0, b_lo, T, theta, sigma, mu)
    p_hi = one_sided_touch(x0, b_hi, T, theta, sigma, mu)
    p_two = two_sided_touch(x0, b_lo, b_hi, T, theta, sigma, mu)
    assert 0.0 <= p_two <= 1.0
    assert p_two + 1e-9 >= p_lo
    assert p_two + 1e-9 >= p_hi
    assert p_two <= 1.0


def test_bm_over_ou_ratio_at_middle_barrier_is_order_2_to_4_for_theta_T_around_1_5():
    """0.26 → 0.08 middle-band hole.

    Money-man Euler check: at BM p ≈ 0.25, θT ≈ 1.5, BM/OU ≈ 2.45.
    Synthetic sample, too small to claim a shadow move. Ratio must be
    > 2 and < 5. Cheap MC is the cross-check (not in the hot function).
    """
    # 2Φ(−z) = 0.25 ⇒ z = −Φ^{-1}(0.125) ≈ 1.150349380
    z = 1.150349380
    T, sigma, theta = 1.0, 1.0, 1.5
    barrier = z * sigma * math.sqrt(T)
    p_bm = one_sided_touch(0.0, barrier, T, theta=1e-12, sigma=sigma, mu=0.0)
    p_ou = one_sided_touch(0.0, barrier, T, theta=theta, sigma=sigma, mu=0.0)
    assert abs(p_bm - 0.25) < 0.01
    assert 0.0 < p_ou < p_bm
    ratio = p_bm / p_ou
    assert 2.0 < ratio < 5.0, f"BM/OU={ratio:.3f} p_bm={p_bm:.4f} p_ou={p_ou:.4f}"

    mc = _mc_one_sided(0.0, barrier, T, theta, sigma, n_paths=3000, n_steps=500, seed=21)
    assert abs(p_ou - mc) < 0.05

    # Invalid params must not look like a sure cell.
    with pytest.raises(ValueError):
        one_sided_touch(0.0, barrier, T, theta=-0.4, sigma=sigma)
    with pytest.raises(ValueError):
        one_sided_touch(0.0, barrier, T, theta=theta, sigma=0.0)
    with pytest.raises(ValueError):
        one_sided_touch(0.0, barrier, T=0.0, theta=theta, sigma=sigma)


def test_anti_reverting_window_is_not_clamped_to_a_fake_calm_theta():
    """Momentum is not a calm OU. Clamping θ>0 made a trend look mean-reverting."""
    t0 = 1_700_000_000.0
    times = [t0 + i * 0.5 for i in range(90)]
    prices = [2000.0 + 0.08 * i for i in range(90)]  # steady walk, six+ rows
    fit = fit_ou(times, prices)
    assert fit.ok is False
    assert fit.reason == "anti-reverting"
    assert fit.theta < 0.0
    assert fit.sigma > 0.0 and math.isfinite(fit.sigma)
    assert math.isnan(fit.sigma_stat)

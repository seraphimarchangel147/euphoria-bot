"""Winner's-curse / selection-rank calibrator — measured, not μ_N."""
from __future__ import annotations

import random

from src.analytics.rank_calibrator import (
    LCB_Z,
    MAX_LIFT_ABSOLUTE,
    MAX_LIFT_FACTOR,
    MIN_BOOTSTRAP_WINDOWS,
    MIN_EDGE,
    MIN_P_LCB,
    POLICY_NOTE,
    PRIOR_WEIGHT,
    RankCalibrator,
    apply_to_winner,
    closed_form_displayed,
    mu_N,
    rank_band_key,
)


N_QUOTED = 180
P_TRUE = 0.35
P_DISPLAYED = 0.60
P_HOLE = 0.365  # predicted 0.60 → actual 0.365


def _sigma_for_closed_form_hole() -> float:
    """σ such that closed-form i.i.d. noise turns true 0.35 into displayed 0.60."""
    return (P_DISPLAYED - P_TRUE) / mu_N(N_QUOTED)


def test_closed_form_mu_N_overstates_when_errors_are_tape_shared():
    """I.i.d. μ_N treats ~180 quoted cells as independent lucky draws.

    They share one price path. A common tape shock does not change the
    ranking, so argmax does not harvest 180 residuals. Closed form maps
    true 0.35 → displayed 0.60 (σ μ_180 = 0.25). Empirical rank-0
    overstatement on tape-shared windows stays near 0 — μ_N overstates.
    Using μ_N as a correction would haircut a well-calibrated 0.35 down
    toward 0.10. The empirical bucket does not.
    """
    sigma = _sigma_for_closed_form_hole()
    assert abs(closed_form_displayed(P_TRUE, N_QUOTED, sigma) - P_DISPLAYED) < 1e-9
    assert abs(mu_N(2) - 1.0 / (3.141592653589793 ** 0.5)) < 0.01
    assert 2.5 < mu_N(N_QUOTED) < 3.0

    rng = random.Random(7)
    cal = RankCalibrator()
    residuals = []
    for _ in range(400):
        shock = rng.gauss(0.0, sigma)
        displayed = min(1.0, max(0.0, P_TRUE + shock))
        touched = rng.random() < P_TRUE
        cal.observe_ranked(displayed, 0, touched)
        residuals.append(displayed - (1.0 if touched else 0.0))

    empirical_s = sum(residuals) / len(residuals)
    closed_form_bias = P_DISPLAYED - P_TRUE  # 0.25
    assert empirical_s < 0.08
    assert closed_form_bias > 0.20
    assert empirical_s < closed_form_bias - 0.12

    adj = cal.adjust_rank(P_TRUE, 0)
    assert adj.trusted is True
    # Empirical lift ≈ 1. Closed-form "correction" of 0.35 is 0.10.
    assert abs(adj.p_cal - P_TRUE) < 0.06
    assert adj.p_cal > P_TRUE - 0.10
    mu_n_corrected = P_TRUE - sigma * mu_N(N_QUOTED)
    assert mu_n_corrected < 0.12
    assert adj.p_cal > mu_n_corrected + 0.15


def test_rank0_realises_the_060_to_0365_hole():
    """predicted 0.60 → actual 0.365; closed form turns true 0.35 into displayed 0.60.

    Even-money EV at 0.365 is 2×0.365 − 1 = −0.27. A bug that 'corrects'
    the hole by lifting would print +0.40 (p ≈ 0.70). Haircut toward the
    tape, never mint that sign flip.
    """
    sigma = _sigma_for_closed_form_hole()
    assert abs(closed_form_displayed(0.35, 180, sigma) - 0.60) < 1e-9

    cal = RankCalibrator()
    # 400 rank-0 settles at the live hole: 146/400 = 0.365.
    hits = 146
    for i in range(400):
        cal.observe_ranked(0.60, 0, i < hits)
    adj = cal.adjust_rank(0.60, 0)
    assert adj.key == "0"
    assert adj.n == 400
    assert adj.trusted is True
    assert 0.34 < adj.p_cal < 0.40
    assert adj.p_cal < 0.60
    assert adj.p_lcb <= adj.p_cal
    # Still a hole vs displayed 0.60; not a manufactured 0.70.
    assert adj.p_cal < 0.50
    even_money_ev = 2.0 * adj.p_cal - 1.0
    assert even_money_ev < 0.0
    assert even_money_ev > -0.40
    assert even_money_ev != 0.40
    # Closed form invented the 0.60. We un-invent it on the cells we pick.
    assert abs(adj.p_cal - 0.365) < 0.03

    p_cal2, p_lcb2 = apply_to_winner(0.60, 0.55, cal)
    assert p_cal2 < 0.50
    assert p_lcb2 <= p_cal2
    assert MIN_EDGE == 0.05 and MIN_P_LCB == 0.02
    assert "adjust_rank(rank=0)" in POLICY_NOTE
    assert "MIN_EDGE=0.05" in POLICY_NOTE


def test_a_cold_rank_bucket_cannot_manufacture_edge():
    """Fat multiplier + thin n must not produce a tap.

    Live bugs: +475% (5.75×) and +641% (7.41×) lifts on cold buckets
    printed taps the tape never earned. Three lucky hits at p=0.08 stay
    untrusted; incoming p is unchanged; LCB stays below both break-evens.
    Caps still bind if someone forces the bucket warm.
    """
    assert rank_band_key(0) == "0"
    assert rank_band_key(1) == "1-2"
    assert rank_band_key(2) == "1-2"
    assert rank_band_key(4) == "3-5"
    assert rank_band_key(10) == "6-15"
    assert rank_band_key(40) == "16+"

    cal = RankCalibrator()
    for _ in range(3):
        cal.observe_ranked(0.08, 0, True)
    adj = cal.adjust_rank(0.08, 0)
    assert adj.trusted is False
    assert adj.n == 3
    assert adj.p_cal == 0.08
    assert adj.p_lcb == 0.08
    assert adj.p_lcb < 1.0 / 5.75  # +475%
    assert adj.p_lcb < 1.0 / 7.41  # +641%
    assert adj.p_cal <= 0.08 * MAX_LIFT_FACTOR
    assert adj.p_cal <= 0.08 + MAX_LIFT_ABSOLUTE

    p_cal2, p_lcb2 = apply_to_winner(0.08, 0.04, cal)
    assert (p_cal2, p_lcb2) == (0.08, 0.04)

    # Even a forced-warm thin bucket cannot print a 5.75× / 7.41× lift.
    forced = RankCalibrator(min_trusted_n=1)
    for _ in range(3):
        forced.observe_ranked(0.08, 0, True)
    fat = forced.adjust_rank(0.08, 0)
    assert fat.p_cal <= 0.08 + MAX_LIFT_ABSOLUTE + 1e-12
    assert fat.p_cal <= 0.08 * MAX_LIFT_FACTOR
    assert fat.p_lcb <= fat.p_cal
    assert fat.p_lcb < 1.0 / 5.75
    assert fat.p_lcb < 1.0 / 7.41
    assert PRIOR_WEIGHT == 8.0
    assert abs(LCB_Z - 1.6449) < 1e-9


def test_block_bootstrap_reports_sample_size_and_refuses_to_claim_significance_on_tiny_n():
    """Twelve windows can print a pretty interval. They cannot prove a curse.

    n_windows=12 < 50 → too_small=True, and the reported interval must
    include 0 so nobody ships a 'significant' s. s is the rank-0 residual
    mean(p_model − 1{touched}) on one shared-tape window.
    """
    tiny = [
        [{"p_model": 0.60, "rank": 0, "touched": i % 3 == 0}]
        for i in range(12)
    ]
    out = RankCalibrator().block_bootstrap_s(tiny, n_boot=400, rng=random.Random(0))
    assert out["n_windows"] == 12
    assert out["n_boot"] == 400
    assert out["too_small"] is True
    assert out["n_windows"] < MIN_BOOTSTRAP_WINDOWS
    assert out["s_lo"] <= 0.0 <= out["s_hi"]

    empty = RankCalibrator().block_bootstrap_s([], n_boot=400)
    assert empty["too_small"] is True
    assert empty["n_windows"] == 0

    rng = random.Random(1)
    fat = []
    for _ in range(60):
        touched = rng.random() < 0.365
        fat.append([{"p_model": 0.60, "rank": 0, "touched": touched}])
    ok = RankCalibrator().block_bootstrap_s(fat, n_boot=400, rng=random.Random(2))
    assert ok["n_windows"] == 60
    assert ok["too_small"] is False
    assert ok["s_hat"] > 0.10  # 0.60 − 0.365 ≈ 0.235
    assert ok["s_lo"] < ok["s_hat"] < ok["s_hi"]

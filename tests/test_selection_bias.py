"""Selection-rank calibrator gates. SYNTHETIC units; tape is not measured here."""
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


def test_too_small_sits_adjust_rank_and_apply_to_winner():
    """n_windows < 50 sits the live path, not merely the bootstrap CI.

    Twelve observe_window calls (each with rank-0 rows) make the rank-0
    cell count warm (60 >= 20). That is not enough. adjust_rank and
    apply_to_winner must pass through incoming p and set trusted=False.
    """
    cal = RankCalibrator()
    for _ in range(12):
        cal.observe_window(
            [{"p_model": 0.40, "rank": 0, "touched": False} for _ in range(5)]
        )
    assert cal.n_windows == 12
    assert cal.too_small is True
    assert cal._buckets["0"].n == 60
    adj = cal.adjust_rank(0.40, 0)
    assert adj.too_small is True
    assert adj.trusted is False
    assert adj.n_windows == 12
    assert adj.p_cal == 0.40
    assert adj.p_lcb == 0.40
    assert apply_to_winner(0.40, 0.30, cal) == (0.40, 0.30)
    assert MIN_BOOTSTRAP_WINDOWS == 50
    assert "too_small" in POLICY_NOTE


def test_a_cell_flood_without_fifty_windows_does_not_trust_or_haircut():
    """SYNTHETIC. Four hundred rank-0 observe_ranked calls are not 50 windows.

    Cell-count warmth does not open the gate. The live path must not
    haircut. This is not a measurement of any reliability pin and does
    not reprint a 0.60/0.365 hole from invented hits.
    """
    cal = RankCalibrator()
    for i in range(400):
        cal.observe_ranked(0.50, 0, i % 5 == 0)
    assert cal.n_windows == 0
    assert cal.too_small is True
    assert cal._buckets["0"].n == 400
    adj = cal.adjust_rank(0.50, 0)
    assert adj.trusted is False
    assert adj.too_small is True
    assert adj.p_cal == 0.50
    assert adj.p_lcb == 0.50
    assert apply_to_winner(0.50, 0.45, cal) == (0.50, 0.45)

    stuffed = RankCalibrator()
    stuffed.observe_window(
        [{"p_model": 0.50, "rank": 0, "touched": False} for _ in range(400)]
    )
    assert stuffed.n_windows == 1
    flood = stuffed.adjust_rank(0.50, 0)
    assert flood.trusted is False
    assert flood.p_cal == 0.50


def test_closed_form_mu_N_is_not_the_live_correction():
    """SYNTHETIC. μ_N is a comparison helper. It is never the live path.

    I.i.d. closed form maps true 0.35 → displayed 0.60 when σ μ_180 = 0.25.
    Owner diagnosis, not re-measured here: predicted 0.60 → actual 0.365
    over 111,381 labelled cells. This test does not invent 146/400 hits
    to reprint that hole. With n_windows=0 the live path pass-throughs
    a 0.60; it does not subtract σ μ_N.
    """
    n_quoted = 180
    sigma = 0.25 / mu_N(n_quoted)
    assert abs(closed_form_displayed(0.35, n_quoted, sigma) - 0.60) < 1e-9
    assert abs(mu_N(2) - 1.0 / (3.141592653589793 ** 0.5)) < 0.01
    assert 2.5 < mu_N(n_quoted) < 3.0

    rng = random.Random(7)
    residuals = []
    for _ in range(400):
        shock = rng.gauss(0.0, sigma)
        displayed = min(1.0, max(0.0, 0.35 + shock))
        touched = rng.random() < 0.35
        residuals.append(displayed - (1.0 if touched else 0.0))
    empirical_s = sum(residuals) / len(residuals)
    assert empirical_s < 0.08
    assert (0.60 - 0.35) > empirical_s + 0.12

    cal = RankCalibrator()
    adj = cal.adjust_rank(0.60, 0)
    assert cal.n_windows == 0
    assert adj.trusted is False
    assert adj.too_small is True
    assert adj.p_cal == 0.60
    assert adj.p_lcb == 0.60
    mu_n_corrected = 0.60 - sigma * mu_N(n_quoted)
    assert abs(mu_n_corrected - 0.35) < 1e-9
    assert adj.p_cal != mu_n_corrected


def test_a_cold_rank_bucket_cannot_manufacture_edge():
    """Fat multiplier + thin n must not produce a tap.

    Live bugs: +475% (5.75×) and +641% (7.41×) lifts on cold rank-0
    printed taps the tape never earned. Three lucky hits at p=0.08 stay
    untrusted; incoming p is unchanged; LCB stays below both break-evens.
    Caps still bind if the window gate is forced open on all-hit synthetics.
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
    assert adj.too_small is True
    assert adj.n == 3
    assert adj.p_cal == 0.08
    assert adj.p_lcb == 0.08
    assert adj.p_lcb < 1.0 / 5.75  # +475%
    assert adj.p_lcb < 1.0 / 7.41  # +641%
    assert adj.p_cal <= 0.08 * MAX_LIFT_FACTOR
    assert adj.p_cal <= 0.08 + MAX_LIFT_ABSOLUTE
    assert apply_to_winner(0.08, 0.04, cal) == (0.08, 0.04)

    forced = RankCalibrator(min_trusted_n=1)
    for _ in range(50):
        forced.observe_window([{"p_model": 0.08, "rank": 0, "touched": True}])
    fat = forced.adjust_rank(0.08, 0)
    assert fat.n_windows == 50
    assert fat.p_cal <= 0.08 + MAX_LIFT_ABSOLUTE + 1e-12
    assert fat.p_cal <= 0.08 * MAX_LIFT_FACTOR
    assert fat.p_lcb <= fat.p_cal
    assert fat.p_lcb < 1.0 / 5.75
    assert fat.p_lcb < 1.0 / 7.41
    assert PRIOR_WEIGHT == 8.0
    assert abs(LCB_Z - 1.6449) < 1e-9


def test_empty_and_twelve_windows_are_too_small_and_pass_through():
    """n_windows=0 and n_windows=12 → too_small, not measured, pass-through.

    This remote has no live GridBoard stream. Tape n_windows=0. Baseline
    not beaten: full window 1365 / 12.2% / −0.2775 vs −0.2712
    (indistinguishable). Drop −0.4948. Full windows only. Do not claim
    an EV move. Bootstrap on twelve caller rows may print an interval;
    it cannot unlock adjust_rank.
    """
    empty_cal = RankCalibrator()
    empty = empty_cal.block_bootstrap_s()
    assert empty["n_windows"] == 0
    assert empty["too_small"] is True
    assert empty["measured"] is False
    assert empty["s_hat"] is None
    adj0 = empty_cal.adjust_rank(0.50, 0)
    assert adj0.trusted is False
    assert adj0.p_cal == 0.50
    assert apply_to_winner(0.50, 0.40, empty_cal) == (0.50, 0.40)

    tiny = [[{"p_model": 0.50, "rank": 0, "touched": False}] for _ in range(12)]
    out = RankCalibrator().block_bootstrap_s(tiny, n_boot=400, rng=random.Random(0))
    assert out["n_windows"] == 12
    assert out["n_boot"] == 400
    assert out["too_small"] is True
    assert out["measured"] is False
    assert out["n_windows"] < MIN_BOOTSTRAP_WINDOWS

    ingested = RankCalibrator()
    for window in tiny:
        ingested.observe_window(window)
    assert ingested.n_windows == 12
    seated = ingested.adjust_rank(0.50, 0)
    assert seated.trusted is False
    assert seated.too_small is True
    assert seated.p_cal == 0.50
    assert apply_to_winner(0.50, 0.40, ingested) == (0.50, 0.40)
    assert MIN_EDGE == 0.05 and MIN_P_LCB == 0.02


def test_synthetic_beta_math_is_not_a_tape_measurement():
    """SYNTHETIC unit test of Beta shrinkage. Not a tape measurement.

    Sixty caller-built windows at p=0.40 with 12 touches (hit rate 0.20).
    After the window gate opens, p_cal moves toward the bucket mean. It
    does not reprint a live 0.60/0.365 hole and does not subtract μ_N.
    """
    cal = RankCalibrator()
    for i in range(60):
        cal.observe_window([{"p_model": 0.40, "rank": 0, "touched": i < 12}])
    assert cal.n_windows == 60
    assert cal.too_small is False
    adj = cal.adjust_rank(0.40, 0)
    assert adj.trusted is True
    assert adj.key == "0"
    assert adj.p_cal < 0.40
    assert 0.18 < adj.p_cal < 0.32
    assert adj.p_lcb <= adj.p_cal
    assert adj.p_cal != 0.40 - 0.25
    boot = cal.block_bootstrap_s(n_boot=200, rng=random.Random(3))
    assert boot["measured"] is True
    assert boot["too_small"] is False
    assert boot["n_windows"] == 60
    assert boot["s_hat"] is not None
    # residual mean ≈ 0.40 − 0.20 = 0.20 on these synthetics
    assert 0.10 < boot["s_hat"] < 0.30

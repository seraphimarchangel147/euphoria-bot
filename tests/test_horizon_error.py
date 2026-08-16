"""Pin tests for the horizon-structured barrier-touch error.

Sentence names are the spec. The 0.13× / 0.81× figures in the live
p = 0.41 docstring are the measured table, n = 111,381 labelled cells,
all labelled, not the selected ~180. The helpers are synthetic. They
must match sign and ordering. They do not recover 0.13 and 0.81 to
three decimals and must not be dressed up as that table.
"""

from src.analytics.horizon_error import (
    COMBINATION_SIGMA_ERR,
    COMBINATION_THETA,
    ESTIMATOR_H,
    LIVE_H,
    LONG_HORIZON_S,
    MATCHED_OU_THETA,
    MEASURED_LONG_X,
    MEASURED_SHORT_X,
    N_LABELLED_CELLS,
    P_SLICE,
    SHORT_HORIZON_S,
    SPEARMAN_PRED_ACTUAL,
    classic_bm_vs_ou_ratio,
    evaluate_all,
    live_hit_prob,
    live_sigma_eff,
    live_spread,
    money_man_bm_over_ou,
    predicted_to_actual_ratio,
    shortest_vs_longest_ratio,
    tap_band_has_good_news,
    verdict,
)


def test_a_too_small_hurst_alone_does_not_reproduce_short_013_long_081():
    a = verdict("A")
    assert a["hypothesis"] == "A"
    assert a["consistent"] is False
    assert a["predicted_short_x"] > 1.0
    assert a["predicted_long_x"] > 1.0
    assert a["predicted_long_x"] > a["predicted_short_x"]
    # House 0.09–0.15: long more understated / underconfident. Backwards.
    for hurst in (0.09, 0.12, 0.15):
        short_x, long_x = shortest_vs_longest_ratio(hurst=hurst, theta=0.0, sigma_err=1.0)
        assert short_x > 1.0 and long_x > 1.0
        assert long_x > short_x
        assert abs(short_x - MEASURED_SHORT_X) > 0.5
        assert abs(long_x - MEASURED_LONG_X) > 0.2
    # Estimator wants H below the 0.05 floor: mild overrate, worse at long T.
    low_s, low_l = shortest_vs_longest_ratio(hurst=ESTIMATOR_H, theta=0.0, sigma_err=1.0)
    assert 0.0 < low_s < 1.0 and 0.0 < low_l < 1.0
    assert low_l < low_s
    assert abs(low_s - MEASURED_SHORT_X) > 0.5


def test_missing_mean_reversion_at_fixed_predicted_p_overrates_short_horizons_more():
    """Matched-σ missing OU is backwards; the joint is not.

    Live T^H hack vs saturating first-passage, sigma matched, at fixed
    predicted p = 0.41: long more understated / underconfident. That
    branch is killed. Classic BM-vs-OU (d ∝ √T) overrates long T more
    — also killed. The joint tick-σ × T^{0.05} against saturating tape
    is the reading that overrates the short horizon more. Do not treat
    that joint as the 111,381-cell table.
    """
    matched_s, matched_l = shortest_vs_longest_ratio(
        hurst=LIVE_H, theta=MATCHED_OU_THETA, sigma_err=1.0, saturating=True
    )
    assert matched_s > 1.0 and matched_l > 1.0
    assert matched_l > matched_s

    b = verdict("B")
    assert b["consistent"] is False
    assert tap_band_has_good_news(b["predicted_short_x"], b["predicted_long_x"])
    assert b["predicted_long_x"] > b["predicted_short_x"]

    mm = money_man_bm_over_ou()
    assert 2.2 < mm < 2.7
    classic_s = classic_bm_vs_ou_ratio(SHORT_HORIZON_S, P_SLICE, theta=0.30)
    classic_l = classic_bm_vs_ou_ratio(LONG_HORIZON_S, P_SLICE, theta=0.30)
    assert classic_l < classic_s

    joint_s, joint_l = shortest_vs_longest_ratio(
        hurst=LIVE_H,
        theta=COMBINATION_THETA,
        sigma_err=COMBINATION_SIGMA_ERR,
        saturating=True,
    )
    assert joint_s < joint_l
    assert joint_s < 1.0 and joint_l < 1.0
    assert joint_s < 0.5


def test_the_live_041_slice_is_pinned():
    """At predicted 0.41, shortest bucket realised 0.13×, longest 0.81×,
    n from the 111,381-cell table. The helper's sign and ordering must
    match. This does not recover 0.13 and 0.81 to three decimals — it
    recovers the ordering and a factor-of-two hole at short T. Do not
    dress that up as the table. Confirmation is synthetic. Too small
    to claim a shadow move. Baseline −0.2712 / 453 / 48.3%.
    """
    assert N_LABELLED_CELLS == 111_381
    assert MEASURED_SHORT_X == 0.13
    assert MEASURED_LONG_X == 0.81
    assert P_SLICE == 0.41
    assert SPEARMAN_PRED_ACTUAL == 0.996

    # Live contract: sigma_eff = spread / sqrt(T) ⇒ p = 2Φ(-d / spread).
    spread = live_spread(1.0, SHORT_HORIZON_S, LIVE_H)
    assert abs(live_sigma_eff(spread, SHORT_HORIZON_S) - spread / SHORT_HORIZON_S**0.5) < 1e-12
    assert 0.0 < live_hit_prob(spread * 0.824, 1.0, SHORT_HORIZON_S, LIVE_H) < 1.0

    d = verdict("D")
    assert d["hypothesis"] == "D"
    assert d["consistent"] is True
    short_x = d["predicted_short_x"]
    long_x = d["predicted_long_x"]
    assert short_x < long_x
    assert short_x < 1.0 and long_x < 1.0
    assert short_x < 0.5
    assert long_x > 0.35
    assert abs(short_x - MEASURED_SHORT_X) > 0.005 or abs(long_x - MEASURED_LONG_X) > 0.005
    assert "not" in d["note"].lower() and "three decimal" in d["note"].lower()
    assert "111,381" in d["note"] or "111381" in d["note"]

    same = shortest_vs_longest_ratio(
        P_SLICE,
        hurst=LIVE_H,
        theta=COMBINATION_THETA,
        sigma_err=COMBINATION_SIGMA_ERR,
        saturating=True,
    )
    assert same == (short_x, long_x)

    # Desk pre-check: uniform overstated σ is T-flat at fixed p, mu=0.
    c_s, c_l = shortest_vs_longest_ratio(hurst=LIVE_H, theta=0.0, sigma_err=2.0)
    assert abs(c_s - c_l) < 1e-12
    assert verdict("C")["consistent"] is False


def test_good_news_is_rejected_if_a_hypothesis_predicts_actual_above_predicted_in_the_tap_band():
    """We have never been under-confident in 0.10–0.45."""
    all_v = evaluate_all()
    for name in ("A", "B"):
        v = all_v[name]
        assert tap_band_has_good_news(v["predicted_short_x"], v["predicted_long_x"])
        assert v["consistent"] is False

    d = all_v["D"]
    assert not tap_band_has_good_news(d["predicted_short_x"], d["predicted_long_x"])
    assert 0.10 <= P_SLICE <= 0.45

    optimistic = predicted_to_actual_ratio(
        SHORT_HORIZON_S, 0.25, hurst=0.12, theta=0.0, sigma_err=1.0
    )
    assert optimistic > 1.0
    assert tap_band_has_good_news(optimistic, optimistic)

"""Pin tests for the horizon-structured barrier-touch error.

Sentence names are the spec. The 0.13× / 0.81× figures in the live
p = 0.41 docstring are the measured table, n = 111,381 labelled cells.
The helpers are synthetic. They must match sign and ordering. They do
not recover 0.13 and 0.81 to three decimals.
"""

from src.analytics.horizon_error import (
    COMBINATION_HURST,
    COMBINATION_SIGMA_ERR,
    COMBINATION_THETA,
    LIVE_H,
    LONG_HORIZON_S,
    MEASURED_LONG_X,
    MEASURED_SHORT_X,
    N_LABELLED_CELLS,
    P_SLICE,
    SHORT_HORIZON_S,
    classic_bm_vs_ou_ratio,
    evaluate_all,
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
    # House band 0.09–0.15 is one-sided the wrong way at fixed predicted p.
    for hurst in (0.09, 0.12, 0.15):
        short_x, long_x = shortest_vs_longest_ratio(hurst=hurst, theta=0.0, sigma_err=1.0)
        assert short_x > 1.0 and long_x > 1.0
        assert long_x > short_x
        assert abs(short_x - MEASURED_SHORT_X) > 0.5
        assert abs(long_x - MEASURED_LONG_X) > 0.2


def test_missing_mean_reversion_at_fixed_predicted_p_overrates_short_horizons_more():
    """One-increment / no-pullback reach at the live p=0.41 slice.

    Short T is basically one increment; overstated reach overrates a 5s
    touch more than a 40s touch, where even an OU has time to wander.
    That ordering is the table slice (fixed *predicted* p, live T^{0.05}
    barrier almost flat). Classic BM-vs-OU at a √T-fixed p is the
    opposite monotone and is killed in verdict('B').
    """
    short_x, long_x = shortest_vs_longest_ratio(
        hurst=COMBINATION_HURST,
        theta=COMBINATION_THETA,
        sigma_err=COMBINATION_SIGMA_ERR,
    )
    assert short_x < long_x
    assert short_x < 1.0 and long_x < 1.0
    assert short_x < 0.5

    # Money-man Euler pin: BM p ≈ 0.25, θT ≈ 1.5 → BM/OU ≈ 2.45.
    mm = money_man_bm_over_ou()
    assert 2.2 < mm < 2.7
    near_s = classic_bm_vs_ou_ratio(SHORT_HORIZON_S, 0.80, theta=0.30)
    assert abs(near_s - 1.0) < 0.25

    # Classic BM-quoted p (d ∝ √T) overrates long T more — wrong monotone.
    classic_s = classic_bm_vs_ou_ratio(SHORT_HORIZON_S, P_SLICE, theta=0.30)
    classic_l = classic_bm_vs_ou_ratio(LONG_HORIZON_S, P_SLICE, theta=0.30)
    assert classic_l < classic_s
    b = verdict("B")
    assert b["consistent"] is False
    assert tap_band_has_good_news(b["predicted_short_x"], b["predicted_long_x"])


def test_the_live_041_slice_is_pinned():
    """At predicted 0.41, shortest bucket realised 0.13×, longest 0.81×,
    n from the 111,381-cell table. The helper's sign and ordering must
    match. This does not recover 0.13 and 0.81 to three decimals — it
    recovers the ordering and a factor-of-two hole at short T. Do not
    dress that up. Confirmation is synthetic. Too small to claim a
    shadow move. Baseline −0.2712 / 453 / 48.3%.
    """
    assert N_LABELLED_CELLS == 111_381
    assert MEASURED_SHORT_X == 0.13
    assert MEASURED_LONG_X == 0.81
    assert P_SLICE == 0.41

    d = verdict("D")
    assert d["hypothesis"] == "D"
    assert d["consistent"] is True
    short_x = d["predicted_short_x"]
    long_x = d["predicted_long_x"]
    assert short_x < long_x
    assert short_x < 1.0 and long_x < 1.0
    assert short_x < 0.5
    assert long_x > 0.35
    # Honest hole: not three-decimal recovery of the measured pair.
    assert abs(short_x - MEASURED_SHORT_X) > 0.005 or abs(long_x - MEASURED_LONG_X) > 0.005
    assert "not" in d["note"].lower() and "three decimal" in d["note"].lower()

    same = shortest_vs_longest_ratio(
        P_SLICE,
        hurst=COMBINATION_HURST,
        theta=COMBINATION_THETA,
        sigma_err=COMBINATION_SIGMA_ERR,
    )
    assert same == (short_x, long_x)
    # C is T-flat at the same predicted p — cannot be the split.
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

    # A hypothesis that prints actual > predicted in the tap band is dead.
    optimistic = predicted_to_actual_ratio(
        SHORT_HORIZON_S, 0.25, hurst=0.12, theta=0.0, sigma_err=1.0
    )
    assert optimistic > 1.0
    assert tap_band_has_good_news(optimistic, optimistic)

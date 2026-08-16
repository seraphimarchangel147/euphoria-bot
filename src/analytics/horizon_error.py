"""Horizon-structured error in the live barrier-touch quote.

Live forecast math (forecast.py, not edited here). ``sigma`` is the mean
of ``dp^2/dt`` over consecutive settlement ticks (~16 Hz page / ws /
extension). Units: price / sqrt(second). ``spread(sigma, T, H)`` is
``sigma * T^H`` inside the fitted window; beyond ``fitted_to_s`` it
blends H back toward 0.5. ``HURST_MIN, HURST_MAX = 0.05, 0.50``. Live H
pins to the 0.05 floor (the estimator wants lower; house quotes imply
~0.09). ``hit_prob`` is BM reflection with ``sigma_eff = spread / sqrt(T)``,
so ``p = 2 Φ(-d / spread)``. The table is sliced at *predicted* p.

Measured signature, 111,381 labelled cells (all labelled, not the
selected ~180). Spearman(pred, actual) = +0.996. At predicted p = 0.41
the shortest horizon bucket realises 0.13× (actual ≈ 0.053) and the
longest realises 0.81× (actual ≈ 0.332). ~3× optimistic in the tap band
p = 0.10–0.45, calibrated at both extremes, 16–20× too low at tiny p.
We have never been under-confident in 0.10–0.45.

Conclusion (verified, not taken as given). Three named branches at
fixed model p = 0.41, mu = 0 reflection:

1. H-floor. Vs house 0.09 (same 2Φ map, sigma matched): 1.07× / 1.16×.
   Long more understated / underconfident. Backwards. Vs true H below
   the 0.05 floor (estimator wants lower): mild overrate that *worsens*
   with T (0.96× / 0.92× at H = 0.03). Still the wrong monotone for
   0.13 vs 0.81. A single wrong H is one-sided; neither side is the
   table. Killed.

2. Missing OU. Live ``T^H`` hack versus saturating first-passage
   (OU integrated variance), sigma matched: 1.63× / 1.95× at θ = 0.03.
   Long more understated / underconfident. Backwards. Killed.
   Classic BM-quoted p (d ∝ √T) vs OU is the other wrong monotone
   (long more *overrated*). Money-man Euler (BM p ≈ 0.25, θT ≈ 1.5 →
   BM/OU ≈ 2.45) is reproduced; that is not the table slice.

3. Overstated σ. Tick-level ``dp^2/dt`` versus the vol that actually
   displaces over seconds, same H, mu = 0: T-flat at fixed predicted p
   (0.24× / 0.24× at r = 2). The "max leverage at short T" claim is a
   fixed-distance statement. Killed for the split.

Joint "tick-σ × T^{0.05} against saturating tape" gets the *direction*
right (short more overrated, both below 1). Fat tick-σ puts the level
below 1; the live time-flat map versus a tape that still accumulates
then saturates opens the short-vs-long hole. Helpers recover sign and
a factor-of-two hole at short T. They do not recover 0.13 and 0.81 to
three decimals and must not be dressed up as the 111,381-cell table.
Confirmation is synthetic. Too small to claim a shadow move.

Something we were not looking for: fat tick-σ makes tiny-p *more*
overrated, so the measured 16–20× under-prediction at tiny p is a
separate hole (floor, discreteness, or selection). Rank order can stay
almost perfect while the level is a lie. The ``fitted_to_s`` blend
toward H = 0.5 is not the 5s-vs-40s split if that window is inside the
fitted range.

Changelog (for the owner's local CHANGELOG.md; this file does not
write that tree): 2026-08-16 / horizon desk / src/analytics/horizon_error.py
+ tests/test_horizon_error.py / analysis + pin test, not a shadow-traded
model, 8765 not restarted / measured signature n = 111,381 labelled
cells; confirmation synthetic; baseline −0.2712 / 453 / 48.3%; too
small to claim a shadow move / open: tiny-p 16–20× under-prediction;
what residual of the 16.6× tick estimator survives smoothing.
"""

from __future__ import annotations

import math
from statistics import NormalDist
from typing import TypedDict

HURST_MIN = 0.05
HURST_MAX = 0.50
LIVE_H = 0.05
HOUSE_H = 0.09
HOUSE_H_HIGH = 0.15
ESTIMATOR_H = 0.03  # below the live floor; estimator wants lower
SHORT_HORIZON_S = 5.0
LONG_HORIZON_S = 40.0
P_SLICE = 0.41
MEASURED_SHORT_X = 0.13
MEASURED_LONG_X = 0.81
N_LABELLED_CELLS = 111_381
SPEARMAN_PRED_ACTUAL = 0.996
TAP_BAND = (0.10, 0.45)

# Joint stand-in: fat tick-σ against a tape that saturates on a ~30s mix.
# Order-of-magnitude, not a fit of 0.13 / 0.81.
COMBINATION_HURST = 0.50
COMBINATION_THETA = 0.03
COMBINATION_SIGMA_ERR = 4.0
MATCHED_OU_THETA = 0.03

MONEY_MAN_P = 0.25
MONEY_MAN_THETA_T = 1.5
MONEY_MAN_BM_OVER_OU = 2.45

_N = NormalDist()


class HorizonVerdict(TypedDict):
    hypothesis: str
    consistent: bool
    predicted_short_x: float
    predicted_long_x: float
    note: str


def _phi(x: float) -> float:
    return _N.cdf(x)


def k_from_predicted_p(p_pred: float) -> float:
    """Invert ``p = 2 Φ(-k)`` for mu = 0 BM reflection."""
    p = min(max(float(p_pred), 1e-12), 1.0 - 1e-12)
    return -_N.inv_cdf(p / 2.0)


def touch_p_from_k(k: float) -> float:
    if k <= 0.0:
        return 1.0
    return min(1.0, 2.0 * _phi(-k))


def live_hurst(horizon_s: float, hurst: float, fitted_to_s: float | None = None) -> float:
    """Clamp H, then blend back toward 0.5 beyond ``fitted_to_s``."""
    h = min(max(float(hurst), HURST_MIN), HURST_MAX)
    if fitted_to_s is None or horizon_s <= fitted_to_s:
        return h
    weight = 1.0 - fitted_to_s / horizon_s
    return h + (0.5 - h) * weight


def live_spread(
    sigma: float,
    horizon_s: float,
    hurst: float,
    fitted_to_s: float | None = None,
) -> float:
    """``sigma * T^H`` inside the fitted window (forecast.py contract)."""
    if horizon_s <= 0.0 or sigma <= 0.0:
        raise ValueError("sigma and horizon_s must be positive")
    return sigma * (horizon_s ** live_hurst(horizon_s, hurst, fitted_to_s))


def live_sigma_eff(spread: float, horizon_s: float) -> float:
    """``sigma_eff = spread / sqrt(T)`` as used by live ``hit_prob``."""
    if horizon_s <= 0.0:
        raise ValueError("horizon_s must be positive")
    return spread / math.sqrt(horizon_s)


def live_hit_prob(
    distance: float,
    sigma: float,
    horizon_s: float,
    hurst: float = LIVE_H,
    fitted_to_s: float | None = None,
) -> float:
    """BM reflection with ``sigma_eff = spread / sqrt(T)``."""
    spread = live_spread(sigma, horizon_s, hurst, fitted_to_s)
    sigma_eff = live_sigma_eff(spread, horizon_s)
    return touch_p_from_k(distance / (sigma_eff * math.sqrt(horizon_s)))


def saturating_spread(horizon_s: float, theta: float, sigma: float = 1.0) -> float:
    """OU integrated-variance reach. Saturates at ``sigma / sqrt(2θ)``."""
    if horizon_s <= 0.0 or sigma <= 0.0:
        raise ValueError("sigma and horizon_s must be positive")
    if theta <= 0.0:
        return sigma * math.sqrt(horizon_s)
    return sigma * math.sqrt((1.0 - math.exp(-2.0 * theta * horizon_s)) / (2.0 * theta))


def saturating_hit_prob(distance: float, horizon_s: float, *, theta: float, sigma: float = 1.0) -> float:
    """First-passage stand-in: reflection on the saturating tape spread."""
    return touch_p_from_k(distance / saturating_spread(horizon_s, theta, sigma))


def live_implied_distance(horizon_s: float, p_pred: float, *, sigma_err: float = 1.0) -> float:
    """Barrier in true-σ units implied by a live ``T^{0.05}`` quote at ``p_pred``.

    Invert ``p = 2Φ(-d / (σ_live T^{0.05}))``. Same predicted p therefore
    holds distance almost fixed across 5s and 40s.
    """
    if horizon_s <= 0.0:
        raise ValueError("horizon_s must be positive")
    if sigma_err <= 0.0:
        raise ValueError("sigma_err must be positive")
    return k_from_predicted_p(p_pred) * sigma_err * (horizon_s**LIVE_H)


def _ou_reach_factor(theta_T: float) -> float:
    """Far-barrier OU/BM first-passage factor. ≈ 0.70 at θT = 1.5 (money man)."""
    x = max(float(theta_T), 0.0)
    return 1.0 / (1.0 + 0.55 * (1.0 - math.exp(-x)))


def _classic_ou_touch_p(horizon_s: float, distance: float, *, theta: float) -> float:
    """OU first-passage against a BM-scaled barrier (money-man comparison)."""
    spread = math.sqrt(horizon_s)
    k_bm = distance / spread
    if theta <= 0.0:
        return touch_p_from_k(k_bm)
    theta_T = theta * horizon_s
    far_factor = _ou_reach_factor(theta_T)
    k_far = k_bm / far_factor
    sigma_inf = 1.0 / math.sqrt(2.0 * theta)
    d_over_inf = distance / sigma_inf
    if d_over_inf > 2.0:
        extra = math.exp(-0.8 * (d_over_inf - 2.0) * (1.0 - math.exp(-min(theta_T, 8.0))))
        k_far = k_bm / max(far_factor * extra, 1e-9)
    return touch_p_from_k(k_far)


def predicted_to_actual_ratio(
    horizon_s: float,
    p_pred: float,
    *,
    hurst: float,
    theta: float,
    sigma_err: float,
    saturating: bool = False,
) -> float:
    """Actual / predicted under a hypothesized true process.

    Predicted side is always the live quote: ``p = 2Φ(-d / (σ T^{0.05}))``
    via ``sigma_eff = spread / sqrt(T)``. ``sigma_err`` is
    ``σ_tick / σ_tape``. When ``saturating`` is true, actual is
    reflection on OU integrated variance (the saturating tape).
    Otherwise actual is the same 2Φ map at ``hurst`` (H-floor / flat-σ
    branches; ``theta`` ignored unless saturating).
    """
    if horizon_s <= 0.0:
        raise ValueError("horizon_s must be positive")
    if not 0.0 < p_pred < 1.0:
        raise ValueError("p_pred must lie in (0, 1)")
    if hurst <= 0.0:
        raise ValueError("hurst must be positive")
    if theta < 0.0:
        raise ValueError("theta must be non-negative")
    if sigma_err <= 0.0:
        raise ValueError("sigma_err must be positive")

    distance = live_implied_distance(horizon_s, p_pred, sigma_err=sigma_err)
    if saturating:
        p_actual = saturating_hit_prob(distance, horizon_s, theta=theta)
    else:
        # Hypothesized truth is unclamped: the live floor is a model error,
        # not a property of the tape. ``p = 2Φ(-d / (σ T^H))``.
        if hurst <= 0.0:
            raise ValueError("hurst must be positive")
        p_actual = touch_p_from_k(distance / (horizon_s**hurst))
    return p_actual / p_pred


def shortest_vs_longest_ratio(
    p_pred: float = P_SLICE,
    *,
    hurst: float,
    theta: float,
    sigma_err: float,
    saturating: bool = False,
    short_s: float = SHORT_HORIZON_S,
    long_s: float = LONG_HORIZON_S,
) -> tuple[float, float]:
    """``(short_x, long_x)`` on the table slice at ``p_pred`` (default 0.41)."""
    return (
        predicted_to_actual_ratio(
            short_s, p_pred, hurst=hurst, theta=theta, sigma_err=sigma_err, saturating=saturating
        ),
        predicted_to_actual_ratio(
            long_s, p_pred, hurst=hurst, theta=theta, sigma_err=sigma_err, saturating=saturating
        ),
    )


def classic_bm_vs_ou_ratio(horizon_s: float, p_pred: float, *, theta: float) -> float:
    """BM-quoted p (d = k σ √T) versus OU first-passage at that barrier.

    Money-man's comparison. Not the live ``T^{0.05}`` slice. At fixed BM
    p it overrates long T more — the opposite monotone from 0.13 / 0.81.
    """
    if horizon_s <= 0.0:
        raise ValueError("horizon_s must be positive")
    if not 0.0 < p_pred < 1.0:
        raise ValueError("p_pred must lie in (0, 1)")
    if theta < 0.0:
        raise ValueError("theta must be non-negative")
    distance = k_from_predicted_p(p_pred) * (horizon_s**0.5)
    return _classic_ou_touch_p(horizon_s, distance, theta=theta) / p_pred


def money_man_bm_over_ou() -> float:
    """BM/OU at money-man's point (p ≈ 0.25, θT ≈ 1.5). Target ≈ 2.45."""
    ratio = classic_bm_vs_ou_ratio(1.5, MONEY_MAN_P, theta=1.0)
    return 1.0 / ratio


def tap_band_has_good_news(short_x: float, long_x: float) -> bool:
    """True if a hypothesis claims actual > predicted in the tap band."""
    return short_x > 1.0 or long_x > 1.0


def _consistent_split(short_x: float, long_x: float) -> bool:
    """Right sign, right order, a real hole at short T, no good news."""
    if tap_band_has_good_news(short_x, long_x):
        return False
    if not (short_x < long_x):
        return False
    if short_x > 0.50:
        return False
    if long_x < 0.35:
        return False
    return True


def verdict(hypothesis: str) -> HorizonVerdict:
    """Structured kill / keep for the three named branches plus the joint.

    Keys: ``hypothesis``, ``consistent``, ``predicted_short_x``,
    ``predicted_long_x``, ``note``.
    """
    key = hypothesis.strip().upper()
    if key in {"A", "HURST", "HURST_FLOOR", "1"}:
        short_x, long_x = shortest_vs_longest_ratio(hurst=HOUSE_H, theta=0.0, sigma_err=1.0)
        high_s, high_l = shortest_vs_longest_ratio(hurst=HOUSE_H_HIGH, theta=0.0, sigma_err=1.0)
        low_s, low_l = shortest_vs_longest_ratio(hurst=ESTIMATOR_H, theta=0.0, sigma_err=1.0)
        note = (
            "Killed. H-floor vs house 0.09 at fixed predicted p, same 2Φ "
            f"map, sigma matched: short={short_x:.2f}× long={long_x:.2f}× "
            f"(H=0.15: {high_s:.2f}× / {high_l:.2f}×). Long more "
            "understated / underconfident — backwards vs 0.13 / 0.81. "
            f"Vs true H below the floor (H={ESTIMATOR_H}): "
            f"{low_s:.2f}× / {low_l:.2f}×, mild overrate that worsens "
            "with T, still the wrong monotone. A single wrong H is "
            "one-sided; neither side is the table."
        )
        return {
            "hypothesis": "A",
            "consistent": False,
            "predicted_short_x": short_x,
            "predicted_long_x": long_x,
            "note": note,
        }

    if key in {"B", "OU", "MEAN_REVERSION", "MR", "MISSING_OU", "2"}:
        short_x, long_x = shortest_vs_longest_ratio(
            hurst=LIVE_H, theta=MATCHED_OU_THETA, sigma_err=1.0, saturating=True
        )
        classic_s = classic_bm_vs_ou_ratio(SHORT_HORIZON_S, P_SLICE, theta=0.30)
        classic_l = classic_bm_vs_ou_ratio(LONG_HORIZON_S, P_SLICE, theta=0.30)
        mm = money_man_bm_over_ou()
        note = (
            "Killed. Missing OU (live T^H hack vs saturating first-passage), "
            f"sigma matched, θ={MATCHED_OU_THETA:g}: short={short_x:.2f}× "
            f"long={long_x:.2f}×. Long more understated / underconfident "
            "— backwards. Classic BM-quoted p (d ∝ √T) vs OU overrates "
            f"long T more: {classic_s:.2f}× / {classic_l:.2f}×. Money-man "
            f"BM/OU at p=0.25, θT=1.5 is {mm:.2f} (target 2.45). Neither "
            "reading is 0.13 vs 0.81. Good news on the matched-σ branch, "
            "rejected."
        )
        return {
            "hypothesis": "B",
            "consistent": False,
            "predicted_short_x": short_x,
            "predicted_long_x": long_x,
            "note": note,
        }

    if key in {"C", "SIGMA", "SIGMA_ERR", "TICK_SIGMA", "3"}:
        short_x, long_x = shortest_vs_longest_ratio(hurst=LIVE_H, theta=0.0, sigma_err=2.0)
        note = (
            "Killed for the split. Uniform overstated σ (tick dp^2/dt vs "
            "seconds-scale tape vol) at fixed model p=0.41, mu=0 "
            f"reflection, same H: short={short_x:.2f}× long={long_x:.2f}× "
            "at r=2 — T-flat. Same k → same 2Φ(-k r) at every horizon. "
            "Fat σ can set the level below 1; it cannot open 0.13 vs 0.81. "
            "A 16.6× raw estimator would crush the tap band to ~0."
        )
        return {
            "hypothesis": "C",
            "consistent": False,
            "predicted_short_x": short_x,
            "predicted_long_x": long_x,
            "note": note,
        }

    if key in {"D", "COMBINATION", "COMBO", "JOINT", "TICK_X_SATURATING"}:
        short_x, long_x = shortest_vs_longest_ratio(
            hurst=LIVE_H,
            theta=COMBINATION_THETA,
            sigma_err=COMBINATION_SIGMA_ERR,
            saturating=True,
        )
        no_fat_s, no_fat_l = shortest_vs_longest_ratio(
            hurst=LIVE_H, theta=COMBINATION_THETA, sigma_err=1.0, saturating=True
        )
        consistent = _consistent_split(short_x, long_x)
        note = (
            "Direction only. Joint tick-σ × T^{0.05} against saturating "
            f"tape (r={COMBINATION_SIGMA_ERR:g}, θ={COMBINATION_THETA:g}): "
            f"short={short_x:.2f}× long={long_x:.2f}×. Sign and a "
            "factor-of-two hole at short T match the table; 0.13 and "
            "0.81 are *not* recovered to three decimals and this is not "
            "the 111,381-cell table. Fat tick-σ dominates the level "
            "(matched σ on the same tape is "
            f"{no_fat_s:.2f}× / {no_fat_l:.2f}× — good news, killed as B). "
            "The split is the live time-flat map versus a tape that still "
            "accumulates then saturates. A, B, C stay killed. Too small "
            "to claim a shadow move."
        )
        return {
            "hypothesis": "D",
            "consistent": consistent,
            "predicted_short_x": short_x,
            "predicted_long_x": long_x,
            "note": note,
        }

    raise ValueError(f"unknown hypothesis {hypothesis!r}; use A, B, C, or D")


def evaluate_all() -> dict[str, HorizonVerdict]:
    return {name: verdict(name) for name in ("A", "B", "C", "D")}

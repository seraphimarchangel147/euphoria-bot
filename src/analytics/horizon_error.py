"""Horizon-structured error in the live barrier-touch quote.

The live displacement hack is ``spread = sigma * T^hurst`` with hurst
clamped to [0.05, 0.50] and pinned at the 0.05 floor (the estimator wants
lower; house quotes imply ~0.09). Predicted touch is the reflection map
``p = 2 Φ(-d / spread)``. The table that matters is sliced at *predicted*
p, not at true p.

On 111,381 labelled cells, miscalibration is monotone in horizon. At
predicted p = 0.41 the shortest bucket realises 0.13× of the quote
(actual ≈ 0.053) and the longest realises 0.81× (actual ≈ 0.332). The
model is ~3× optimistic in the tap band p = 0.10–0.45, calibrated at
both extremes, and 16–20× too *low* at tiny p. Spearman(predicted,
actual) = +0.996. We have never been under-confident in 0.10–0.45.

Conclusion. The 0.13 vs 0.81 split is not a single wrong Hurst and not
a single wrong σ. Holding predicted p fixed, a too-small H (0.05 vs
0.09–0.15) in the *same* 2Φ map pushes actual/pred above 1 and further
above 1 as T grows — the opposite sign from the table (we still see
0.81× < 1 at long T). Classic missing mean reversion (BM-quoted p, so
d ∝ √T, versus OU first-passage) overrates the *long* bucket more;
money-man's Euler (BM p ≈ 0.25, θT ≈ 1.5 → BM/OU ≈ 2.45; near-barrier
p > 0.75 → ratio ≈ 1) is reproduced, and that monotone is the wrong
one. Overstated σ at fixed predicted p and the same H is T-flat: the
"max leverage at short T" claim is a fixed-*distance* statement, not a
fixed-*p* statement.

What does match the table's sign is a combination. The live T^{0.05}
map makes the same predicted p imply almost the same barrier at 5s and
at 40s. A real first-passage (BM clock, optional OU well) then realises
far more of that quote at 40s than at 5s. Overstated σ is the term that
puts *both* ratios below 1; without it the H = 0.05 vs √T gap
under-predicts (good news, rejected). A modest OU term is secondary: it
keeps the long bucket from crossing 1 once σ is fat. The split itself
is the missing first-passage time-scaling in the live quote, not the
H-floor and not BM-vs-OU at a √T-fixed p.

The helpers recover the sign and a factor-of-two hole at short T. They
do not recover 0.13 and 0.81 to three decimals. This is analysis plus a
pin test, not a shadow-traded model change.

Something we were not looking for: fat σ makes tiny-p *more* overrated,
so the measured 16–20× under-prediction at tiny p is a separate hole
(floor, tick discreteness, or selection). Rank order can stay almost
perfect while the level is a lie.
"""

from __future__ import annotations

import math
from statistics import NormalDist
from typing import TypedDict

LIVE_H = 0.05
HOUSE_H = 0.09
HOUSE_H_HIGH = 0.15
SHORT_HORIZON_S = 5.0
LONG_HORIZON_S = 40.0
P_SLICE = 0.41
MEASURED_SHORT_X = 0.13
MEASURED_LONG_X = 0.81
N_LABELLED_CELLS = 111_381
TAP_BAND = (0.10, 0.45)

# Combination defaults: BM-scale first-passage clock, modest OU, fat live σ.
# Documented as an order-of-magnitude stand-in, not a fit of 0.13 / 0.81.
COMBINATION_HURST = 0.50
COMBINATION_THETA = 0.15
COMBINATION_SIGMA_ERR = 4.0

# Money-man Euler pin: BM p ≈ 0.25, θT ≈ 1.5 → BM/OU ≈ 2.45.
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
    """Invert ``p = 2 Φ(-k)`` for the live reflection map."""
    p = min(max(float(p_pred), 1e-12), 1.0 - 1e-12)
    return -_N.inv_cdf(p / 2.0)


def touch_p_from_k(k: float) -> float:
    if k <= 0.0:
        return 1.0
    return min(1.0, 2.0 * _phi(-k))


def live_implied_distance(horizon_s: float, p_pred: float, *, sigma_err: float = 1.0) -> float:
    """Barrier in true-σ units implied by a live ``T^{0.05}`` quote at ``p_pred``.

    ``d / σ_true = k(p) * (σ_live / σ_true) * T^{0.05}``. Same predicted p
    therefore holds distance almost fixed across 5s and 40s.
    """
    if horizon_s <= 0.0:
        raise ValueError("horizon_s must be positive")
    if sigma_err <= 0.0:
        raise ValueError("sigma_err must be positive")
    return k_from_predicted_p(p_pred) * sigma_err * (horizon_s**LIVE_H)


def _ou_reach_factor(theta_T: float) -> float:
    """First-passage reach of OU over BM, money-man calibrated.

    ``1 / (1 + 0.55 (1 - e^{-θT}))`` is 1 at θT = 0 and ≈ 0.70 at
    θT = 1.5, so BM p = 0.25 maps to BM/OU ≈ 2.45. This is the *far*
    barrier factor; close barriers are blended in ``_ou_touch_p``.
    """
    x = max(float(theta_T), 0.0)
    return 1.0 / (1.0 + 0.55 * (1.0 - math.exp(-x)))


def _ou_touch_p(horizon_s: float, distance: float, *, hurst: float, theta: float) -> float:
    """Deterministic OU / fBM first-passage stand-in (no Monte Carlo).

    Far from the OU well the money-man reach factor suppresses hits
    (classic BM-vs-OU). Inside the well the process stays near the
    origin and retries a nearby barrier, so finite-T hit probability
    can *exceed* BM — that is the "even an OU has time to wander"
    regime the live T^{0.05} slice sits in.
    """
    spread = horizon_s**hurst
    if spread <= 0.0:
        return 1.0
    k_bm = distance / spread
    p_bm = touch_p_from_k(k_bm)
    if theta <= 0.0:
        return p_bm

    theta_T = theta * horizon_s
    far_factor = _ou_reach_factor(theta_T)
    p_far = touch_p_from_k(k_bm / far_factor)

    sigma_inf = 1.0 / (2.0 * theta) ** 0.5
    d_over_inf = distance / sigma_inf
    # Extra far-well suppression when the barrier sits outside ~2 σ_inf.
    if d_over_inf > 2.0:
        extra = math.exp(-0.8 * (d_over_inf - 2.0) * (1.0 - math.exp(-min(theta_T, 8.0))))
        p_far = touch_p_from_k(k_bm / max(far_factor * extra, 1e-9))

    # Nearby retries: survival stacked over a θT-scaled attempt count.
    p_near = 1.0 - (1.0 - p_bm) ** (1.0 + 0.35 * theta_T)
    w_near = math.exp(-(d_over_inf**2))
    return w_near * p_near + (1.0 - w_near) * p_far


def predicted_to_actual_ratio(
    horizon_s: float,
    p_pred: float,
    *,
    hurst: float,
    theta: float,
    sigma_err: float,
) -> float:
    """Actual / predicted under a hypothesized true process.

    The predicted side is always the live quote: ``p = 2Φ(-d / (σ T^{0.05}))``.
    ``hurst`` is the true displacement exponent (0.50 = BM clock, 0.09 =
    house). ``theta`` is OU pullback in 1/s (0 = no mean reversion).
    ``sigma_err`` is σ_live / σ_true (>1 = overstated σ).
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
    p_actual = _ou_touch_p(horizon_s, distance, hurst=hurst, theta=theta)
    return p_actual / p_pred


def shortest_vs_longest_ratio(
    p_pred: float = P_SLICE,
    *,
    hurst: float,
    theta: float,
    sigma_err: float,
    short_s: float = SHORT_HORIZON_S,
    long_s: float = LONG_HORIZON_S,
) -> tuple[float, float]:
    """``(short_x, long_x)`` on the table slice at ``p_pred`` (default 0.41)."""
    return (
        predicted_to_actual_ratio(short_s, p_pred, hurst=hurst, theta=theta, sigma_err=sigma_err),
        predicted_to_actual_ratio(long_s, p_pred, hurst=hurst, theta=theta, sigma_err=sigma_err),
    )


def classic_bm_vs_ou_ratio(horizon_s: float, p_pred: float, *, theta: float) -> float:
    """BM-quoted p (d = k σ √T) versus OU first-passage at that same barrier.

    This is money-man's comparison. It is *not* the live T^{0.05} slice.
    At fixed BM p it overrates long T more — the opposite short-vs-long
    pattern from the 0.13 / 0.81 table.
    """
    if horizon_s <= 0.0:
        raise ValueError("horizon_s must be positive")
    if not 0.0 < p_pred < 1.0:
        raise ValueError("p_pred must lie in (0, 1)")
    if theta < 0.0:
        raise ValueError("theta must be non-negative")
    k = k_from_predicted_p(p_pred)
    distance = k * (horizon_s**0.5)
    p_actual = _ou_touch_p(horizon_s, distance, hurst=0.5, theta=theta)
    return p_actual / p_pred


def money_man_bm_over_ou() -> float:
    """BM/OU at money-man's point (p ≈ 0.25, θT ≈ 1.5). Target ≈ 2.45."""
    theta = MONEY_MAN_THETA_T / 1.5
    ratio = classic_bm_vs_ou_ratio(1.5, MONEY_MAN_P, theta=theta)
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
    # Factor-of-two hole at short T is the honest bar, not 0.13 to 3dp.
    if short_x > 0.50:
        return False
    if long_x < 0.35:
        return False
    return True


def verdict(hypothesis: str) -> HorizonVerdict:
    """Structured kill / keep for A, B, C, or D.

    Keys: ``hypothesis``, ``consistent``, ``predicted_short_x``,
    ``predicted_long_x``, ``note``.
    """
    key = hypothesis.strip().upper()
    if key in {"A", "HURST", "HURST_FLOOR"}:
        short_x, long_x = shortest_vs_longest_ratio(hurst=HOUSE_H, theta=0.0, sigma_err=1.0)
        high_s, high_l = shortest_vs_longest_ratio(hurst=HOUSE_H_HIGH, theta=0.0, sigma_err=1.0)
        note = (
            "Killed. Too-small H (0.05 vs 0.09–0.15) at fixed predicted p, "
            f"same 2Φ map: short={short_x:.2f}× long={long_x:.2f}× at H=0.09; "
            f"short={high_s:.2f}× long={high_l:.2f}× at H=0.15. Both sides "
            "sit above 1 and rise with T (long more underpredicted). The "
            "table is 0.13× / 0.81×, still below 1 at long T. A single "
            "wrong H is one-sided; this side is the wrong one. The 0.04–"
            "0.10 gap is also too small to open a 6× short-vs-long hole."
        )
        return {
            "hypothesis": "A",
            "consistent": False,
            "predicted_short_x": short_x,
            "predicted_long_x": long_x,
            "note": note,
        }

    if key in {"B", "OU", "MEAN_REVERSION", "MR"}:
        live_s, live_l = shortest_vs_longest_ratio(hurst=0.5, theta=COMBINATION_THETA, sigma_err=1.0)
        classic_s = classic_bm_vs_ou_ratio(SHORT_HORIZON_S, P_SLICE, theta=0.30)
        classic_l = classic_bm_vs_ou_ratio(LONG_HORIZON_S, P_SLICE, theta=0.30)
        mm = money_man_bm_over_ou()
        note = (
            "Killed as a standalone. Classic BM-quoted p (d ∝ √T) vs OU "
            f"overrates long T more: short={classic_s:.2f}× long={classic_l:.2f}× "
            f"at θ=0.30. Money-man BM/OU at p=0.25, θT=1.5 is {mm:.2f} "
            "(target 2.45); near-barrier ratio → 1. That is the opposite "
            "monotone from 0.13 vs 0.81. On the *live* T^{0.05} slice with "
            f"correct σ, wander gives short={live_s:.2f}× long={live_l:.2f}× "
            "— right order, both above 1 (T^{0.05} << √T under-predicts). "
            "Good news, rejected. The user's 'one increment vs wander' "
            "ordering only becomes an overrate once σ is also fat (see D)."
        )
        return {
            "hypothesis": "B",
            "consistent": False,
            "predicted_short_x": live_s,
            "predicted_long_x": live_l,
            "note": note,
        }

    if key in {"C", "SIGMA", "SIGMA_ERR"}:
        short_x, long_x = shortest_vs_longest_ratio(hurst=LIVE_H, theta=0.0, sigma_err=2.0)
        note = (
            "Killed for the split. Overstated σ at fixed predicted p and "
            f"the same H is T-flat: short={short_x:.2f}× long={long_x:.2f}× "
            "at σ_live/σ_true=2. Same k maps to the same 2Φ(-k r) at every "
            "horizon. 'Max leverage at short T' is true at fixed distance, "
            "not at fixed p. Fat σ can set the *level* below 1; it cannot "
            "open 0.13 vs 0.81. A 16.6× raw estimator would crush the tap "
            "band to ~0, so the live number after smoothing is not 16.6."
        )
        return {
            "hypothesis": "C",
            "consistent": False,
            "predicted_short_x": short_x,
            "predicted_long_x": long_x,
            "note": note,
        }

    if key in {"D", "COMBINATION", "COMBO"}:
        short_x, long_x = shortest_vs_longest_ratio(
            hurst=COMBINATION_HURST,
            theta=COMBINATION_THETA,
            sigma_err=COMBINATION_SIGMA_ERR,
        )
        no_ou_s, no_ou_l = shortest_vs_longest_ratio(
            hurst=COMBINATION_HURST, theta=0.0, sigma_err=COMBINATION_SIGMA_ERR
        )
        consistent = _consistent_split(short_x, long_x)
        note = (
            "Kept, with an honest hole. Live T^{0.05} quote at fixed p "
            "holds d nearly fixed; a BM-clock first-passage then realises "
            f"more at 40s than at 5s. With σ_err={COMBINATION_SIGMA_ERR:g} "
            f"and θ={COMBINATION_THETA:g}: short={short_x:.2f}× "
            f"long={long_x:.2f}×. Sign and a factor-of-two short hole "
            "match the table; 0.13 and 0.81 are *not* recovered to three "
            "decimals. The split is dominated by missing first-passage "
            "time-scaling in the live map. Fat σ dominates the level "
            "(both < 1). OU is secondary: the same σ without pullback "
            f"gives short={no_ou_s:.2f}× long={no_ou_l:.2f}× "
            f"{'(long crosses 1 — good news)' if no_ou_l > 1.0 else ''}. "
            "A and classic B stay killed. Too small to claim a shadow move."
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

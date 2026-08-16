"""Empirical winner's-curse / selection-rank calibrator.

The live policy takes argmax EV over ~180 quoted cells that share one
price path. Closed-form μ_N (expected max of N i.i.d. N(0,1) errors)
overstates that bias: tape-shared residuals do not give 180 independent
chances to get lucky. This module measures the hole on the cells we
actually pick, keyed by selection-rank band.

Beta buckets use prior_weight=8, one-sided LCB z=1.6449, and lift caps
MAX_LIFT_FACTOR=4, MAX_LIFT_ABSOLUTE=0.01. A cold rank-0 bucket returns
the incoming p unchanged — it never manufactures edge. Decisions use LCB.

Policy.choose (not implemented here) should run the would-be winner
through ``adjust_rank(rank=0)`` and sit if LCB fails MIN_EDGE=0.05 or
MIN_P_LCB=0.02.

Do not ship μ_N as the correction. ``mu_N`` is a comparison helper only.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

PRIOR_WEIGHT = 8.0
LCB_Z = 1.6449  # Φ^{-1}(0.95), one-sided 95%
MAX_LIFT_FACTOR = 4.0
MAX_LIFT_ABSOLUTE = 0.01
MIN_TRUSTED_N = 20
MIN_BOOTSTRAP_WINDOWS = 50
MIN_EDGE = 0.05
MIN_P_LCB = 0.02

# Selection rank 0 is the chosen cell; the rest are runners-up on the
# same quoted window. Bands match how far the cell sat from argmax EV.
RANK_BANDS: tuple[tuple[int, int | None, str], ...] = (
    (0, 0, "0"),
    (1, 2, "1-2"),
    (3, 5, "3-5"),
    (6, 15, "6-15"),
    (16, None, "16+"),
)

POLICY_NOTE = (
    "Policy.choose should run the would-be winner through "
    "adjust_rank(rank=0) and sit if LCB fails MIN_EDGE=0.05 or "
    "MIN_P_LCB=0.02. RankCalibrator does not implement Policy."
)


def _clip01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return float(value)


def rank_band_key(rank: int) -> str:
    """Map a 0-based selection rank onto a Beta-bucket key."""
    r = max(0, int(rank))
    for lo, hi, key in RANK_BANDS:
        if hi is None:
            if r >= lo:
                return key
        elif lo <= r <= hi:
            return key
    return "16+"


def mu_N(n: int) -> float:
    """E[max of n i.i.d. standard normals].

    Comparison helper only. This is the closed-form selection term that
    overstates tape-shared errors; it is not the live correction.
    """
    n = int(n)
    if n <= 1:
        return 0.0
    # n ∫ x φ(x) Φ(x)^{n-1} dx, centred on the Gaussian extreme.
    center = math.sqrt(2.0 * math.log(n))
    lo = center - 8.0
    hi = center + 4.0
    steps = 4000
    dx = (hi - lo) / steps
    acc = 0.0
    nm1 = n - 1
    inv_sqrt_2 = 1.0 / math.sqrt(2.0)
    inv_sqrt_2pi = 1.0 / math.sqrt(2.0 * math.pi)
    for i in range(steps + 1):
        x = lo + i * dx
        cdf = 0.5 * (1.0 + math.erf(x * inv_sqrt_2))
        if cdf <= 0.0:
            continue
        log_term = nm1 * math.log(cdf)
        if log_term < -700.0:
            continue
        phi = math.exp(-0.5 * x * x) * inv_sqrt_2pi
        fx = x * n * phi * math.exp(log_term)
        weight = 0.5 if i == 0 or i == steps else 1.0
        acc += weight * fx
    return acc * dx


def closed_form_displayed(p_true: float, n: int, sigma: float) -> float:
    """I.i.d. additive-Gaussian displayed probability.

    ``p_true + σ μ_N``. Comparison only — this is the mapping that turns
    a true 0.35 into a displayed 0.60 when σ μ_N = 0.25. Do not use it
    to correct live quotes.
    """
    return _clip01(float(p_true) + float(sigma) * mu_N(n))


def _beta_mean_lcb(alpha: float, beta: float, z: float = LCB_Z) -> tuple[float, float]:
    total = alpha + beta
    if total <= 0.0:
        return 0.0, 0.0
    mean = alpha / total
    var = (alpha * beta) / (total * total * (total + 1.0))
    std = math.sqrt(max(0.0, var))
    return mean, max(0.0, mean - float(z) * std)


def _as_row(item: Any) -> tuple[float, int, bool, Any]:
    if isinstance(item, Mapping):
        p = item.get("p_model", item.get("p"))
        rank = item.get("rank")
        touched = item.get("touched", item.get("hit"))
        return float(p), int(rank), bool(touched), item.get("now")
    p, rank, touched, *rest = item
    now = rest[0] if rest else None
    return float(p), int(rank), bool(touched), now


def _iter_rows(window: Any) -> Iterable[Any]:
    if isinstance(window, Mapping):
        yield window
        return
    yield from window


def window_s(window: Any) -> float | None:
    """Rank-0 winner's-curse residual for one quoted window.

    s = mean(p_model − 1{touched}) on rank-0 cells of a single shared
    price path. Positive s means the chosen cell's model probability
    overstated the tape.
    """
    residual = 0.0
    n = 0
    for item in _iter_rows(window):
        p_model, rank, touched, _now = _as_row(item)
        if rank != 0:
            continue
        residual += _clip01(p_model) - (1.0 if touched else 0.0)
        n += 1
    if n == 0:
        return None
    return residual / n


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _randint(rng: Any, n: int) -> int:
    if hasattr(rng, "randrange"):
        return int(rng.randrange(n))
    if hasattr(rng, "integers"):
        return int(rng.integers(0, n))
    return int(rng.randint(0, n - 1))


@dataclass(frozen=True)
class RankAdjustment:
    p_model: float
    p_cal: float
    p_lcb: float
    n: int
    key: str
    trusted: bool


@dataclass
class _Bucket:
    n: int = 0
    hits: float = 0.0
    sum_p: float = 0.0


@dataclass
class RankCalibrator:
    """Beta rank-band calibrator. Pure: no I/O, no clock unless ``now`` is passed."""

    prior_weight: float = PRIOR_WEIGHT
    lcb_z: float = LCB_Z
    max_lift_factor: float = MAX_LIFT_FACTOR
    max_lift_absolute: float = MAX_LIFT_ABSOLUTE
    min_trusted_n: int = MIN_TRUSTED_N
    _buckets: dict[str, _Bucket] = field(default_factory=dict, init=False, repr=False)
    last_now: Any = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self._buckets = {key: _Bucket() for _lo, _hi, key in RANK_BANDS}

    def _bucket(self, rank: int) -> _Bucket:
        return self._buckets[rank_band_key(rank)]

    def observe_ranked(
        self,
        p_model: float,
        rank: int,
        touched: bool,
        *,
        now: Any = None,
    ) -> None:
        """Record one quoted cell at its selection rank (0 = chosen)."""
        if now is not None:
            self.last_now = now
        bucket = self._bucket(int(rank))
        bucket.n += 1
        bucket.hits += 1.0 if touched else 0.0
        bucket.sum_p += _clip01(float(p_model))

    def observe_window(self, rows: Iterable[Any]) -> None:
        """Record every cell of one shared-tape quoted window."""
        for item in _iter_rows(rows):
            p_model, rank, touched, now = _as_row(item)
            self.observe_ranked(p_model, rank, touched, now=now)

    def _cap_up(self, p_model: float, raw: float) -> float:
        """Never lift past the factor / absolute caps. Haircuts pass through."""
        ceiling = min(p_model * self.max_lift_factor, p_model + self.max_lift_absolute, 1.0)
        return _clip01(min(raw, ceiling))

    def adjust_rank(self, p_model: float, rank: int) -> RankAdjustment:
        """Shrink ``p_model`` by the rank-band posterior. Decisions use LCB."""
        p = _clip01(float(p_model))
        key = rank_band_key(int(rank))
        bucket = self._buckets[key]
        n = bucket.n
        if n < self.min_trusted_n:
            # Cold: incoming p unchanged. Never mint a lift from thin n.
            return RankAdjustment(p_model=p, p_cal=p, p_lcb=p, n=n, key=key, trusted=False)

        p_bar = bucket.sum_p / n
        p_bar = min(max(p_bar, 1e-12), 1.0 - 1e-12)
        alpha = self.prior_weight * p_bar + bucket.hits
        beta = self.prior_weight * (1.0 - p_bar) + (n - bucket.hits)
        mean, lcb_rate = _beta_mean_lcb(alpha, beta, self.lcb_z)
        p_cal = self._cap_up(p, p * (mean / p_bar))
        p_lcb = self._cap_up(p, p * (lcb_rate / p_bar))
        p_lcb = min(p_lcb, p_cal)
        return RankAdjustment(
            p_model=p,
            p_cal=p_cal,
            p_lcb=p_lcb,
            n=n,
            key=key,
            trusted=True,
        )

    def block_bootstrap_s(
        self,
        windows: Sequence[Any],
        *,
        n_boot: int = 400,
        rng: Any = None,
    ) -> dict[str, Any]:
        """Block-bootstrap the rank-0 residual s across quoted windows.

        s is mean(p_model − 1{touched}) on the chosen cell of each window.
        Windows are the resampling atoms because cells on one tape are
        dependent. ``too_small`` is True when n_windows < 50: the interval
        is forced to include 0 so nobody claims a significant curse.
        """
        return block_bootstrap_s(windows, n_boot=n_boot, rng=rng)


def block_bootstrap_s(
    windows: Sequence[Any],
    *,
    n_boot: int = 400,
    rng: Any = None,
) -> dict[str, Any]:
    """See ``RankCalibrator.block_bootstrap_s``."""
    stats = [s for window in windows if (s := window_s(window)) is not None]
    n_windows = len(stats)
    n_boot = int(n_boot)
    too_small = n_windows < MIN_BOOTSTRAP_WINDOWS
    if n_windows == 0:
        return {
            "s_hat": 0.0,
            "s_lo": 0.0,
            "s_hi": 0.0,
            "n_windows": 0,
            "n_boot": n_boot,
            "too_small": True,
        }
    s_hat = _mean(stats)
    rng = random.Random(0) if rng is None else rng
    boots: list[float] = []
    for _ in range(max(0, n_boot)):
        sample = [stats[_randint(rng, n_windows)] for _ in range(n_windows)]
        boots.append(_mean(sample))
    boots.sort()
    if boots:
        lo = boots[int(0.025 * (len(boots) - 1))]
        hi = boots[int(0.975 * (len(boots) - 1))]
    else:
        lo = hi = s_hat
    if too_small:
        # Refuse significance on tiny n: a pretty interval is not a proof.
        lo = min(lo, 0.0)
        hi = max(hi, 0.0)
    return {
        "s_hat": s_hat,
        "s_lo": lo,
        "s_hi": hi,
        "n_windows": n_windows,
        "n_boot": n_boot,
        "too_small": too_small,
    }


def apply_to_winner(
    p_cal: float,
    p_lcb: float,
    rank_calibrator: RankCalibrator,
) -> tuple[float, float]:
    """Re-score the would-be winner at rank 0.

    Policy.choose should run the would-be winner through
    ``adjust_rank(rank=0)`` and sit if LCB fails MIN_EDGE=0.05 or
    MIN_P_LCB=0.02. This helper applies that rank-0 pass; it does not
    implement Policy.

    A cold rank-0 bucket leaves ``(p_cal, p_lcb)`` unchanged. A trusted
    bucket may haircut. The decision LCB is never lifted (good news is
    treated as a bug until the tape earns it).
    """
    incoming_cal = _clip01(float(p_cal))
    incoming_lcb = _clip01(float(p_lcb))
    adj = rank_calibrator.adjust_rank(incoming_cal, 0)
    if not adj.trusted:
        return incoming_cal, incoming_lcb
    return adj.p_cal, min(incoming_lcb, adj.p_lcb)

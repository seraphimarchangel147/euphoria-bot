"""Touch-probability forecasting across the whole quote grid.

Euphoria pays on a single touch: price only has to *enter* a cell's price band
at some point during that cell's time column. The quantity that matters is

    P(enter [lo, hi) at some t in [t_start, t_end))

not "where will price be at t_end". This module estimates that for any cell on
the grid -- any column forward, any row above, below or diagonal -- from a
drift/diffusion fit to the recent tape. No I/O.

Math
----
Over a few seconds price is modelled as arithmetic Brownian motion
``X_t = mu*t + sigma*W_t`` in price units. Linear (not log) units are correct
here: the horizons are seconds and the moves are cents, and the cell bands are
themselves arithmetic, so this keeps the band arithmetic exact.

For a barrier a distance ``d > 0`` above the start, the running-maximum law
(reflection principle with drift) gives

    P(max_{t<=T} X_t >= d)
        = Phi((mu*T - d) / (sigma*sqrt(T)))
          + exp(2*mu*d / sigma^2) * Phi((-d - mu*T) / (sigma*sqrt(T)))

Entering a band from below means touching its lower edge, so the band problem
reduces to a barrier problem. A cell in a *future* column needs two stages:
diffuse to the column open, then touch during the column. We integrate the
barrier law over the distribution of price at the column open using fixed-node
Gauss quadrature -- deterministic, and far cheaper than Monte Carlo at the
0.5s loop rate.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Iterable, Sequence

# --- numerics --------------------------------------------------------------
MIN_SIGMA = 1e-9
_SQRT2 = math.sqrt(2.0)
_INV_SQRT_2PI = 1.0 / math.sqrt(2.0 * math.pi)

# Quadrature over the standard normal. Odd node count so z=0 is a node.
_QUAD_NODES = 41
_QUAD_HALF_WIDTH = 4.5


def norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / _SQRT2))


def _norm_pdf(z: float) -> float:
    return _INV_SQRT_2PI * math.exp(-0.5 * z * z)


def _build_quadrature() -> tuple[tuple[float, float], ...]:
    step = 2.0 * _QUAD_HALF_WIDTH / (_QUAD_NODES - 1)
    raw = []
    total = 0.0
    for i in range(_QUAD_NODES):
        z = -_QUAD_HALF_WIDTH + i * step
        w = _norm_pdf(z) * step
        raw.append((z, w))
        total += w
    return tuple((z, w / total) for z, w in raw)


_QUAD = _build_quadrature()


# --- diffusion fit ---------------------------------------------------------
DEFAULT_FIT_WINDOW_S = 45.0
MIN_FIT_PAIRS = 5
MAX_TICK_GAP_S = 4.0

# Volatility regimes, measured in cells per sqrt(second). Kept in lockstep with
# extension/grid-context.js so buckets mean the same thing on both sides.
VOL_LOW = 0.10
VOL_MED = 0.35


# The tape the house settles on: the page's own ~16Hz quote socket. The
# Redstone oracle is a different series entirely -- roughly 1.4Hz, and it
# republishes an unchanged price when nothing has arrived, so its zero-moves
# are staleness rather than a quiet market. Fitting volatility across the two
# mixed together drags sigma toward zero, which biases at-the-money cells UP
# and far cells DOWN: the exact shape of a false edge.
SETTLEMENT_SOURCES = frozenset({"page", "ws", "extension"})


@dataclass(frozen=True)
class Diffusion:
    """Drift and volatility of the recent tape, in price units per second."""

    mu: float           # shrunk drift, price units / second
    mu_raw: float       # unshrunk drift
    sigma: float        # price units / sqrt(second)
    pairs: int
    span_s: float
    ok: bool
    reason: str = ""
    source: str = ""    # which tape the fit actually used
    degraded: bool = False
    hurst: float = 0.5      # measured displacement exponent; 0.5 = Brownian
    hurst_pairs: int = 0
    hurst_fitted_to_s: float = 0.0   # longest lag the exponent was measured over
    # What this pass alone measured, before smoothing. Kept so the stability of
    # the estimator itself stays visible rather than being hidden by the fix.
    raw_sigma: float = 0.0
    raw_hurst: float = 0.0
    smoothed: bool = False
    anchored: bool = False   # sigma came from the house's grid, not our ticks

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def sd_over(self, seconds: float) -> float:
        return spread(self.sigma, max(0.0, seconds), self.hurst, self.hurst_fitted_to_s)

    def cells_sigma(self, dpl: float) -> float:
        """Volatility expressed in grid cells per sqrt(second)."""
        return self.sigma / dpl if dpl > 0 else 0.0

    def vol_bucket(self, dpl: float) -> str:
        cells = self.cells_sigma(dpl)
        if cells < VOL_LOW:
            return "LOW"
        if cells < VOL_MED:
            return "MED"
        return "HIGH"


# Lags used to measure how displacement grows with time. Spread wide enough to
# separate a random walk from a mean-reverting tape over the horizons we price.
HURST_LAGS = (1.0, 2.0, 4.0, 8.0, 16.0, 32.0)
MIN_LAG_SAMPLES = 6
MIN_HURST_LAGS = 3
# Clamped, and deliberately asymmetric: 0.5 is Brownian and the house's quotes
# imply roughly 0.09, so real tape sits at or below 0.5. A measured exponent
# ABOVE 0.5 says price accelerates away from itself, which over 5-90 seconds is
# a transient trend or estimator noise -- and it is the one direction that
# inflates far-cell reach and invents edge. Erring toward mean reversion costs
# a missed bet; erring toward trending costs the stake.
HURST_MIN, HURST_MAX = 0.05, 0.50
DEFAULT_HURST = 0.5
# Below this many lag samples the slope is noise; shrink it toward Brownian.
HURST_CONFIDENT_PAIRS = 400


def estimate_hurst(seq: Sequence[Any], *,
                   lags: Sequence[float] = HURST_LAGS) -> tuple[float, int, float]:
    """Log-log slope of RMS displacement against lag.

    ``rms(tau) ~ sigma * tau**H``. Brownian motion gives H = 0.5. A tape that
    mean-reverts gives H well below 0.5, because a move now is partly undone
    later, so displacement grows more slowly than the square root of time.
    Measuring H is what lets a 90-second cell be priced on what the tape
    actually does rather than on an assumption inherited from textbook
    diffusion. Returns (H, pairs used, longest lag actually measured).
    """
    if len(seq) < 4:
        return DEFAULT_HURST, 0, 0.0
    times = [t.ts for t in seq]
    prices = [t.price for t in seq]
    n = len(seq)
    xs: list[float] = []
    ys: list[float] = []
    used = 0
    longest = 0.0
    for lag in lags:
        if lag <= 0 or times[-1] - times[0] < lag:
            continue
        acc = 0.0
        count = 0
        j = 0
        for i in range(n):
            target = times[i] + lag
            if j < i:
                j = i
            while j < n and times[j] < target:
                j += 1
            if j >= n:
                break
            # Only accept a partner close to the requested lag.
            if abs(times[j] - target) > lag * 0.35:
                continue
            dp = prices[j] - prices[i]
            acc += dp * dp
            count += 1
        if count < MIN_LAG_SAMPLES:
            continue
        rms = math.sqrt(acc / count)
        if rms <= 0:
            continue
        xs.append(math.log(lag))
        ys.append(math.log(rms))
        used += count
        longest = max(longest, lag)
    if len(xs) < MIN_HURST_LAGS:
        return DEFAULT_HURST, used, longest

    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom <= 0:
        return DEFAULT_HURST, used, longest
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom
    # Shrink toward Brownian while the evidence is thin, so a swing in the
    # exponent cannot swing every long-horizon probability with it.
    weight = min(1.0, used / HURST_CONFIDENT_PAIRS)
    slope = DEFAULT_HURST + weight * (slope - DEFAULT_HURST)
    return min(HURST_MAX, max(HURST_MIN, slope)), used, longest


def _flat(reason: str) -> Diffusion:
    return Diffusion(mu=0.0, mu_raw=0.0, sigma=0.0, pairs=0, span_s=0.0, ok=False, reason=reason)


@dataclass
class DiffusionSmoother:
    """Carries the volatility estimate across passes.

    Each pass refits sigma from scratch over a 45-second window, so it has no
    memory at all. Measured over 50 minutes of quiet tape: consecutive
    estimates 15 seconds apart moved by a median 1.17x, a p90 of 2.11x and a
    maximum of 6.36x, and swung 16-fold end to end -- while price travelled six
    rows and moved 2% of a row per sample. The market was not doing that; the
    estimator was.

    That instability is not merely noise in the output. The policy ranks cells
    by probability and takes the maximum, so it systematically selects whichever
    square is riding the largest upward estimation error at that instant. Taking
    a maximum over noisy estimates selects the noise -- which is how a ranking
    ends up worse than picking at random.

    Sigma is smoothed in log space because its error is multiplicative and it
    cannot go negative. Hurst is smoothed linearly, and held rather than
    re-derived when the new fit lands on a clamp: an estimator that spent 43% of
    its samples pinned to a rail is reporting that it could not measure, not
    that the exponent really sat at the boundary.
    """

    half_life_s: float = 30.0
    sigma: float | None = None
    hurst: float | None = None
    fitted_to_s: float = 0.0
    last_ts: float = 0.0
    updates: int = 0
    # Recent raw fits, used as a median target. A single wild print cannot move
    # a median, so outliers are rejected without capping the step -- see
    # `_target`.
    window_s: float = 60.0
    recent: deque = field(default_factory=lambda: deque(maxlen=400))
    anchored: int = 0
    last_anchor: float | None = None

    def _weight(self, now: float) -> float:
        if self.last_ts <= 0:
            return 1.0
        dt = max(0.0, now - self.last_ts)
        if dt <= 0:
            return 0.0
        return 1.0 - 0.5 ** (dt / max(self.half_life_s, 1e-6))

    def _target(self, fresh_sigma: float, now: float) -> float:
        """Median of the recent raw fits, not the latest one.

        Capping each step against the carried value was the first attempt, and
        it fails on the case that matters. The cap binds every pass, so the
        moves compound: measured live, climbing out of a dead patch to a 40x
        higher sigma took six minutes, during which the model believed the
        market was up to 15x calmer than it was and quoted nothing as
        reachable. Yet the cap barely helps against the outlier it was meant to
        stop, because the per-pass weight is already ~2% at the live pass rate.

        A median does the job the cap was reaching for and does not have that
        cost. One 6x print among a minute of samples moves it not at all, while
        a genuine regime shift moves it in full as soon as most of the window
        agrees -- so convergence is bounded by the half-life rather than by an
        arbitrary per-step ceiling.
        """
        self.recent.append((now, fresh_sigma))
        cutoff = now - self.window_s
        while self.recent and self.recent[0][0] < cutoff:
            self.recent.popleft()
        vals = sorted(v for _, v in self.recent if v > 0)
        if not vals:
            return fresh_sigma
        mid = len(vals) // 2
        return vals[mid] if len(vals) % 2 else 0.5 * (vals[mid - 1] + vals[mid])

    def update(self, fresh: "Diffusion", *, now: float,
               anchor: float | None = None) -> "Diffusion":
        """Blend a fresh fit into the carried estimate and return the result.

        ``anchor``, when given, is the sigma at which our model reproduces the
        house's own quoted grid, and it replaces our tick fit as the target.
        Measured live, the tick fit read 0.0105 against a house-implied 0.199 --
        19x apart, and the reachability ledger's *observed* 0.154 at distance 1
        agreed with the house, not with us. A number that wrong does not become
        useful by being smoothed; the smoothing machinery still earns its keep
        here, but it should be carrying the right number.
        """
        if anchor is not None and anchor > MIN_SIGMA:
            # The house's grid is the target. Our tick fit is still recorded as
            # raw_sigma so the gap between the two stays visible -- a large
            # persistent divergence is then a signal rather than a silent bias.
            self.anchored += 1
            self.last_anchor = anchor
            w = self._weight(now)
            self.sigma = anchor if self.sigma is None else math.exp(
                math.log(self.sigma) * (1.0 - w) + math.log(anchor) * w)
            self.last_ts = now
            self.updates += 1
            if fresh.ok:
                on_rail = fresh.hurst >= HURST_MAX - 1e-9
                if self.hurst is None:
                    self.hurst = fresh.hurst
                elif not on_rail:
                    self.hurst = self.hurst * (1.0 - w) + fresh.hurst * w
                if fresh.hurst_fitted_to_s > 0:
                    self.fitted_to_s = max(self.fitted_to_s, fresh.hurst_fitted_to_s)
            return replace(
                fresh, sigma=self.sigma,
                hurst=min(HURST_MAX, max(HURST_MIN, self.hurst if self.hurst is not None else fresh.hurst)),
                hurst_fitted_to_s=self.fitted_to_s or fresh.hurst_fitted_to_s,
                raw_sigma=fresh.sigma, raw_hurst=fresh.hurst,
                smoothed=True, anchored=True, ok=True,
            )

        if not fresh.ok or fresh.sigma <= MIN_SIGMA:
            # Nothing measurable this pass. Keep what we had rather than
            # letting a gap in the tape read as "the market stopped moving".
            if self.sigma is None:
                return fresh
            return replace(fresh, sigma=self.sigma, hurst=self.hurst or fresh.hurst,
                           hurst_fitted_to_s=self.fitted_to_s or fresh.hurst_fitted_to_s,
                           ok=True, reason="carried: " + (fresh.reason or "no fit"))

        w = self._weight(now)
        target = self._target(fresh.sigma, now)
        if self.sigma is None:
            self.sigma = fresh.sigma
        else:
            self.sigma = math.exp(
                math.log(self.sigma) * (1.0 - w) + math.log(max(target, MIN_SIGMA)) * w
            )

        # The two clamps are not symmetric, so they cannot be treated alike.
        #
        # A fit at the high rail wanted to claim hurst >= 0.5 -- a full random
        # walk or better -- and was cut off there. Believing it inflates how
        # reachable distant cells look, and it is the common case: 72 of 197
        # samples in the 50-minute observation sat at 0.50 against 12 at 0.05.
        # Hold instead.
        #
        # A fit at the low rail wanted to go *below* 0.05: tape that reverts
        # harder than the model can express. Holding a higher carried value
        # there would overstate reachability just as badly, in the same
        # direction. So the low rail is accepted at normal weight.
        #
        # Both branches follow one rule: when the estimator saturates, take
        # whichever reading does not raise the claimed reachability.
        if self.hurst is None:
            self.hurst = fresh.hurst
        elif fresh.hurst < HURST_MAX - 1e-9:
            self.hurst = self.hurst * (1.0 - w) + fresh.hurst * w
        if fresh.hurst_fitted_to_s > 0:
            self.fitted_to_s = max(self.fitted_to_s, fresh.hurst_fitted_to_s)

        self.last_ts = now
        self.updates += 1
        return replace(
            fresh,
            sigma=self.sigma,
            hurst=min(HURST_MAX, max(HURST_MIN, self.hurst)),
            hurst_fitted_to_s=self.fitted_to_s or fresh.hurst_fitted_to_s,
            raw_sigma=fresh.sigma,
            raw_hurst=fresh.hurst,
            smoothed=True,
        )

    def stats(self) -> dict[str, Any]:
        return {
            "sigma": round(self.sigma, 6) if self.sigma is not None else None,
            "hurst": round(self.hurst, 4) if self.hurst is not None else None,
            "fitted_to_s": self.fitted_to_s,
            "updates": self.updates,
            "half_life_s": self.half_life_s,
            "window": len(self.recent),
            "anchored": self.anchored,
            "last_anchor": round(self.last_anchor, 6) if self.last_anchor else None,
        }


def _in_window(ticks: Sequence[Any], symbol: str, now: float, window_s: float,
               sources: frozenset[str] | None) -> list[Any]:
    out = []
    for t in ticks:
        if getattr(t, "symbol", "").upper() != symbol or getattr(t, "price", 0) <= 0:
            continue
        if t.ts > now or now - t.ts > window_s:
            continue
        if sources is not None and getattr(t, "source", "") not in sources:
            continue
        out.append(t)
    out.sort(key=lambda t: t.ts)
    return out


def estimate_diffusion(
    ticks: Sequence[Any],
    *,
    now: float | None = None,
    window_s: float = DEFAULT_FIT_WINDOW_S,
    symbol: str = "ETH",
    sources: frozenset[str] | None = SETTLEMENT_SOURCES,
) -> Diffusion:
    """Fit mu/sigma to the recent tape.

    sigma^2 is the mean of ``dp^2 / dt`` over consecutive ticks, which is the
    right estimator for irregular sampling. The drift is shrunk by its own
    t-statistic: over five seconds drift is almost entirely noise, and trusting
    a raw five-second slope is how a model talks itself into a bet.

    The fit prefers the settlement tape. A house prices against the series it
    settles on, so measuring a different feed does not estimate the same
    quantity -- it estimates the wrong one, confidently. If the settlement tape
    is too thin the fit falls back to whatever is available and says so, and
    callers can decline to trade on a degraded number.
    """
    if now is None:
        raise ValueError("estimate_diffusion requires an explicit `now`")
    symbol = symbol.upper()

    seq = _in_window(ticks, symbol, now, window_s, sources)
    used = "settlement" if sources else "all"
    degraded = False
    if sources is not None and len(seq) < MIN_FIT_PAIRS + 1:
        fallback = _in_window(ticks, symbol, now, window_s, None)
        if len(fallback) > len(seq):
            seq, used, degraded = fallback, "mixed", True

    if len(seq) < MIN_FIT_PAIRS + 1:
        flat = _flat(f"only {len(seq)} ticks in the {window_s:.0f}s fit window")
        return replace(flat, source=used, degraded=degraded)

    var_acc = 0.0
    pairs = 0
    for a, b in zip(seq, seq[1:]):
        dt = b.ts - a.ts
        if dt <= 0 or dt > MAX_TICK_GAP_S:
            continue
        # Never measure across a source boundary: the two feeds are on
        # different clocks, so `dt` there is a clock difference, not elapsed
        # time, and `dp^2/dt` is meaningless.
        if getattr(a, "source", "") != getattr(b, "source", ""):
            continue
        dp = b.price - a.price
        var_acc += (dp * dp) / dt
        pairs += 1
    if pairs < MIN_FIT_PAIRS:
        flat = _flat(f"only {pairs} usable tick pairs on the {used} tape")
        return replace(flat, source=used, degraded=degraded)

    sigma = math.sqrt(max(0.0, var_acc / pairs))
    span = seq[-1].ts - seq[0].ts
    if span <= 0:
        return _flat("zero time span")
    mu_raw = (seq[-1].price - seq[0].price) / span

    # James-Stein style shrink toward zero drift by the drift's own t-stat.
    if sigma <= MIN_SIGMA:
        mu = 0.0
    else:
        stderr = sigma / math.sqrt(span)
        t_stat = mu_raw / stderr if stderr > 0 else 0.0
        mu = mu_raw * (t_stat * t_stat) / (1.0 + t_stat * t_stat)

    hurst, hurst_pairs, fitted_to = estimate_hurst(seq)
    return Diffusion(
        mu=mu, mu_raw=mu_raw, sigma=sigma, pairs=pairs, span_s=span, ok=True,
        source=used, degraded=degraded, hurst=hurst, hurst_pairs=hurst_pairs,
        hurst_fitted_to_s=fitted_to,
    )


# --- barrier / band probabilities -----------------------------------------
def spread(sigma: float, horizon_s: float, hurst: float = 0.5,
           fitted_to_s: float = 0.0) -> float:
    """How far price typically travels in `horizon_s`, in price units.

    Brownian motion gives ``sigma * sqrt(T)`` -- the ``hurst = 0.5`` case. Real
    5-to-90-second tape is not Brownian: it mean-reverts, so displacement grows
    more slowly than the square root of time, and the house prices exactly that.

    ``fitted_to_s`` is the longest lag the exponent was actually measured over.
    Beyond it the exponent is an extrapolation, and compounding a strong
    mean-reversion exponent past its evidence is how a model concludes price
    moves 2.6 cents in ninety seconds. Measured live: cells rated below 0.02%
    at the longest horizon touched 1.96% of the time across 1,380 samples -- a
    hundredfold underestimate, all of it beyond the fitted range. Past that
    range the exponent is blended back toward Brownian, so the model widens
    where it stops knowing rather than narrowing.
    """
    if horizon_s <= 0:
        return 0.0
    if fitted_to_s and horizon_s > fitted_to_s > 0:
        inside = sigma * (fitted_to_s ** hurst)
        beyond = (hurst + DEFAULT_HURST) / 2.0
        return inside * ((horizon_s / fitted_to_s) ** beyond)
    return sigma * (horizon_s ** hurst)


def hit_prob_up(d: float, mu: float, sigma: float, horizon_s: float,
                hurst: float = 0.5, fitted_to_s: float = 0.0) -> float:
    """P(max_{t<=T} X_t >= d) for a barrier d above the start."""
    if d <= 0:
        return 1.0
    if horizon_s <= 0:
        return 0.0
    if sigma <= MIN_SIGMA:
        return 1.0 if mu * horizon_s >= d else 0.0
    s = spread(sigma, horizon_s, hurst, fitted_to_s)
    if s <= MIN_SIGMA:
        return 1.0 if mu * horizon_s >= d else 0.0
    # The reflection identity is stated for Brownian motion; with a non-Brownian
    # scale we keep its shape and substitute the measured spread. That is an
    # approximation, but it is a far smaller error than pricing a mean-reverting
    # tape as though it were a random walk.
    sigma_eff = s / math.sqrt(horizon_s)
    first = norm_cdf((mu * horizon_s - d) / s)
    expo = 2.0 * mu * d / (sigma_eff * sigma_eff)
    if expo > 700.0:                      # exp() would overflow; the law saturates
        return 1.0
    second = math.exp(expo) * norm_cdf((-d - mu * horizon_s) / s)
    return min(1.0, max(0.0, first + second))


def hit_prob_down(d: float, mu: float, sigma: float, horizon_s: float,
                  hurst: float = 0.5, fitted_to_s: float = 0.0) -> float:
    """P(min_{t<=T} X_t <= -d) for a barrier d below the start."""
    return hit_prob_up(d, -mu, sigma, horizon_s, hurst, fitted_to_s)


def _touch_from(x: float, lo: float, hi: float, dt: float, mu: float, sigma: float,
                hurst: float = 0.5, fitted_to_s: float = 0.0) -> float:
    """P(a path starting at offset x enters [lo, hi) within dt)."""
    if lo <= x < hi:
        return 1.0
    if x < lo:
        return hit_prob_up(lo - x, mu, sigma, dt, hurst, fitted_to_s)
    return hit_prob_down(x - hi, mu, sigma, dt, hurst, fitted_to_s)


def band_touch_prob(
    lo: float,
    hi: float,
    t_start: float,
    t_end: float,
    diffusion: Diffusion,
) -> float:
    """P(price enters the band [lo, hi) during [t_start, t_end]).

    ``lo``/``hi`` are price offsets relative to the current price; ``t_start``
    and ``t_end`` are seconds from now. A column that already opened is scored
    over its remaining slice; a column that already closed scores 0.
    """
    if hi <= lo:
        return 0.0
    mu, sigma, hurst = diffusion.mu, diffusion.sigma, diffusion.hurst
    fit = diffusion.hurst_fitted_to_s
    if t_end <= 0:
        return 0.0
    if t_start <= 0.0:
        return _touch_from(0.0, lo, hi, t_end, mu, sigma, hurst, fit)

    dt = max(0.0, t_end - t_start)
    sd = spread(sigma, t_start, hurst, fit)
    if sd <= MIN_SIGMA:
        return _touch_from(mu * t_start, lo, hi, dt, mu, sigma, hurst, fit)

    total = 0.0
    for z, w in _QUAD:
        total += w * _touch_from(mu * t_start + sd * z, lo, hi, dt, mu, sigma, hurst, fit)
    return min(1.0, max(0.0, total))


# --- the grid surface ------------------------------------------------------
@dataclass(frozen=True)
class CellForecast:
    """One grid cell, with its band, its timing and its modelled touch chance."""

    cell_x: int
    cell_y: int
    forward: int          # columns ahead of the current one (1 = next)
    row_offset: int       # rows from the current price row (+ = above)
    side: str             # up | down | at-price
    distance: int         # abs(row_offset)
    lo: float             # absolute band low (inclusive)
    hi: float             # absolute band high (exclusive)
    t_start: float        # seconds from now until the column opens
    t_end: float          # seconds from now until it closes
    p_touch: float
    edge_cells: float     # distance from price to the near band edge, in cells
    sd_cells: float       # sd of price at column close, in cells
    multiplier: float | None = None
    breakeven: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def neighbourhood(
    current_x: int,
    current_y: int,
    *,
    forward: int = 6,
    radius: int = 4,
) -> list[dict[str, Any]]:
    """Synthetic cell list when the page has not handed us quoted multipliers."""
    cells: list[dict[str, Any]] = []
    for f in range(1, max(1, forward) + 1):
        for dy in range(-radius, radius + 1):
            cells.append({
                "cell_x": current_x + f,
                "cell_y": current_y + dy,
                "forward": f,
                "row_offset": dy,
                "multiplier": None,
            })
    return cells


def forecast_grid(
    cells: Iterable[dict[str, Any]],
    *,
    price: float,
    dpl: float,
    column_s: float,
    current_x: int,
    now_offset_s: float,
    diffusion: Diffusion,
) -> list[CellForecast]:
    """Score every supplied cell.

    ``now_offset_s`` is how far the current column has already run, so the
    next column opens in ``column_s - now_offset_s`` seconds.
    """
    if price <= 0 or dpl <= 0 or column_s <= 0:
        return []
    current_y = math.floor(price / dpl)
    # Anchor the time axis the same way the price axis is anchored.
    #
    # `cell_y` is absolute, so the band [lo, hi) is exact. `cell_x` is absolute
    # too -- both are minted from the quote frame's own server timestamp. But
    # timing used to be rebuilt from the *relative* `forward` field re-anchored
    # to our own clock, so whenever a column boundary fell between the frame's
    # timestamp and our read of it, every cell was priced against a window a
    # full 5 seconds away from the one it names. Deriving t_start from `cell_x`
    # keeps time and price on the same footing, and a stale frame can no longer
    # mis-time anything.
    server_now_s = current_x * column_s + now_offset_s

    # If the anchor is so far off that EVERY quoted column reads as already
    # closed, the clocks disagree -- the board itself is fine. Re-anchor once,
    # uniformly, so the earliest quoted column becomes the next one to open.
    #
    # The tempting alternative is to re-time each cell from its `forward` field
    # whenever its absolute timing looks closed. Do not: `forward` is relative
    # to the quote frame's own clock, so that reintroduces exactly the split
    # between an absolute price axis and a relative time axis that had the bot
    # grading windows five seconds away from the cells it named, and paying
    # itself for wins that never happened. Shift the anchor, never the cells.
    clock_shift_s = 0.0
    xs = [int(c["cell_x"]) for c in cells
          if isinstance(c, dict) and str(c.get("cell_x", "")).lstrip("-").isdigit()]
    if xs:
        latest_close = (max(xs) + 1) * column_s - server_now_s
        if latest_close <= 0:
            target = min(xs) * column_s - max(0.0, column_s - now_offset_s)
            clock_shift_s = target - server_now_s
            server_now_s = target

    out: list[CellForecast] = []
    for raw in cells:
        try:
            cx = int(raw["cell_x"])
            cy = int(raw["cell_y"])
        except (KeyError, TypeError, ValueError):
            continue
        row_offset = int(raw.get("row_offset", cy - current_y))
        lo = cy * dpl
        hi = lo + dpl
        t_start = cx * column_s - server_now_s
        t_end = t_start + column_s
        if t_end <= 0:
            continue          # that column really has closed; it is not a bet
        try:
            raw_fwd = int(raw.get("forward") or 0)
        except (TypeError, ValueError):
            raw_fwd = 0
        # Display only. Timing above comes from cell_x and the shared anchor,
        # never from this field.
        forward = cx - current_x
        if forward <= 0 and raw_fwd > 0:
            forward = raw_fwd
        p = band_touch_prob(lo - price, hi - price, t_start, t_end, diffusion)

        if price < lo:
            edge_cells = (lo - price) / dpl
        elif price >= hi:
            edge_cells = (price - hi) / dpl
        else:
            edge_cells = 0.0

        mult = raw.get("multiplier")
        try:
            mult = float(mult) if mult is not None else None
        except (TypeError, ValueError):
            mult = None
        if mult is not None and mult <= 0:
            mult = None

        out.append(CellForecast(
            cell_x=cx,
            cell_y=cy,
            forward=forward,
            row_offset=row_offset,
            side="up" if row_offset > 0 else "down" if row_offset < 0 else "at-price",
            distance=abs(row_offset),
            lo=lo,
            hi=hi,
            t_start=t_start,
            t_end=t_end,
            p_touch=p,
            edge_cells=round(edge_cells, 4),
            sd_cells=round(diffusion.sd_over(max(t_end, 0.0)) / dpl, 4) if dpl > 0 else 0.0,
            multiplier=mult,
            breakeven=round(1.0 / mult, 6) if mult else None,
        ))
    return out

"""Ornstein–Uhlenbeck first-passage and a robust 45s tick fit.

SDE
---
    dX = −θ (X − μ) dt + σ dW

Public API is pure: no I/O, no network, no clock. Owner wires this into
the book; this module does not import signal / forecast / policy.

First-passage  P(τ ≤ T)
-----------------------
Dimensionless coordinates (when θT is not tiny):

    s = θ t
    y = (x − μ) * sqrt(2θ) / σ     # stationary N(0, 1)

so dY = −Y ds + √2 dW_s, generator  L = −y ∂_y + ∂_{yy}.

Scheme choice — Crank–Nicolson absorbing PDE, not an eigen-series
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Alili–Patie–Pedersen / Ricciardi eigen-expansions of the killed generator
need parabolic-cylinder zeros and terms that behave like e^{θT}. A live
desk already overflowed that path (exp(θT), erf of 1e8) and the numbers
looked *better* — far cells stayed tappable. CN on a truncated domain is
unconditionally stable for the diffusion piece, uses the same code for
one-sided and two-sided, and never forms e^{θT}.

Method of images is wrong for OU (state-dependent drift). Two-sided is
the same absorbing PDE on [b_lo, b_hi]. Inclusion is theoretical:
max(P_lo, P_hi) ≤ P_two ≤ 1; we lift a numerical undershoot to the lower
bound (O(h² + Δs²) CN error) and clip to 1. We do not replace the PDE
with the loose union P_lo + P_hi.

Regimes
~~~~~~~
* θ < 0, σ ≤ 0, T ≤ 0, or a non-finite input: raise ValueError.
  A failed fit must not be readable as stillness / p = 1 (dead feed →
  ten tappable cells at ev_lcb 0.51).
* θ = 0 or θT ≲ 0.05: driftless Brownian. One-sided is the reflection
  formula  2Φ(−|b−x0|/(σ√T)). Two-sided is the sine series on a strip.
* 0.05 ≲ θT ≲ 8: CN for the backward survival equation
  u_s = L u,  u = 0 on absorbing barriers,  u(·,0) = 1 inside.
  P = 1 − u(y0, θT). Far one-sided end is reflecting (paths that wander
  the other way have not touched the barrier).
* θT ≳ 8: do **not** march an exploding series. Far barriers
  (|y_b| ≳ 3.5 stationary scales): Poisson-from-MFPT tail
  P ≈ 1 − exp(−θT / m(y0→y_b)), with m the dimensionless mean first
  passage time. Near barriers: CN to s = 8, then quasistationary
  decay u(s) = u(8) e^{−λ(s−8)} with λ from the last unit of CN time.
  exp(−λ Δs) is clamped; we never call erf(1e8).

Unsafe / out-of-scope
~~~~~~~~~~~~~~~~~~~~~
* θ < 0 is momentum, not OU. Touch raises; fit returns ok=False
  reason="anti-reverting" and does **not** clamp to a fake calm θ.
* Images / Euler-Maruyama in this module's hot path: not used.
* Live shadow: unverified. Do not treat a unit-test pin as a book move.

Fit  fit_ou(times, prices) -> OuFit
-----------------------------------
45s window, 1–4 Hz, irregular timestamps (epoch seconds) OK.

* μ = median of the window, never the last tick (a spike-at-close must
  not walk the mean).
* σ = 1.4826 · MAD(Δx / √Δt). A single increment cannot own the
  estimate — the live failure was σ swinging 16.6× while price moved
  six rows. Untrimmed quadratic variation is computed only as a
  diagnostic fallback after dropping the largest |z|, never as the
  primary σ.
* θ = Theil–Sen slope of Δx on −(x−μ)Δt. If θ would be negative,
  ok=False, reason="anti-reverting"; the negative θ is left visible.
* sigma_stat = σ / √(2θ) only when θ > 0 and ok.
* n < 20 or total variation too small → ok=False, reason="thin".
* Failed fit: σ is NaN, **not** 0. Stillness (σ = 0) is what turned a
  dead feed into ten cells at ev_lcb 0.51. one_sided_touch / two_sided
  raise on that NaN.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import erf, exp, inf, isfinite, isnan, log, pi, sin, sqrt
from statistics import median
from typing import Sequence

# --- regime cuts (dimensionless time τ = θT) ---------------------------------
_TAU_BM = 0.05
_TAU_LARGE = 8.0
_Y_FAR = 3.5
_Y_PAD = 7.0
_H_Y = 0.07
_MAX_NODES = 220
_MIN_NODES = 48
_ERF_CLIP = 8.0

# --- fit floors --------------------------------------------------------------
_MIN_N = 20
_MIN_SLOPE_PAIRS = 8
_MAD_TO_SIGMA = 1.482602218505602  # 1 / Φ^{-1}(0.75)
_FAILED_FLOAT = float("nan")


@dataclass(frozen=True)
class OuFit:
    """Robust OU snapshot. Read ``ok`` before using the numbers."""

    theta: float
    sigma: float
    mu: float
    sigma_stat: float
    n: int
    ok: bool
    reason: str


# ---------------------------------------------------------------------------
# public touch
# ---------------------------------------------------------------------------

def one_sided_touch(
    x0: float,
    barrier: float,
    T: float,
    theta: float,
    sigma: float,
    mu: float = 0.0,
) -> float:
    """P(hit ``barrier`` by time T) for one-sided OU. Value in [0, 1]."""
    _check_touch_params(T, theta, sigma, x0, barrier, mu)
    if x0 == barrier:
        return 1.0
    if theta == 0.0 or theta * T < _TAU_BM:
        return _bm_one_sided(x0, barrier, T, sigma)
    return _ou_one_sided(x0, barrier, T, theta, sigma, mu)


def two_sided_touch(
    x0: float,
    b_lo: float,
    b_hi: float,
    T: float,
    theta: float,
    sigma: float,
    mu: float = 0.0,
) -> float:
    """P(hit either barrier by T). Images are wrong for OU; this is PDE."""
    _check_touch_params(T, theta, sigma, x0, b_lo, b_hi, mu)
    if b_lo >= b_hi:
        raise ValueError("b_lo must be < b_hi")
    if x0 <= b_lo or x0 >= b_hi:
        return 1.0
    if theta == 0.0 or theta * T < _TAU_BM:
        p = _bm_two_sided(x0, b_lo, b_hi, T, sigma)
    else:
        p = _ou_two_sided(x0, b_lo, b_hi, T, theta, sigma, mu)
    # Inclusion lower bound: two-sided cannot be below either one-sided.
    p_lo = one_sided_touch(x0, b_lo, T, theta, sigma, mu)
    p_hi = one_sided_touch(x0, b_hi, T, theta, sigma, mu)
    return _clip01(max(p, p_lo, p_hi))


# ---------------------------------------------------------------------------
# public fit
# ---------------------------------------------------------------------------

def fit_ou(times: Sequence[float], prices: Sequence[float]) -> OuFit:
    """Robust OU parameters from a short irregular tick window."""
    if len(times) != len(prices):
        raise ValueError("times and prices must have the same length")
    n = len(prices)
    finite_px = [float(p) for p in prices if isfinite(float(p))]
    mu = median(finite_px) if finite_px else _FAILED_FLOAT

    if n < _MIN_N:
        return _failed(n, mu, "thin")

    z: list[float] = []
    x_reg: list[float] = []
    y_reg: list[float] = []
    tv = 0.0
    for i in range(1, n):
        t0 = float(times[i - 1])
        t1 = float(times[i])
        p0 = float(prices[i - 1])
        p1 = float(prices[i])
        if not (isfinite(t0) and isfinite(t1) and isfinite(p0) and isfinite(p1)):
            continue
        dt = t1 - t0
        if dt <= 0.0:
            continue
        dx = p1 - p0
        tv += abs(dx)
        z.append(dx / sqrt(dt))
        x_reg.append(-(p0 - mu) * dt)
        y_reg.append(dx)

    scale = max(1.0, abs(mu) if isfinite(mu) else 1.0)
    if len(z) < _MIN_N - 1 or tv < max(1e-12, 1e-8 * scale):
        return _failed(n, mu, "thin")

    sigma = _robust_sigma(z)
    if not isfinite(sigma) or sigma <= 0.0:
        # Dead / flat tape. NaN, not 0 — 0 reads as stillness.
        return _failed(n, mu, "thin")

    theta = _theil_sen(x_reg, y_reg)
    if theta is None:
        return OuFit(
            theta=_FAILED_FLOAT,
            sigma=sigma,
            mu=mu,
            sigma_stat=_FAILED_FLOAT,
            n=n,
            ok=False,
            reason="thin",
        )
    if theta < 0.0:
        # Momentum. Leave the negative θ visible; do not clamp to a calm θ.
        return OuFit(
            theta=theta,
            sigma=sigma,
            mu=mu,
            sigma_stat=_FAILED_FLOAT,
            n=n,
            ok=False,
            reason="anti-reverting",
        )
    if theta == 0.0:
        return OuFit(
            theta=0.0,
            sigma=sigma,
            mu=mu,
            sigma_stat=_FAILED_FLOAT,
            n=n,
            ok=False,
            reason="thin",
        )

    sigma_stat = sigma / sqrt(2.0 * theta)
    return OuFit(
        theta=theta,
        sigma=sigma,
        mu=mu,
        sigma_stat=sigma_stat,
        n=n,
        ok=True,
        reason="ok",
    )


# ---------------------------------------------------------------------------
# validation / Brownian
# ---------------------------------------------------------------------------

def _check_touch_params(T: float, theta: float, sigma: float, *levels: float) -> None:
    for name, val in (("T", T), ("theta", theta), ("sigma", sigma)):
        if not isfinite(float(val)):
            raise ValueError(f"{name} must be finite, got {val!r}")
    for val in levels:
        if not isfinite(float(val)):
            raise ValueError("level must be finite")
    if theta < 0.0:
        raise ValueError("theta must be >= 0 (anti-reverting is not a valid OU)")
    if sigma <= 0.0:
        raise ValueError("sigma must be > 0 (σ=0 reads as stillness / p=1)")
    if T <= 0.0:
        raise ValueError("T must be > 0")


def _norm_cdf(z: float) -> float:
    # erf saturates; clip so we never hand 1e8 to the libm.
    a = z / sqrt(2.0)
    if a > _ERF_CLIP:
        return 1.0
    if a < -_ERF_CLIP:
        return 0.0
    return 0.5 * (1.0 + erf(a))


def _clip01(p: float) -> float:
    if not isfinite(p):
        raise ValueError("non-finite touch probability (numerical failure)")
    if p < 0.0:
        return 0.0
    if p > 1.0:
        return 1.0
    return p


def _bm_one_sided(x0: float, barrier: float, T: float, sigma: float) -> float:
    """Driftless Brownian first passage: 2Φ(−|b−x0|/(σ√T))."""
    dist = abs(barrier - x0)
    if dist == 0.0:
        return 1.0
    return _clip01(2.0 * _norm_cdf(-dist / (sigma * sqrt(T))))


def _bm_two_sided(x0: float, a: float, b: float, T: float, sigma: float) -> float:
    """Driftless BM in a strip. Sine series; images *are* valid for BM."""
    length = b - a
    if length <= 0.0:
        return 1.0
    xi = (x0 - a) / length
    var_term = (sigma * sigma * T) / (length * length)
    surv = 0.0
    for n in range(1, 121, 2):
        decay = -0.5 * (n * n) * (pi * pi) * var_term
        if decay < -50.0:
            break
        surv += (4.0 / (n * pi)) * sin(n * pi * xi) * exp(decay)
    return _clip01(1.0 - surv)


# ---------------------------------------------------------------------------
# dimensionless map
# ---------------------------------------------------------------------------

def _to_y(x: float, mu: float, theta: float, sigma: float) -> float:
    return (x - mu) * sqrt(2.0 * theta) / sigma


# ---------------------------------------------------------------------------
# one-sided / two-sided OU
# ---------------------------------------------------------------------------

def _ou_one_sided(
    x0: float, barrier: float, T: float, theta: float, sigma: float, mu: float
) -> float:
    tau = theta * T
    y0 = _to_y(x0, mu, theta, sigma)
    y_b = _to_y(barrier, mu, theta, sigma)
    if y0 == y_b:
        return 1.0

    far = abs(y_b) >= _Y_FAR and abs(y_b - y0) >= 2.5 and abs(y0) < abs(y_b)
    if tau >= _TAU_LARGE and far:
        return _clip01(_mfpt_poisson(y0, y_b, tau))

    # Domain: absorbing at the barrier, reflecting at a far opposite wall.
    direction = 1.0 if y_b > y0 else -1.0
    span = max(_Y_PAD, abs(y_b - y0) + 5.0)
    y_far = y_b - direction * span
    y_left, y_right = (y_far, y_b) if y_far < y_b else (y_b, y_far)
    absorb_left = y_left == y_b or abs(y_left - y_b) < 1e-15
    absorb_right = y_right == y_b or abs(y_right - y_b) < 1e-15
    return _clip01(
        1.0
        - _cn_survival(
            y0, y_left, y_right, tau, absorb_left, absorb_right, extrapolate=tau >= _TAU_LARGE
        )
    )


def _ou_two_sided(
    x0: float,
    b_lo: float,
    b_hi: float,
    T: float,
    theta: float,
    sigma: float,
    mu: float,
) -> float:
    tau = theta * T
    y0 = _to_y(x0, mu, theta, sigma)
    y_lo = _to_y(b_lo, mu, theta, sigma)
    y_hi = _to_y(b_hi, mu, theta, sigma)
    if y_lo > y_hi:
        y_lo, y_hi = y_hi, y_lo
    if y0 <= y_lo or y0 >= y_hi:
        return 1.0

    both_far = (abs(y_lo) >= _Y_FAR and abs(y_hi) >= _Y_FAR) and (
        abs(y0 - y_lo) >= 2.5 and abs(y_hi - y0) >= 2.5
    )
    if tau >= _TAU_LARGE and both_far:
        p_lo = _mfpt_poisson(y0, y_lo, tau)
        p_hi = _mfpt_poisson(y0, y_hi, tau)
        return _clip01(1.0 - (1.0 - p_lo) * (1.0 - p_hi))

    return _clip01(
        1.0
        - _cn_survival(
            y0, y_lo, y_hi, tau, True, True, extrapolate=tau >= _TAU_LARGE
        )
    )


# ---------------------------------------------------------------------------
# large-τ MFPT tail  (dimensionless dY = −Y ds + √2 dW)
# ---------------------------------------------------------------------------

def _mfpt_poisson(y0: float, y_b: float, tau: float) -> float:
    """P(hit by s=τ) ≈ 1 − exp(−τ / m). m is huge for |y_b| ≳ 4; P stays ~0."""
    m = _mfpt_dimensionless(y0, y_b)
    if m >= 1e200:
        return 0.0
    rate = tau / m
    if rate <= 0.0:
        return 0.0
    if rate > 50.0:
        return 1.0
    return 1.0 - exp(-rate)


def _mfpt_dimensionless(y0: float, y_b: float) -> float:
    """Mean first-passage time in s-units, one absorbing barrier, other at −∞.

    t(y0) = ∫_{y0}^{y_b} exp(y²/2) √(2π) Φ(y) dy   (hitting from below).
    Hitting from above flips sign. Integrand overflows only past |y|~20;
    those barriers are treated as unreachable (m = +∞).
    """
    if y_b < y0:
        return _mfpt_dimensionless(-y0, -y_b)
    if y_b - y0 < 1e-15:
        return 0.0
    if y_b > 12.0:
        return inf

    n_quad = 480
    h = (y_b - y0) / n_quad
    total = 0.0
    sqrt_2pi = sqrt(2.0 * pi)
    for i in range(n_quad):
        y = y0 + (i + 0.5) * h
        half_sq = 0.5 * y * y
        if half_sq > 60.0:
            return inf
        total += exp(half_sq) * sqrt_2pi * _norm_cdf(y) * h
    return max(total, 1e-15)


# ---------------------------------------------------------------------------
# Crank–Nicolson survival
# ---------------------------------------------------------------------------

def _cn_survival(
    y0: float,
    y_left: float,
    y_right: float,
    tau: float,
    absorb_left: bool,
    absorb_right: bool,
    extrapolate: bool = False,
) -> float:
    """Survival u(y0, s*) in dimensionless coordinates. u ∈ [0, 1]."""
    if tau <= 0.0:
        return 1.0
    width = y_right - y_left
    if width <= 1e-15:
        return 0.0
    if y0 <= y_left or y0 >= y_right:
        return 0.0

    n_int = int(round(width / _H_Y)) - 1
    n_int = max(_MIN_NODES, min(_MAX_NODES, n_int))
    h = width / (n_int + 1)
    ys = [y_left + i * h for i in range(n_int + 2)]

    s_end = _TAU_LARGE if (extrapolate and tau > _TAU_LARGE) else tau
    n_steps = int(round(s_end / 0.045))
    n_steps = max(50, min(280, n_steps))
    ds = s_end / n_steps

    # A row: α u_{i-1} + β u_i + γ u_{i+1}
    alpha = [0.0] * (n_int + 2)
    beta = [0.0] * (n_int + 2)
    gamma = [0.0] * (n_int + 2)
    inv_h2 = 1.0 / (h * h)
    inv_2h = 1.0 / (2.0 * h)
    for i in range(1, n_int + 1):
        y = ys[i]
        alpha[i] = inv_h2 + y * inv_2h
        beta[i] = -2.0 * inv_h2
        gamma[i] = inv_h2 - y * inv_2h

    # Left CN matrix (constant): I − (ds/2) A, after boundary elimination.
    sub = [0.0] * n_int
    diag = [0.0] * n_int
    sup = [0.0] * n_int
    half = 0.5 * ds
    for j in range(n_int):
        i = j + 1
        a_im, a_i, a_ip = alpha[i], beta[i], gamma[i]
        # reflecting: fold the ghost onto the first/last unknown
        if i == 1 and not absorb_left:
            a_i = a_i + a_im
            a_im = 0.0
        if i == n_int and not absorb_right:
            a_i = a_i + a_ip
            a_ip = 0.0
        sub[j] = -half * a_im
        diag[j] = 1.0 - half * a_i
        sup[j] = -half * a_ip

    u = [1.0] * (n_int + 2)
    if absorb_left:
        u[0] = 0.0
    if absorb_right:
        u[-1] = 0.0

    # Snapshots for λ at s_end-1 and s_end (large-τ only).
    u_before = 1.0
    s_mark = s_end - 1.0 if extrapolate and tau > _TAU_LARGE else None

    for step in range(n_steps):
        s_now = (step + 1) * ds
        rhs = _cn_rhs(u, alpha, beta, gamma, n_int, half, absorb_left, absorb_right)
        interior = _thomas(sub, diag, sup, rhs)
        for j, val in enumerate(interior):
            # CN is not bound-preserving; clip keeps a probability.
            if val < 0.0:
                val = 0.0
            elif val > 1.0:
                val = 1.0
            u[j + 1] = val
        if not absorb_left:
            u[0] = u[1]
        else:
            u[0] = 0.0
        if not absorb_right:
            u[-1] = u[-2]
        else:
            u[-1] = 0.0
        if s_mark is not None and s_now >= s_mark and s_now - ds < s_mark:
            u_before = _interp(ys, u, y0)

    surv = _interp(ys, u, y0)
    if not (extrapolate and tau > _TAU_LARGE):
        return surv

    # Quasistationary tail. λ from the last unit of CN time.
    if surv <= 0.0 or u_before <= 0.0:
        return 0.0
    if surv >= u_before:
        # No decay resolved (far / rare). Stay at the s=8 survival —
        # extra time only *lowers* survival; leaving it is conservative
        # for "P near 0" far-barrier calls? No: leaving u high *under*
        # estimates P. Far+large already took the MFPT branch.
        return surv
    lam = -log(surv / u_before) / max(s_end - (s_end - 1.0), 1e-12)
    if lam < 0.0:
        lam = 0.0
    extra = tau - s_end
    decay = -lam * extra
    if decay < -50.0:
        return 0.0
    return surv * exp(decay)


def _cn_rhs(
    u: list[float],
    alpha: list[float],
    beta: list[float],
    gamma: list[float],
    n_int: int,
    half: float,
    absorb_left: bool,
    absorb_right: bool,
) -> list[float]:
    """(I + (ds/2) A) u  on interior nodes, ghosts already in ``u``."""
    rhs = [0.0] * n_int
    for j in range(n_int):
        i = j + 1
        um, ui, up = u[i - 1], u[i], u[i + 1]
        if i == 1 and not absorb_left:
            um = ui
        if i == n_int and not absorb_right:
            up = ui
        # absorbing ghosts are 0 and already stored
        a_u = alpha[i] * um + beta[i] * ui + gamma[i] * up
        rhs[j] = ui + half * a_u
    return rhs


def _thomas(
    sub_t: Sequence[float],
    diag_t: Sequence[float],
    sup_t: Sequence[float],
    rhs: Sequence[float],
) -> list[float]:
    """Tridiagonal solve. Copies the template so the matrix can be reused."""
    n = len(rhs)
    sub = list(sub_t)
    diag = list(diag_t)
    sup = list(sup_t)
    d = list(rhs)
    for i in range(1, n):
        pivot = diag[i - 1]
        if abs(pivot) < 1e-18:
            raise ValueError("singular CN matrix")
        w = sub[i] / pivot
        diag[i] -= w * sup[i - 1]
        d[i] -= w * d[i - 1]
    if abs(diag[-1]) < 1e-18:
        raise ValueError("singular CN matrix")
    x = [0.0] * n
    x[-1] = d[-1] / diag[-1]
    for i in range(n - 2, -1, -1):
        x[i] = (d[i] - sup[i] * x[i + 1]) / diag[i]
    return x


def _interp(ys: Sequence[float], u: Sequence[float], y0: float) -> float:
    if y0 <= ys[0]:
        return u[0]
    if y0 >= ys[-1]:
        return u[-1]
    # ys is uniform; binary-ish linear scan is fine at N~200
    for i in range(len(ys) - 1):
        if ys[i] <= y0 <= ys[i + 1]:
            w = ys[i + 1] - ys[i]
            if w <= 0.0:
                return u[i]
            t = (y0 - ys[i]) / w
            return u[i] * (1.0 - t) + u[i + 1] * t
    return u[-1]


# ---------------------------------------------------------------------------
# robust estimators
# ---------------------------------------------------------------------------

def _robust_sigma(z: Sequence[float]) -> float:
    """σ from MAD of Δx/√Δt. One 16× increment does not own this."""
    if not z:
        return _FAILED_FLOAT
    mid = median(z)
    mad = median([abs(v - mid) for v in z])
    sigma_mad = _MAD_TO_SIGMA * mad
    if sigma_mad > 0.0 and isfinite(sigma_mad):
        return sigma_mad
    # Flat MAD (e.g. two-point tape) — trimmed QV, largest |z| dropped.
    if len(z) < 3:
        return _FAILED_FLOAT
    ordered = sorted(z, key=abs)
    trimmed = ordered[:-1]
    qv = sum(v * v for v in trimmed) / len(trimmed)
    if qv <= 0.0 or not isfinite(qv):
        return _FAILED_FLOAT
    return sqrt(qv)


def _theil_sen(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Median pairwise slope. None if the design is degenerate."""
    n = len(xs)
    slopes: list[float] = []
    for i in range(n):
        xi = xs[i]
        yi = ys[i]
        for j in range(i + 1, n):
            dx = xs[j] - xi
            if abs(dx) < 1e-18:
                continue
            slopes.append((ys[j] - yi) / dx)
    if len(slopes) < _MIN_SLOPE_PAIRS:
        return None
    return float(median(slopes))


def _failed(n: int, mu: float, reason: str) -> OuFit:
    return OuFit(
        theta=_FAILED_FLOAT,
        sigma=_FAILED_FLOAT,  # NaN, not 0
        mu=mu,
        sigma_stat=_FAILED_FLOAT,
        n=n,
        ok=False,
        reason=reason,
    )

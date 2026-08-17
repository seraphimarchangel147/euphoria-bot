"""Sigma must carry across passes. No network.

Measured over 50 minutes of live tape (200 samples, 7,976 graded cells): the
volatility estimate swung 16.6-fold end to end, consecutive samples 15 seconds
apart moved by a median 1.17x with a p90 of 2.11x and a maximum of 6.36x, and
23% of consecutive pairs moved more than 1.5x. Over that same 50 minutes price
travelled 3.09 dollars -- about six rows, 2% of a row per sample. The market was
not moving 16-fold; the estimator was.

That is not merely noisy output. The policy ranks every cell by probability and
takes the maximum, so an unstable sigma means it reliably picks whichever square
is riding the largest upward error at that instant. Taking a maximum over noisy
estimates selects the noise, which is how a ranking scored worse than random:
-0.323 per unit over 452 shadow trades against -0.18 for picking at random.

The hurst exponent had the same problem from the other end -- it sat pinned to
one of its clamps in 43% of samples, which is an estimator reporting that it
could not measure, not a market with a boundary-valued exponent.
"""
import math

from src.analytics.forecast import (
    HURST_MAX,
    HURST_MIN,
    Diffusion,
    DiffusionSmoother,
)


def _fit(sigma, hurst=0.3, ok=True):
    return Diffusion(mu=0.0, mu_raw=0.0, sigma=sigma, pairs=100, span_s=45.0,
                     ok=ok, hurst=hurst, hurst_pairs=40, hurst_fitted_to_s=20.0)


def _nofit(reason="too few ticks"):
    return Diffusion(mu=0.0, mu_raw=0.0, sigma=0.0, pairs=0, span_s=0.0,
                     ok=False, reason=reason)


def _feed(smoother, sigmas, *, start=1_700_000_000.0, step=15.0, hurst=0.3):
    """Push a series of per-pass fits through at the live sampling cadence."""
    out = []
    for i, s in enumerate(sigmas):
        out.append(smoother.update(_fit(s, hurst=hurst), now=start + i * step))
    return out


def _swing(values):
    """Largest ratio between the biggest and smallest value in a series."""
    values = [v for v in values if v > 0]
    return max(values) / min(values) if values else 1.0


def _step_ratios(values):
    return [max(a, b) / min(a, b)
            for a, b in zip(values, values[1:]) if a > 0 and b > 0]


# The live estimator's own output, reconstructed at its measured statistics:
# a 16x range, a median step of ~1.17x and occasional 6x jumps.
WILD = [0.30, 0.35, 0.29, 1.84, 0.31, 0.28, 0.26, 1.10, 0.33, 0.30,
        0.11, 0.34, 0.36, 0.29, 0.70, 0.32, 0.31, 0.27, 1.72, 0.30]


# --- the swing is what gets damped ---------------------------------------
def test_it_damps_the_swing_that_broke_the_ranking():
    raw_swing = _swing(WILD)
    assert raw_swing > 10, "the fixture must reproduce the live instability"
    out = [d.sigma for d in _feed(DiffusionSmoother(), WILD)]
    assert _swing(out) < raw_swing / 3


def test_consecutive_samples_stop_jumping():
    """The stated success measure: the step ratio moves toward 1.0."""
    raw = _step_ratios(WILD)
    out = _step_ratios([d.sigma for d in _feed(DiffusionSmoother(), WILD)])
    raw_median = sorted(raw)[len(raw) // 2]
    out_median = sorted(out)[len(out) // 2]
    assert out_median < raw_median
    assert max(out) < max(raw)
    # 23% of live pairs moved more than 1.5x. None should now.
    assert not [r for r in out if r > 1.5]


def test_one_wild_fit_cannot_move_the_estimate():
    """A single 6x print is a minority of the window, so the median ignores it."""
    s = DiffusionSmoother()
    _feed(s, [0.30] * 10, step=5.0)
    settled = s.sigma
    spike = s.update(_fit(6.0), now=1_700_000_000.0 + 55.0)
    assert abs(spike.sigma / settled - 1.0) < 0.05


def test_a_real_move_still_gets_through():
    """Damping noise must not mean ignoring the market."""
    s = DiffusionSmoother()
    _feed(s, [0.30] * 10)
    quiet = s.sigma
    _feed(s, [3.0] * 12, start=1_700_000_000.0 + 200.0)
    assert s.sigma > quiet * 5, "a sustained regime change must be tracked"


def test_climbing_out_of_a_dead_patch_takes_a_half_life_not_minutes():
    """The defect the first attempt shipped with, measured live.

    Capping each step against the carried value compounded: recovering from a
    dead patch to a 40x higher sigma took six minutes, and throughout that ramp
    the model believed the market was up to 15x calmer than it was and scored
    every cell unreachable. Convergence must be bounded by the half-life.
    """
    s = DiffusionSmoother(half_life_s=30.0)
    # A dead patch: barely any movement on the tape.
    _feed(s, [0.001] * 20, step=1.0)
    assert s.sigma < 0.002
    # The tape comes alive, 40x higher. Give it four half-lives.
    _feed(s, [0.040] * 120, start=1_700_000_000.0 + 100.0, step=1.0)
    assert s.sigma > 0.030, f"still at {s.sigma:.5f} after two minutes"


# --- weighting behaves ----------------------------------------------------
def test_the_first_reading_is_taken_whole():
    s = DiffusionSmoother()
    assert s.update(_fit(0.42), now=1_700_000_000.0).sigma == 0.42


def test_a_long_gap_weights_the_new_reading_more():
    """After several half-lives of silence the carried value is stale."""
    quick = DiffusionSmoother()
    quick.update(_fit(0.30), now=1000.0)
    quick.update(_fit(0.45), now=1001.0)

    slow = DiffusionSmoother()
    slow.update(_fit(0.30), now=1000.0)
    slow.update(_fit(0.45), now=1000.0 + 300.0)

    assert slow.sigma > quick.sigma
    assert quick.sigma < 0.32, "one second later, the old value still dominates"


def test_smoothing_happens_in_log_space():
    """Sigma's error is multiplicative and it cannot go negative."""
    # window_s is shortened so the median target is the new reading alone and
    # the log-space blend is what the assertion measures.
    s = DiffusionSmoother(half_life_s=15.0, window_s=1.0)
    s.update(_fit(0.10), now=1000.0)
    out = s.update(_fit(0.40), now=1015.0)  # exactly one half-life
    # Geometric midpoint of 0.10 and 0.40 is 0.20; arithmetic would be 0.25.
    assert math.isclose(out.sigma, 0.20, rel_tol=0.02)


def test_sigma_never_goes_negative_or_zero():
    s = DiffusionSmoother()
    for d in _feed(s, [1e-12, 0.5, 1e-9, 2.0, 1e-11]):
        assert d.sigma > 0


# --- hurst: the two clamps mean opposite things ---------------------------
def test_the_high_rail_is_held_rather_than_believed():
    """72 of 197 live samples sat at 0.50 -- the estimator asking to claim a
    full random walk after being cut off there. Believing that inflates how
    reachable distant cells look, which is the error that costs money."""
    s = DiffusionSmoother()
    _feed(s, [0.30] * 8, hurst=0.30)
    settled = s.hurst
    s.update(_fit(0.30, hurst=HURST_MAX), now=1_700_000_000.0 + 500.0)
    assert s.hurst == settled


def test_the_low_rail_is_accepted():
    """A fit at 0.05 wanted to go lower still: tape reverting harder than the
    model can express. Holding a higher value overstates reachability in
    exactly the same direction as believing the high rail would."""
    s = DiffusionSmoother()
    _feed(s, [0.30] * 8, hurst=0.30)
    settled = s.hurst
    _feed(s, [0.30] * 6, start=1_700_000_000.0 + 500.0, hurst=HURST_MIN)
    assert s.hurst < settled


def test_saturation_never_raises_claimed_reachability():
    """One rule covers both rails: a saturated fit may lower the exponent,
    never lift it."""
    for rail in (HURST_MIN, HURST_MAX):
        s = DiffusionSmoother()
        _feed(s, [0.30] * 8, hurst=0.30)
        before = s.hurst
        s.update(_fit(0.30, hurst=rail), now=1_700_000_000.0 + 500.0)
        assert s.hurst <= before + 1e-12, f"rail {rail} lifted the exponent"


def test_an_unclamped_hurst_still_moves_the_estimate():
    s = DiffusionSmoother()
    _feed(s, [0.30] * 8, hurst=0.20)
    before = s.hurst
    _feed(s, [0.30] * 8, start=1_700_000_000.0 + 500.0, hurst=0.45)
    assert s.hurst > before


def test_hurst_stays_inside_its_clamps():
    s = DiffusionSmoother()
    for d in _feed(s, [0.3] * 6, hurst=0.49):
        assert HURST_MIN <= d.hurst <= HURST_MAX


# --- a gap in the tape is not a calm market -------------------------------
def test_a_failed_fit_carries_rather_than_reading_as_stillness():
    """No ticks means we cannot see, not that the market stopped."""
    s = DiffusionSmoother()
    _feed(s, [0.30] * 6)
    dead = s.update(_nofit(),
                    now=1_700_000_000.0 + 200.0)
    assert dead.sigma == s.sigma
    assert dead.ok is True
    assert "carried" in dead.reason


def test_a_failed_fit_with_no_history_stays_failed():
    """Nothing to carry means the no-fit verdict must survive intact."""
    s = DiffusionSmoother()
    out = s.update(_nofit(), now=1000.0)
    assert out.ok is False


def test_a_carried_pass_does_not_advance_the_clock():
    """Otherwise a long dead patch would silently age out the real estimate."""
    s = DiffusionSmoother()
    s.update(_fit(0.30), now=1000.0)
    stamp = s.last_ts
    s.update(_nofit(), now=1600.0)
    assert s.last_ts == stamp


# --- the raw fit stays visible -------------------------------------------
def test_the_unsmoothed_fit_is_still_reported():
    """The fix must not hide the instability it is compensating for."""
    s = DiffusionSmoother()
    s.update(_fit(0.30), now=1000.0)
    out = s.update(_fit(0.90, hurst=0.42), now=1015.0)
    assert out.raw_sigma == 0.90
    assert out.raw_hurst == 0.42
    assert out.smoothed is True
    assert out.sigma != out.raw_sigma
    assert "raw_sigma" in out.to_dict()


def test_stats_report_what_is_being_carried():
    s = DiffusionSmoother()
    _feed(s, [0.30] * 4)
    st = s.stats()
    assert st["updates"] == 4
    assert st["sigma"] > 0
    assert st["half_life_s"] == s.half_life_s


# --- through the room -----------------------------------------------------
def _room(tmp_path, **kw):
    from src.control.room import ControlRoom
    kw.setdefault("enable_oracle", False)
    kw.setdefault("enable_wallet", False)
    kw.setdefault("dry_run", True)
    for name, stem in (("session_path", "s"), ("token_path", "t"),
                       ("calibration_path", "c"), ("bankroll_path", "b"),
                       ("traversal_path", "tr"), ("pnl_path", "p"),
                       ("player_path", "pl"), ("player_calibration_path", "pc")):
        kw.setdefault(name, tmp_path / f"{stem}.json")
    return ControlRoom(**kw)


def test_the_room_carries_sigma_between_passes(tmp_path):
    room = _room(tmp_path)
    now = 1_700_000_000.0
    # Quiet tape, then a burst -- two passes whose raw fits differ sharply.
    for i in range(120):
        room.ticks.push("ETH", 1000.0 + (0.02 if i % 2 else -0.02),
                        now - (120 - i) * 0.4, source="page")
    room.think(now=now)
    first = room.smoother.sigma
    assert first is not None and first > 0

    for i in range(120):
        room.ticks.push("ETH", 1000.0 + (3.0 if i % 2 else -3.0),
                        now + i * 0.4, source="page")
    room.think(now=now + 50.0)
    # It moved toward the burst without simply adopting it.
    assert room.smoother.sigma > first
    assert room.smoother.updates >= 2
    room.close()


def test_the_room_exposes_the_smoother_for_inspection(tmp_path):
    room = _room(tmp_path)
    st = room.snapshot()["think"].get("smoother")
    assert st is not None and "half_life_s" in st
    room.close()

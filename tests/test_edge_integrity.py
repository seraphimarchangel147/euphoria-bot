"""Guards against a *false* edge. No network.

Three failures found by analysing the live board, all of which manufacture
apparent profit rather than losing money outright — which makes them far more
dangerous than a crash.
"""
from src.analytics.calibration import Calibrator
from src.analytics.forecast import (
    SETTLEMENT_SOURCES,
    CellForecast,
    Diffusion,
    estimate_diffusion,
)
from src.analytics.policy import Bankroll, Policy
from src.analytics.signal import Tick


def _tick(px, ts, source="page"):
    return Tick("ETH", px, ts, source=source)


def _cell(p, mult, *, distance=0):
    return CellForecast(
        cell_x=10, cell_y=100, forward=1, row_offset=distance,
        side="at-price" if distance == 0 else "up", distance=distance,
        lo=50.0, hi=50.5, t_start=0.0, t_end=5.0, p_touch=p,
        edge_cells=0.0, sd_cells=0.2, multiplier=mult,
        breakeven=round(1 / mult, 6) if mult else None,
    )


# --- 1. a dead feed must not read as certainty ---------------------------
def test_a_failed_volatility_fit_refuses_to_trade():
    """sigma=0 means 'we cannot see', not 'price cannot move'.

    Observed live: a feed dropout produced sigma=0, pairs=0 — and the model
    concluded every at-the-money cell was a certainty, offering ten tappable
    cells at ev_lcb 0.51 off a tape it could not measure.
    """
    calib = Calibrator()
    for _ in range(600):
        calib.observe(0.99, 5.0, "LOW", touched=True)
    score = Policy().score_cell(
        _cell(1.0, 1.5), calib, vol_bucket="LOW",
        bankroll=Bankroll(), diffusion_ok=False,
    )
    assert score.verdict == "no-fit"
    assert score.stake == 0
    assert score.ev_lcb is None


def test_a_dead_feed_produces_no_plan_at_all():
    calib = Calibrator()
    for _ in range(600):
        calib.observe(0.99, 5.0, "LOW", touched=True)
    scores = Policy().score(
        [_cell(1.0, 1.5), _cell(1.0, 1.7), _cell(0.99, 2.0)],
        calib, vol_bucket="LOW", bankroll=Bankroll(), diffusion_ok=False,
    )
    assert all(s.verdict == "no-fit" for s in scores)
    assert Policy.choose(scores) is None


def test_the_same_cell_is_tradeable_once_the_fit_is_healthy():
    """The gate must be about the fit, not a blanket mute."""
    calib = Calibrator()
    for i in range(600):
        calib.observe(0.60, 5.0, "LOW", touched=i < 360)      # calibrated 0.60
    score = Policy().score_cell(
        _cell(0.60, 3.0), calib, vol_bucket="LOW",
        bankroll=Bankroll(), diffusion_ok=True,
    )
    assert score.verdict == "tap"
    assert score.stake > 0


# --- 2. fit the tape the house settles on --------------------------------
def test_the_fit_prefers_the_settlement_tape_over_the_oracle():
    """A stale oracle republishing one price must not flatten sigma."""
    now = 1000.0
    live = [_tick(1000.0 + (0.4 if i % 2 else -0.4), now - 40 + i * 0.4) for i in range(80)]
    # Redstone: slow, and repeating the same print — zero moves that are
    # staleness, not a quiet market.
    stale = [_tick(1000.0, now - 40 + i * 1.5, source="redstone") for i in range(25)]

    settlement = estimate_diffusion(live + stale, now=now)
    mixed = estimate_diffusion(live + stale, now=now, sources=None)
    assert settlement.ok and settlement.source == "settlement"
    assert settlement.sigma > mixed.sigma        # the oracle drags it down
    assert not settlement.degraded


def test_the_fit_falls_back_and_says_so_when_the_socket_is_quiet():
    now = 1000.0
    only_oracle = [_tick(1000.0 + i * 0.1, now - 30 + i, source="redstone") for i in range(25)]
    diff = estimate_diffusion(only_oracle, now=now)
    assert diff.ok
    assert diff.source == "mixed"
    assert diff.degraded is True


def test_volatility_is_never_measured_across_a_source_boundary():
    """Two feeds are on two clocks; dt between them is not elapsed time."""
    now = 1000.0
    a = [_tick(1000.0, now - 20 + i * 0.5) for i in range(20)]           # flat, page
    b = [_tick(1400.0, now - 20 + i * 0.5, source="redstone") for i in range(20)]
    diff = estimate_diffusion(a + b, now=now, sources=None)
    # The 400-point gap between the feeds must not be read as a price move.
    assert diff.sigma < 1.0


def test_settlement_sources_are_the_socket_feeds():
    assert "page" in SETTLEMENT_SOURCES
    assert "redstone" not in SETTLEMENT_SOURCES
    assert "dom" not in SETTLEMENT_SOURCES


# --- 3. the calibration table must expose the middle ---------------------
def test_reliability_orders_bands_numerically_not_lexicographically():
    """Sorting keys as strings put p10 between p1 and p2.

    A truncated view then showed only p0/p1/p10 and hid every mid-range
    bucket — which is precisely where every real decision is made.
    """
    calib = Calibrator()
    for band, p in enumerate((0.005, 0.03, 0.05, 0.09, 0.16, 0.26, 0.4, 0.6, 0.8, 0.95)):
        for _ in range(40):
            calib.observe(p, 5.0, "LOW", touched=False)
    table = calib.stats()["reliability"]
    mids = [row["predicted_mid"] for row in table]
    assert mids == sorted(mids), "bands must ascend"
    # The middle of the range must be reachable, not truncated away.
    assert any(0.05 < m < 0.7 for m in mids)


def test_every_warm_bucket_is_reported():
    """Nothing warm may be truncated away — the table was capped at 24 rows."""
    calib = Calibrator()
    for band in range(10):
        p = 0.005 + band * 0.09
        for _ in range(30):
            calib.observe(p, 5.0 + band, "LOW", touched=False)
    # Distinct buckets, not distinct p values: neighbouring probabilities and
    # horizons legitimately share a band.
    assert len(calib.stats()["reliability"]) == len(calib.buckets)


# --- 4. evidence must precede edge ---------------------------------------
def test_a_cold_bucket_cannot_be_tapped_however_fat_the_quote():
    """A 40x quote against an unproven bucket is not an edge, it is a guess.

    Seeded at p=0.5 a cold bucket still yields a lower bound near 0.23; times
    40 that reads as +800% EV. Observed live: seven "tappable" cells with zero
    warm buckets behind them.
    """
    score = Policy().score_cell(
        _cell(0.5, 40.0), Calibrator(), vol_bucket="LOW", bankroll=Bankroll(),
    )
    assert score.verdict == "unproven"
    assert score.stake == 0


def test_the_same_cell_taps_once_the_bucket_is_warm():
    calib = Calibrator()
    for i in range(600):
        calib.observe(0.5, 5.0, "LOW", touched=i < 300)     # calibrated at 0.5
    score = Policy().score_cell(
        _cell(0.5, 40.0), calib, vol_bucket="LOW", bankroll=Bankroll(),
    )
    assert score.verdict == "tap"
    assert score.stake > 0


def test_an_unproven_cell_never_becomes_the_plan():
    scores = Policy().score(
        [_cell(0.5, 40.0), _cell(0.4, 25.0), _cell(0.6, 12.0)],
        Calibrator(), vol_bucket="LOW", bankroll=Bankroll(),
    )
    assert all(s.verdict == "unproven" for s in scores)
    assert Policy.choose(scores) is None


# --- 5. a bucket may correct a probability, not invent one ----------------
def test_a_warm_bucket_cannot_lift_an_impossible_cell():
    """The live failure: p_model 0.0 became p_cal 0.072, and a 99.8x quote
    turned that into +475% EV on a cell four rows away at 89 seconds."""
    calib = Calibrator()
    # A bucket full of *reachable* near-zero cells that do sometimes touch.
    for i in range(900):
        calib.observe(0.009, 90.0, "LOW", touched=i < 65)     # ~7% observed
    out = calib.adjust(0.0, 90.0, "LOW")                      # a hopeless cell
    assert out.p_cal <= 0.011, f"calibration lifted an impossible cell to {out.p_cal}"
    score = Policy().score_cell(_cell(0.0, 99.8), calib, vol_bucket="LOW",
                                bankroll=Bankroll())
    assert score.verdict != "tap"
    assert score.stake == 0


def test_the_bottom_of_the_range_is_finely_bucketed():
    """One-in-a-million and one-in-a-hundred are not the same prediction."""
    from src.analytics.calibration import bucket_key
    assert bucket_key(1e-6, 5.0, "LOW") != bucket_key(0.009, 5.0, "LOW")
    assert bucket_key(0.0005, 5.0, "LOW") != bucket_key(0.005, 5.0, "LOW")


def test_a_genuine_correction_still_gets_through():
    """The cap must not neuter calibration where the model is merely wrong."""
    calib = Calibrator()
    for i in range(900):
        calib.observe(0.20, 10.0, "LOW", touched=i < 450)      # truly 50%
    out = calib.adjust(0.20, 10.0, "LOW")
    assert out.p_cal > 0.40, "a real 2.5x correction must survive"


def test_thin_buckets_stay_out_of_the_table():
    calib = Calibrator()
    calib.observe(0.5, 5.0, "LOW", touched=True)
    assert calib.stats()["reliability"] == []

"""Coil detection: is the tape winding up for a move? No network."""
from src.analytics.pressure import (
    COMPRESSED_AT,
    EXPANDING_AT,
    CoilTracker,
)
from src.analytics.signal import Tick


def _tape(now, *, span_s, step_s, amp, px=1000.0):
    """A zig-zag whose amplitude sets the realised volatility."""
    out = []
    n = int(span_s / step_s)
    for i in range(n):
        out.append(Tick("ETH", px + (amp if i % 2 else -amp),
                        now - span_s + i * step_s, source="page"))
    return out


def _compressed(now):
    """Busy for five minutes, then abruptly still."""
    loud = _tape(now - 25.0, span_s=300.0, step_s=0.5, amp=0.30)
    quiet = _tape(now, span_s=25.0, step_s=0.5, amp=0.01)
    return loud + quiet


def _steady(now):
    return _tape(now, span_s=320.0, step_s=0.5, amp=0.20)


# --- compression ----------------------------------------------------------
def test_a_tape_that_goes_still_reads_as_coiled():
    now = 10_000.0
    coil = CoilTracker().assess(_compressed(now), now=now)
    assert coil.confident
    assert coil.state in ("coiling", "loaded")
    assert coil.score > 0.35
    names = [f.name for f in coil.factors]
    assert "volatility compression" in names


def test_a_steady_tape_reads_as_quiet_not_coiled():
    now = 10_000.0
    coil = CoilTracker().assess(_steady(now), now=now)
    assert coil.state == "quiet"
    assert coil.score < 0.35
    assert coil.ready is False


def test_a_tape_that_lets_go_reads_as_expanding():
    now = 10_000.0
    calm = _tape(now - 25.0, span_s=300.0, step_s=0.5, amp=0.02)
    burst = _tape(now, span_s=25.0, step_s=0.5, amp=0.60)
    coil = CoilTracker().assess(calm + burst, now=now)
    assert coil.state == "expanding"
    assert "moving now" in coil.headline


def test_a_hot_but_not_expanding_tape_is_not_called_calm():
    """The headline must not contradict the factors printed beside it."""
    now = 10_000.0
    calm = _tape(now - 25.0, span_s=300.0, step_s=0.5, amp=0.10)
    hotter = _tape(now, span_s=25.0, step_s=0.5, amp=0.15)   # up, below the bar
    coil = CoilTracker().assess(calm + hotter, now=now)
    assert coil.state == "quiet"
    assert "no pressure building" not in coil.headline
    assert "above its baseline" in coil.headline


def test_too_little_tape_says_so_rather_than_guessing():
    now = 10_000.0
    coil = CoilTracker().assess(_tape(now, span_s=6.0, step_s=1.0, amp=0.1), now=now)
    assert coil.confident is False
    assert coil.state == "unknown"
    assert "not enough tape" in coil.headline


# --- dwell and edge pressure ---------------------------------------------
def test_an_unusually_long_dwell_adds_pressure():
    now = 10_000.0
    ticks = _compressed(now)
    plain = CoilTracker().assess(ticks, now=now)
    pinned = CoilTracker().assess(ticks, now=now, dwell_columns=20, typical_dwell=3.0)
    assert pinned.score > plain.score
    assert any(f.name == "dwell" for f in pinned.factors)


def test_a_normal_dwell_does_not_inflate_the_reading():
    now = 10_000.0
    c = CoilTracker().assess(_compressed(now), now=now, dwell_columns=3, typical_dwell=3.0)
    dwell = next(f for f in c.factors if f.name == "dwell")
    assert dwell.score == 0.0


def test_leaning_on_a_boundary_counts_but_resting_mid_row_does_not():
    now = 10_000.0
    ticks = _compressed(now)
    mid = CoilTracker().assess(ticks, now=now, position_in_row=0.5)
    edge = CoilTracker().assess(ticks, now=now, position_in_row=0.98)
    assert next(f for f in mid.factors if f.name == "edge pressure").score == 0.0
    assert next(f for f in edge.factors if f.name == "edge pressure").score > 0.9
    assert "ceiling" in next(f for f in edge.factors if f.name == "edge pressure").detail


# --- the house's own forecast --------------------------------------------
def test_the_house_marking_up_its_volatility_is_read_as_a_warning():
    now = 10_000.0
    tracker = CoilTracker()
    for i in range(60):                      # calm baseline
        tracker.note_house_volatility(0.02, now=now - 300 + i * 4.0)
    for i in range(8):                       # then it marks up hard
        tracker.note_house_volatility(0.06, now=now - 20 + i * 2.0)
    ratio, samples = tracker.house_ratio(now=now)
    assert ratio is not None and ratio > 1.2
    coil = tracker.assess(_compressed(now), now=now)
    house = next(f for f in coil.factors if f.name == "house marking up")
    assert house.score > 0.3
    assert "expects a move" in house.detail


def test_a_flat_house_estimate_adds_nothing():
    now = 10_000.0
    tracker = CoilTracker()
    # Samples must reach the recent window, or there is nothing to compare.
    for i in range(80):
        tracker.note_house_volatility(0.02, now=now - 300 + i * 3.8)
    house = next(f for f in tracker.assess(_compressed(now), now=now).factors
                 if f.name == "house marking up")
    assert house.score == 0.0


def test_a_house_estimate_with_no_recent_samples_is_not_used():
    now = 10_000.0
    tracker = CoilTracker()
    for i in range(60):                       # all older than the recent window
        tracker.note_house_volatility(0.02, now=now - 300 + i * 4.0)
    assert tracker.house_ratio(now=now)[0] is None


def test_nonsense_house_values_are_ignored():
    tracker = CoilTracker()
    for bad in (None, "x", -1, 0, float("nan"), float("inf")):
        tracker.note_house_volatility(bad)
    assert len(tracker.house) == 0
    assert tracker.house_ratio()[0] is None


# --- the combined verdict -------------------------------------------------
def test_ready_needs_both_a_wound_tape_and_a_second_opinion():
    """"Loaded" alone is a guess; loaded plus the house agreeing is a signal."""
    now = 10_000.0
    tracker = CoilTracker()
    for i in range(60):
        tracker.note_house_volatility(0.02, now=now - 300 + i * 4.0)
    quiet_house = tracker.assess(_compressed(now), now=now,
                                 dwell_columns=14, typical_dwell=3.0,
                                 position_in_row=0.97)
    for i in range(8):
        tracker.note_house_volatility(0.06, now=now - 20 + i * 2.0)
    loud_house = tracker.assess(_compressed(now), now=now,
                                dwell_columns=14, typical_dwell=3.0,
                                position_in_row=0.97)
    assert loud_house.score >= quiet_house.score
    assert loud_house.ready is True


def test_every_factor_explains_itself():
    now = 10_000.0
    coil = CoilTracker().assess(_compressed(now), now=now,
                                dwell_columns=9, typical_dwell=3.0,
                                position_in_row=0.9)
    body = coil.to_dict()
    assert 0.0 <= body["score"] <= 1.0
    assert body["state"] in ("expanding", "loaded", "coiling", "quiet", "unknown")
    for f in body["factors"]:
        assert f["detail"]
        assert 0.0 <= f["score"] <= 1.0
        assert f["weight"] > 0


def test_a_coil_never_claims_a_direction():
    """It says a move is coming, not which way.

    Direction belongs to the grid-walk break statistics; a coil that also
    guessed a side would be two claims dressed as one, and the weaker of the
    two would be invisible.
    """
    now = 10_000.0
    body = CoilTracker().assess(
        _compressed(now), now=now, dwell_columns=14, typical_dwell=3.0,
        position_in_row=0.97,
    ).to_dict()
    assert "p_up" not in body and "p_down" not in body
    assert "side" not in body and "bias" not in body
    for f in body["factors"]:
        assert "p_up" not in f and "side" not in f

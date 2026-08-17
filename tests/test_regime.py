"""The volatility regime must be independent of our own model. No network.

Conditioning calibration on a label derived from our own sigma selects our own
estimation error. Measured live, that made `p9|MED` predict 0.26 and deliver
0.066 -- a bucket miscalibrated by construction rather than by bad luck.
"""
import time

from src.analytics.regime import MIN_SAMPLES, UNKNOWN, VolRegime
from src.control.room import ControlRoom


def _fill(regime, values):
    for v in values:
        regime.observe(v)
    return regime


def _spread(n=MIN_SAMPLES * 2):
    """A wide, evenly spread history so terciles are well defined."""
    return [0.01 + (i / n) * 0.09 for i in range(n)]


# --- it refuses to label without evidence ---------------------------------
def test_no_label_until_there_is_enough_history():
    r = VolRegime()
    assert r.bucket() == UNKNOWN
    assert r.ready is False
    _fill(r, [0.02] * (MIN_SAMPLES - 1))
    assert r.ready is False
    assert r.bucket() == UNKNOWN


def test_a_wrong_label_is_worse_than_none():
    """An UNKNOWN regime must be reported, not guessed, so callers can fall
    back rather than silently splitting one honest bucket into two bad ones."""
    r = VolRegime()
    r.observe(0.05)
    assert r.stats()["bucket"] == UNKNOWN
    assert r.stats()["low_edge"] is None


def test_it_labels_once_the_history_is_deep_enough():
    r = _fill(VolRegime(), _spread())
    assert r.ready is True
    assert r.bucket(0.011) == "LOW"
    assert r.bucket(0.05) == "MED"
    assert r.bucket(0.095) == "HIGH"


# --- terciles, not fixed numbers -----------------------------------------
def test_thresholds_follow_the_houses_own_distribution():
    quiet = _fill(VolRegime(), [0.001 + (i / 240) * 0.004 for i in range(240)])
    busy = _fill(VolRegime(), [0.10 + (i / 240) * 0.40 for i in range(240)])
    q_lo, q_hi = quiet.thresholds()
    b_lo, b_hi = busy.thresholds()
    assert q_hi < b_lo, "each regime is scaled to its own history"
    # The same absolute number means different things in different markets.
    assert quiet.bucket(0.004) == "HIGH"
    assert busy.bucket(0.004) == "LOW"


def test_the_buckets_split_roughly_into_thirds():
    r = _fill(VolRegime(), _spread(300))
    counts = {"LOW": 0, "MED": 0, "HIGH": 0}
    for v in list(r.samples):
        counts[r.bucket(v)] += 1
    for name, n in counts.items():
        assert 70 < n < 130, f"{name} took {n} of 300"


# --- robustness -----------------------------------------------------------
def test_nonsense_readings_are_ignored():
    r = VolRegime()
    for bad in (None, "x", 0, -1, float("nan"), float("inf")):
        r.observe(bad)
    assert len(r.samples) == 0
    assert r.bucket() == UNKNOWN


def test_the_label_defaults_to_the_latest_reading():
    r = _fill(VolRegime(), _spread())
    r.observe(0.098)
    assert r.bucket() == "HIGH"
    r.observe(0.011)
    assert r.bucket() == "LOW"


# --- through the room -----------------------------------------------------
def _room(tmp_path, **kw):
    kw.setdefault("enable_oracle", False)
    kw.setdefault("enable_wallet", False)
    kw.setdefault("dry_run", True)
    for name, stem in (("session_path", "s"), ("token_path", "t"), ("calibration_path", "c"),
                       ("bankroll_path", "b"), ("traversal_path", "tr"), ("pnl_path", "p"),
                       ("player_path", "pl"), ("player_calibration_path", "pc")):
        kw.setdefault(name, tmp_path / f"{stem}.json")
    return ControlRoom(**kw)


def test_the_room_learns_the_regime_from_the_quote_frames(tmp_path):
    room = _room(tmp_path)
    for i in range(MIN_SAMPLES + 40):
        room.set_grid({"now_ms": 1000.0 + i, "square_duration": 5000,
                       "dollars_per_line": 0.5,
                       "server_volatility": 0.01 + (i % 60) * 0.001})
    view = room.regime_view()
    assert view["ready"] is True
    assert view["source"] == "house"
    assert view["bucket"] in ("LOW", "MED", "HIGH")
    assert view["low_edge"] < view["high_edge"]
    room.close()


def test_the_room_reports_unknown_before_it_has_evidence(tmp_path):
    room = _room(tmp_path)
    room.set_grid({"now_ms": 1000.0, "server_volatility": 0.02})
    assert room.regime_view()["bucket"] == UNKNOWN
    assert room.regime_view()["ready"] is False
    room.close()


def test_the_signal_uses_the_house_label_when_one_exists(tmp_path):
    from src.analytics.signal import Tick, compute_signal
    from src.analytics.calibration import Calibrator
    from src.analytics.policy import Bankroll, Policy

    now = 1_700_000_000.0
    ticks = [Tick("ETH", 1000.0 + (0.2 if i % 2 else -0.2), now - (90 - i) * 0.4,
                  source="page") for i in range(90)]
    sig = compute_signal(ticks, now=now, cell_height=0.5,
                         calibrator=Calibrator(), policy=Policy(), bankroll=Bankroll(),
                         vol_bucket_override="HIGH")
    assert sig.vol_bucket == "HIGH"
    # Without an override it falls back to the sigma-derived label.
    plain = compute_signal(ticks, now=now, cell_height=0.5,
                           calibrator=Calibrator(), policy=Policy(), bankroll=Bankroll())
    assert plain.vol_bucket in ("LOW", "MED", "HIGH")

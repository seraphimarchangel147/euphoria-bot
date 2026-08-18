"""RollingVolRegime: local terciles, house source, no wall-clock reads."""

from __future__ import annotations

import time

from statistics import quantiles

from src.analytics.rolling_regime import (
    HIGH,
    KEEP,
    LOW,
    MED,
    MIN_SAMPLES,
    SOURCE,
    UNKNOWN,
    WINDOW_S,
    RollingVolRegime,
)


def _whole_history_occupancy(values: list[float], recent: list[float]) -> dict[str, float]:
    """Live VolRegime failure mode: cuts from all history, labels on recent."""
    q1, q2 = quantiles(values, n=3)
    n = len(recent)
    counts = {LOW: 0, MED: 0, HIGH: 0}
    for value in recent:
        if value <= q1:
            counts[LOW] += 1
        elif value <= q2:
            counts[MED] += 1
        else:
            counts[HIGH] += 1
    return {name: counts[name] / n for name in (LOW, MED, HIGH)}


def test_a_rising_house_vol_trend_does_not_saturate_on_high():
    """Whole-history terciles labelled HIGH whenever house vol trended.

    Live VolRegime takes terciles over all retained history (KEEP=4000).
    A rising house-vol series sits above the historical 2/3 quantile, so
    the label saturates on HIGH and calibration dumps everything there.
    A rolling window must keep LOW/MED/HIGH occupancy near thirds.
    """
    regime = RollingVolRegime()
    assert regime.window_s == WINDOW_S == 20 * 60

    # 1 Hz over three windows: long enough that whole-history cuts on
    # the most recent window saturate HIGH (q2 sits at or below the
    # window floor), and still under KEEP so the cap does not silently
    # shrink the lookback.
    dt = 1.0
    n_window = int(WINDOW_S / dt)
    n_total = n_window * 3
    assert n_total < KEEP

    t0 = 1_700_000_000.0
    values: list[float] = []
    for i in range(n_total):
        value = float(i + 1)
        values.append(value)
        regime.observe(value, now=t0 + i * dt)

    now = t0 + (n_total - 1) * dt
    cutoff = now - WINDOW_S
    expected_n = sum(1 for i in range(n_total) if t0 + i * dt >= cutoff)
    stats = regime.stats(now=now)
    assert stats["window_s"] == WINDOW_S
    assert stats["window_n"] == expected_n
    assert expected_n in (n_window, n_window + 1)
    assert stats["source"] == "house"

    for name in (LOW, MED, HIGH):
        frac = stats[name]
        assert 0.20 <= frac <= 0.45, f"{name} occupancy {frac} not near 1/3: {stats}"

    # Counterfactual: same series, live whole-history cuts. If this
    # is not HIGH≈1.0 the rolling test is not distinguishing the bug.
    recent = values[-expected_n:]
    hist = _whole_history_occupancy(values, recent)
    assert hist[HIGH] >= 0.90, (
        f"whole-history occupancy did not saturate HIGH ({hist}); "
        "the rolling thirds would be cheap good news"
    )
    assert hist[LOW] < 0.05 and hist[MED] < 0.10

    # Sequential latest-print labels on a monotone rise stay HIGH:
    # the newest print is always the window max. That is not the bug
    # this sibling fixes. Pin it so a later change cannot pretend
    # otherwise.
    latest = regime.bucket(values[-1], now=now)
    assert latest == HIGH


def test_unknown_until_the_rolling_window_is_full():
    regime = RollingVolRegime()
    t0 = 0.0
    for i in range(MIN_SAMPLES - 1):
        regime.observe(float(i + 1), now=t0 + i)
        assert regime.bucket(float(i + 1), now=t0 + i) == UNKNOWN
        assert regime.thresholds(now=t0 + i) is None
        stats = regime.stats(now=t0 + i)
        assert stats["window_n"] == i + 1
        assert stats[LOW] == stats[MED] == stats[HIGH] == 0.0

    regime.observe(float(MIN_SAMPLES), now=t0 + MIN_SAMPLES - 1)
    assert regime.thresholds(now=t0 + MIN_SAMPLES - 1) is not None
    assert regime.bucket(float(MIN_SAMPLES), now=t0 + MIN_SAMPLES - 1) != UNKNOWN

    # Window-full is about the rolling window, not lifetime count.
    # Jump past WINDOW_S so only the last print remains in-window.
    later = t0 + MIN_SAMPLES - 1 + WINDOW_S + 1
    regime.observe(999.0, now=later)
    assert regime.stats(now=later)["window_n"] == 1
    assert regime.bucket(999.0, now=later) == UNKNOWN


def test_house_source_is_unchanged():
    regime = RollingVolRegime()
    assert regime.source == SOURCE == "house"
    assert RollingVolRegime.SOURCE == "house"
    regime.observe(0.42, now=0.0)
    stats = regime.stats(now=0.0)
    assert stats["source"] == "house"
    assert "sigma" not in stats


def test_observe_uses_passed_now_rather_than_wall_clock(monkeypatch):
    def boom() -> float:
        raise AssertionError("wall clock read")

    monkeypatch.setattr(time, "time", boom)

    regime = RollingVolRegime()
    # Stamps far in the past relative to any real wall clock. If
    # observe had called time.time(), this test would already have
    # raised. If it had stamped with a live clock, the samples would
    # still be inside WINDOW_S of a query at `later` below.
    regime.observe(1.0, now=0.0)
    regime.observe(2.0, now=10.0)

    assert regime.stats(now=10.0)["window_n"] == 2
    assert regime.stats(now=-1.0)["window_n"] == 0
    later = 10.0 + WINDOW_S + 1
    assert regime.stats(now=later)["window_n"] == 0
    assert regime.bucket(1.5, now=10.0) == UNKNOWN
    assert time.time is boom


def test_a_nan_inf_or_nonpositive_print_cannot_enter_the_window():
    """Live VolRegime drops None / <=0 / NaN / inf. The sibling did not.

    A NaN in the window poisons statistics.quantiles and would make
    every later label undefined. Looser ingest than live is how a
    dead or garbage house-vol field silently wrecks the cuts. Same
    reject list as regime.py: None, non-float, 0, negative, NaN, ±inf.
    """
    regime = RollingVolRegime()
    for i, bad in enumerate((None, "x", 0, -1, 0.0, float("nan"), float("inf"), float("-inf"))):
        regime.observe(bad, now=float(i))
    assert regime.stats(now=7.0)["window_n"] == 0
    assert list(regime._samples) == []
    assert regime.thresholds(now=7.0) is None
    assert regime.bucket(0.05, now=7.0) == UNKNOWN

    regime.observe(0.05, now=8.0)
    assert regime.stats(now=8.0)["window_n"] == 1
    assert list(regime._samples) == [(8.0, 0.05)]

    # A later garbage print must not displace or sit beside the good one.
    regime.observe(float("nan"), now=9.0)
    regime.observe(float("inf"), now=10.0)
    regime.observe(0, now=11.0)
    assert regime.stats(now=11.0)["window_n"] == 1
    assert list(regime._samples) == [(8.0, 0.05)]

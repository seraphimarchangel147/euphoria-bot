import asyncio

from src.monitor import price_monitor
from src.monitor.oracle import Quote, StalePriceError
import src.monitor.oracle as oracle
import time

import pytest


def setup_function():
    price_monitor._last.clear()


def test_first_observation_never_alerts():
    assert price_monitor.check_alert("ETH", 3000) is None


def test_alerts_on_move_above_threshold():
    price_monitor.check_alert("ETH", 3000)
    alert = price_monitor.check_alert("ETH", 3060, threshold=0.01)
    assert alert and "UP" in alert and "+2.00%" in alert


def test_silent_below_threshold():
    price_monitor.check_alert("ETH", 3000)
    assert price_monitor.check_alert("ETH", 3010, threshold=0.01) is None


def test_downward_move_alerts():
    price_monitor.check_alert("BTC", 100000)
    alert = price_monitor.check_alert("BTC", 95000, threshold=0.01)
    assert alert and "DOWN" in alert


def test_reference_resets_only_after_alert():
    price_monitor.check_alert("ETH", 3000)
    price_monitor.check_alert("ETH", 3010, threshold=0.01)   # no alert
    # cumulative move from 3000 still measured against 3000
    assert price_monitor.check_alert("ETH", 3035, threshold=0.01) is not None


def test_quote_age_and_staleness_guard(monkeypatch):
    fresh = Quote("ETH", 3000.0, time.time())
    assert fresh.age < 5

    class FakeResp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return [{"value": 3000, "timestamp": (time.time() - 9999) * 1000}]

    class FakeClient:
        def get(self, *a, **k): return FakeResp()
        def close(self): pass

    with pytest.raises(StalePriceError):
        oracle.fetch_quote("ETH", FakeClient())


def test_monitor_loop_runs_bounded(monkeypatch):
    monkeypatch.setattr(oracle, "fetch_quotes", lambda syms: {"ETH": Quote("ETH", 3000.0, time.time())})
    monkeypatch.setattr(price_monitor.oracle, "fetch_quotes", lambda syms: {"ETH": Quote("ETH", 3000.0, time.time())})
    asyncio.run(price_monitor.monitor_prices(iterations=1))

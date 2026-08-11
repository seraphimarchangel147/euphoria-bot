import pytest

from src.trader.risk import RiskEngine, RiskRejection


def engine(**kw):
    return RiskEngine(max_trade=10, max_daily_loss=50, max_open=3, **kw)


def test_allows_trade_within_limits():
    engine().check(5)


def test_rejects_oversized_trade():
    with pytest.raises(RiskRejection, match="exceeds MAX_TRADE_USDM"):
        engine().check(11)


def test_rejects_non_positive():
    with pytest.raises(RiskRejection):
        engine().check(0)


def test_rejects_over_balance():
    with pytest.raises(RiskRejection, match="exceeds balance"):
        engine().check(5, balance=2)


def test_rejects_too_many_open_trades():
    e = engine()
    for _ in range(3):
        e.record_open()
    with pytest.raises(RiskRejection, match="MAX_OPEN_TRADES"):
        e.check(1)


def test_daily_loss_circuit_breaker():
    e = engine()
    e.record_open(); e.record_settled(-50)
    with pytest.raises(RiskRejection, match="circuit breaker"):
        e.check(1)


def test_profit_does_not_trip_breaker():
    e = engine()
    e.record_open(); e.record_settled(120)
    e.check(1)


def test_settle_decrements_open_trades_without_going_negative():
    e = engine()
    e.record_settled(0)
    assert e.state.open_trades == 0

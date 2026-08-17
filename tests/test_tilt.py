"""Tilt guard: pace against drawdown. No network."""
from src.analytics.tilt import TiltGuard


def _session(guard, *, start=100.0, now=1000.0, trades=44_000):
    guard.begin_session(now=now)
    guard.note_balance(start, now=now)
    guard.note_trade_count(trades, now=now)
    return now


# --- calm -----------------------------------------------------------------
def test_a_steady_account_is_not_flagged():
    g = TiltGuard()
    t0 = _session(g)
    for i in range(1, 11):
        g.note_balance(100.0 + i * 0.1, now=t0 + i * 60.0)
        g.note_trade_count(44_000 + i * 2, now=t0 + i * 60.0)
    out = g.assess(now=t0 + 600.0)
    assert out["flagged"] is False
    assert out["severity"] == "calm"
    assert out["reasons"] == []


def test_nothing_seen_yet_says_so():
    out = TiltGuard().assess(now=1000.0)
    assert out["flagged"] is False
    assert out["balance"] is None
    assert "no balance" in out["headline"]


# --- the pattern ----------------------------------------------------------
def test_fast_trading_into_a_drawdown_is_flagged():
    """Falling hard while trading hard — the shape being watched for."""
    g = TiltGuard()
    t0 = _session(g, start=100.0)
    for i in range(1, 11):
        g.note_balance(100.0 - i * 8.0, now=t0 + i * 60.0)     # 100 -> 20
        g.note_trade_count(44_000 + i * 90, now=t0 + i * 60.0)  # 90/min
    out = g.assess(now=t0 + 600.0)
    assert out["flagged"] is True
    assert out["severity"] == "tilted"
    assert out["drawdown_pct"] >= 0.30
    assert out["trade_rate_per_min"] > 8
    assert any("high" in r for r in out["reasons"])
    assert any("trades a minute" in r for r in out["reasons"])


def test_a_drawdown_alone_is_only_elevated():
    """Losing slowly is not the same as losing fast, and must not read alike."""
    g = TiltGuard()
    t0 = _session(g, start=100.0)
    for i in range(1, 11):
        g.note_balance(100.0 - i * 2.0, now=t0 + i * 300.0)     # slow, over an hour
        g.note_trade_count(44_000 + i, now=t0 + i * 300.0)
    out = g.assess(now=t0 + 3000.0)
    assert out["severity"] in ("calm", "elevated")
    assert out["severity"] != "tilted"


def test_fast_trading_while_winning_is_not_tilt():
    g = TiltGuard()
    t0 = _session(g, start=100.0)
    for i in range(1, 11):
        g.note_balance(100.0 + i * 5.0, now=t0 + i * 60.0)
        g.note_trade_count(44_000 + i * 90, now=t0 + i * 60.0)
    out = g.assess(now=t0 + 600.0)
    assert out["drawdown_pct"] == 0.0
    assert out["severity"] != "tilted"


def test_drawdown_is_measured_from_the_session_peak_not_the_start():
    """The number being chased is the high, not the opening balance."""
    g = TiltGuard()
    t0 = _session(g, start=100.0)
    g.note_balance(200.0, now=t0 + 60.0)      # ran it up
    g.note_balance(150.0, now=t0 + 120.0)     # gave half back
    out = g.assess(now=t0 + 130.0)
    assert out["peak"] == 200.0
    assert out["drawdown"] == 50.0
    assert out["drawdown_pct"] == 0.25
    assert out["session_change"] == 50.0      # still up on the session


# --- measurement ----------------------------------------------------------
def test_trade_rate_comes_from_the_house_counter():
    g = TiltGuard()
    t0 = _session(g, trades=44_000)
    g.note_trade_count(44_300, now=t0 + 300.0)      # 300 trades in 5 minutes
    traded, rate = g.trades_in_window(now=t0 + 300.0)
    assert traded == 300
    assert 59 < rate < 61


def test_a_counter_going_backwards_is_ignored():
    g = TiltGuard()
    t0 = _session(g, trades=44_000)
    g.note_trade_count(10, now=t0 + 60.0)
    assert g.counts[-1][1] == 44_000


def test_bleed_rate_is_per_minute():
    g = TiltGuard()
    t0 = _session(g, start=100.0)
    g.note_balance(70.0, now=t0 + 600.0)            # -30 over ten minutes
    change, per_min = g.bleed(now=t0 + 600.0)
    assert change == -30.0
    assert abs(per_min + 3.0) < 1e-9


def test_max_stake_share_warns_when_the_account_is_small():
    g = TiltGuard()
    t0 = _session(g, start=17.67)
    out = g.assess(now=t0 + 10.0)
    # A single 10 USDM cap is over half of what is left.
    assert out["max_stake_share"] > 0.5


def test_nonsense_inputs_are_ignored():
    g = TiltGuard()
    for bad in (None, "x", -5, float("nan")):
        g.note_balance(bad)
        g.note_trade_count(bad)
    assert g.assess()["balance"] is None

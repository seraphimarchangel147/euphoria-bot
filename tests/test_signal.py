"""Canned-tick tests for the nearby-square think signal. No network."""
from src.analytics.signal import Tick, TickBuffer, compute_signal


def _ramp(symbol: str, start: float, end: float, n: int, t0: float, span: float = 5.0) -> list[Tick]:
    ticks = []
    for i in range(n):
        frac = i / (n - 1)
        ticks.append(Tick(symbol, start + (end - start) * frac, t0 + span * frac, source="test"))
    return ticks


def test_trending_tape_names_nearest_square_above():
    t0 = 1_700_000_000.0
    # 3000 -> 3003.6 is +0.12% over 5s — enough to touch the nearest band.
    ticks = _ramp("ETH", 3000.0, 3003.6, 8, t0)
    sig = compute_signal(ticks, now=t0 + 5.0)
    assert sig.bias == "up"
    assert sig.confidence >= 0.4
    assert sig.suggested != "no trade"
    assert sig.suggested.side == "up"
    assert sig.suggested.distance == 1
    assert sig.suggested.cell == "nearest-up"
    assert sig.suggested.size == 0.10
    assert "nearest square above" in sig.reason
    assert "touch once" in sig.reason
    assert sig.hint == "nearest square above, ~5s, touch once"


def test_down_trend_names_nearest_square_below():
    t0 = 1_700_000_000.0
    ticks = _ramp("ETH", 3000.0, 2996.4, 8, t0)  # -0.12%
    sig = compute_signal(ticks, now=t0 + 5.0)
    assert sig.bias == "down"
    assert sig.suggested.cell == "nearest-down"
    assert sig.suggested.distance == 1
    assert "nearest square below" in sig.reason
    assert "touch once" in sig.reason


def test_choppy_tape_is_no_trade():
    t0 = 1_700_000_000.0
    prices = [3000.0, 3004.0, 2997.0, 3005.0, 2998.0, 3003.0, 2999.0]
    ticks = [Tick("ETH", p, t0 + i * 0.7, source="test") for i, p in enumerate(prices)]
    sig = compute_signal(ticks, now=t0 + 5.0)
    assert sig.suggested == "no trade"
    assert sig.hint == "no trade"
    assert "choppy" in sig.reason


def test_quiet_tape_does_not_recommend_far_cells():
    t0 = 1_700_000_000.0
    ticks = _ramp("ETH", 3000.0, 3000.3, 6, t0)  # +0.01%
    sig = compute_signal(ticks, now=t0 + 5.0)
    assert sig.bias == "flat"
    assert sig.suggested == "no trade"
    assert "no trade" in sig.reason
    assert "far" not in (sig.hint or "")
    assert "lottery" not in sig.reason


def test_strong_print_still_stays_nearby():
    t0 = 1_700_000_000.0
    ticks = _ramp("ETH", 3000.0, 3060.0, 8, t0)  # +2% would be many cells away
    sig = compute_signal(ticks, now=t0 + 5.0)
    assert sig.suggested != "no trade"
    assert sig.suggested.distance <= 2
    assert sig.suggested.cell in ("nearest-up", "next-up")
    assert "nearest" in sig.suggested.cell or sig.suggested.distance <= 2


def test_not_enough_ticks_waits():
    now = 1_700_000_000.0
    sig = compute_signal([Tick("ETH", 3000.0, now)], now=now)
    assert sig.bias == "flat"
    assert sig.confidence == 0.0
    assert sig.suggested == "no trade"
    assert "waiting" in sig.reason


def test_btc_agreement_raises_confidence():
    t0 = 1_700_000_000.0
    eth = _ramp("ETH", 3000.0, 3003.6, 8, t0)
    btc = _ramp("BTC", 100000.0, 100150.0, 8, t0)
    plain = compute_signal(eth, now=t0 + 5.0)
    confirmed = compute_signal(eth + btc, now=t0 + 5.0)
    assert confirmed.confidence >= plain.confidence
    assert "BTC agreeing" in confirmed.reason


def test_maps_cell_coords_when_page_grid_is_known():
    t0 = 1_700_000_000.0
    ticks = _ramp("ETH", 3000.0, 3003.6, 8, t0)
    sig = compute_signal(
        ticks,
        now=t0 + 5.0,
        grid={"cell_x": 3, "cell_y": 7, "cell_height": 1.5},
    )
    assert sig.suggested.cell_x == 4
    assert sig.suggested.cell_y == 8
    assert sig.suggested.cell_height == 1.5


def test_old_ticks_outside_window_ignored():
    now = 1_700_000_000.0
    old = _ramp("ETH", 3000.0, 3100.0, 8, now - 60.0)
    sig = compute_signal(old, now=now)
    assert "waiting" in sig.reason


def test_tick_buffer_extend_and_latest():
    buf = TickBuffer()
    n = buf.extend([
        {"symbol": "eth", "price": 3000, "ts": 10},
        {"asset": "BTC", "value": 100000, "timestamp": 11_000},
        {"nope": True},
    ])
    assert n == 2
    assert buf.latest("ETH").price == 3000
    quotes = buf.latest_quotes()
    assert quotes["ETH"]["price"] == 3000
    assert quotes["BTC"]["price"] == 100000


def test_signal_accepts_tick_buffer():
    t0 = 1_700_000_000.0
    buf = TickBuffer()
    for t in _ramp("ETH", 3000.0, 3003.6, 8, t0):
        buf.push(t.symbol, t.price, t.ts, source="test")
    sig = compute_signal(buf, now=t0 + 5.0)
    assert sig.bias == "up"
    assert sig.suggested.cell == "nearest-up"


def test_to_dict_uses_no_trade_string():
    sig = compute_signal([], now=1.0)
    d = sig.to_dict()
    assert d["suggested"] == "no trade"
    assert d["hint"] == "no trade"
    assert d["bias"] == "flat"

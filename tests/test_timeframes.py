"""Timeframe aggregation, trend classification, and public OHLC parsing. No network."""
from src.analytics.ohlc import fetch_klines, fetch_ohlc_stack, parse_klines
from src.analytics.signal import Tick
from src.analytics.timeframes import (
    TF_KEYS,
    Bar,
    alignment,
    build_stack,
    classify_bars,
    ticks_to_bars,
)


def _ticks(prices, t0=1_700_000_000.0, step=1.0, symbol="ETH"):
    return [Tick(symbol, p, t0 + i * step, source="test") for i, p in enumerate(prices)]


def _trend_bars(direction: str, n: int = 8, start: float = 3000.0) -> list[Bar]:
    bars = []
    px = start
    for i in range(n):
        if direction == "up":
            close = px * 1.012
        elif direction == "down":
            close = px * 0.988
        else:
            close = px
        bars.append(Bar(ts=1000.0 + i * 60, open=px, high=max(px, close), low=min(px, close), close=close))
        px = close
    return bars


def _stack(direction: str) -> dict[str, list[Bar]]:
    return {key: _trend_bars(direction) for key in TF_KEYS}


def test_ticks_bucket_into_ohlc_bars():
    t0 = 1_700_000_000.0
    # two 60s buckets: 10 and 70 seconds after a minute boundary
    base = t0 - (t0 % 60)
    ticks = [
        Tick("ETH", 100.0, base + 10),
        Tick("ETH", 110.0, base + 20),
        Tick("ETH", 90.0, base + 40),
        Tick("ETH", 95.0, base + 70),
        Tick("ETH", 97.0, base + 80),
    ]
    bars = ticks_to_bars(ticks, 60)
    assert len(bars) == 2
    assert bars[0].open == 100.0
    assert bars[0].high == 110.0
    assert bars[0].low == 90.0
    assert bars[0].close == 90.0
    assert bars[1].open == 95.0
    assert bars[1].close == 97.0


def test_classify_bars_up_down_flat_unknown():
    up = classify_bars(_trend_bars("up"), key="1h", source="ohlc")
    down = classify_bars(_trend_bars("down"), key="1h", source="ohlc")
    flat = classify_bars(_trend_bars("flat"), key="1h", source="ohlc")
    empty = classify_bars([], key="1h", source="ohlc")
    assert up.lean == "up" and up.change_pct > 0 and up.source == "ohlc"
    assert down.lean == "down" and down.change_pct < 0
    assert flat.lean == "flat"
    assert empty.lean == "unknown" and empty.source == "unknown"


def test_build_stack_prefers_ohlc_and_exposes_every_tf():
    ticks = _ticks([3000.0, 3000.2, 3000.4], step=1.0)
    stack = build_stack(ticks, symbol="ETH", now=1_700_000_002.0, ohlc=_stack("down"))
    assert stack.lean == "down"
    assert set(stack.frames_dict()) == set(TF_KEYS)
    for key in TF_KEYS:
        frame = stack.frame(key)
        assert frame is not None
        assert frame.lean == "down"
        assert frame.source == "ohlc"
    assert "1m↓" in stack.summary()


def test_build_stack_from_ticks_when_ohlc_missing():
    t0 = 1_700_000_000.0
    # ~12 minutes of rising ETH — enough for 1m / 5m, not for 1h+.
    prices = [3000.0 + i * 0.8 for i in range(12)]
    ticks = _ticks(prices, t0=t0, step=60.0)
    stack = build_stack(ticks, symbol="ETH", now=t0 + 11 * 60)
    assert stack.frame("1m").lean == "up"
    assert stack.frame("1m").source == "ticks"
    assert stack.frame("1h").lean == "unknown"
    assert stack.frame("D").lean == "unknown"
    assert stack.frame("M").source == "unknown"


def test_alignment_with_trend_fading_mixed():
    assert alignment("up", "up") == "with-trend"
    assert alignment("down", "down") == "with-trend"
    assert alignment("up", "down") == "fading"
    assert alignment("down", "up") == "fading"
    assert alignment("up", "mixed") == "mixed"
    assert alignment("up", "unknown") == "mixed"
    assert alignment("flat", "up") == "mixed"


def test_parse_klines_skips_garbage():
    raw = [
        [1_700_000_000_000, "3000", "3010", "2990", "3005", "1"],
        "nope",
        [1, "0", "1", "1", "1"],
        [2, "bad", 1, 1, 1],
        [1_700_000_060_000, "3005.0", "3020", "3000", "3012", "2"],
    ]
    bars = parse_klines(raw)
    assert len(bars) == 2
    assert bars[0].open == 3000.0
    assert bars[1].close == 3012.0


def test_fetch_ohlc_stack_uses_injected_fetch_and_never_needs_network():
    seen = []

    def fake_fetch(url: str, timeout: float):
        seen.append(url)
        if "ETHUSDT" not in url:
            return []
        interval = "1m"
        for token in ("1m", "5m", "1h", "4h", "1d", "1M"):
            if f"interval={token}" in url:
                interval = token
                break
        px = 3000.0 + (hash(interval) % 7)
        return [[1_700_000_000_000, str(px), str(px + 1), str(px - 1), str(px + 0.5), "1"]]

    stack = fetch_ohlc_stack("ETH", fetch_fn=fake_fetch)
    assert set(stack) == set(TF_KEYS)
    assert all(stack[key] for key in TF_KEYS)
    assert len(seen) == len(TF_KEYS)
    assert all("api.binance.com" in url and "ETHUSDT" in url for url in seen)
    assert fetch_klines("SOL", "1m", fetch_fn=fake_fetch) == []

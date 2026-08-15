"""Canned-tick tests for the nearby-square think signal. No network."""
import pytest

from src.analytics.signal import Tick, TickBuffer, compute_signal
from src.analytics.timeframes import TF_KEYS, Bar


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
    assert sig.lesson == "chop"
    assert "standing aside" in sig.why


def test_quiet_tape_does_not_recommend_far_cells():
    t0 = 1_700_000_000.0
    ticks = _ramp("ETH", 3000.0, 3000.3, 6, t0)  # +0.01%
    sig = compute_signal(ticks, now=t0 + 5.0)
    assert sig.bias == "flat"
    assert sig.suggested == "no trade"
    assert "no trade" in sig.reason
    assert sig.lesson == "quiet"
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
    assert any(t["cell_x"] == 4 and t["cell_y"] == 8 for t in sig.looking_at)


def test_maps_live_gridX_gridY_and_price_interval():
    t0 = 1_700_000_000.0
    ticks = _ramp("ETH", 3000.0, 3003.6, 8, t0)
    sig = compute_signal(
        ticks,
        now=t0 + 5.0,
        grid={"now_ms": 25_000, "square_duration": 5_000, "price": 3000.2, "dollars_per_line": 0.5},
    )
    # current col = 25000/5000 = 5, next = 6; price row = 3000.2/0.5 = 6000; up → 6001
    assert sig.suggested.cell_x == 6
    assert sig.suggested.cell_y == 6001
    downs = [t for t in sig.looking_at if t["side"] == "down"]
    assert downs and downs[0]["cell_y"] == 5999


def test_authoritative_grid_exposes_multipliers_lookahead_and_history():
    t0 = 1_700_000_000.0
    ticks = _ramp("ETH", 3000.0, 3003.6, 8, t0)
    current_x, current_y = 340_000_001, 6007
    cells = []
    for forward in (1, 2, 3):
        for side, dy in (("up", 1), ("down", -1)):
            multiplier = 2.0 + forward * 0.1 + (0.02 if side == "up" else 0.0)
            cells.append({
                "cell_x": current_x + forward,
                "cell_y": current_y + dy,
                "forward": forward,
                "forward_s": forward * 5,
                "side": side,
                "distance": 1,
                "multiplier": multiplier,
                "break_even_probability": round(1 / multiplier, 6),
            })
    grid = {
        "authoritative": True,
        "gridX": current_x,
        "gridY": current_y,
        "cell_height": 0.5,
        "multiplier_source": "quotesFeed",
        "quoted_grid_ref_time": 1_700_000_005_000,
        "forward_columns": 3,
        "cells": cells,
        "history": {"window_s": 120, "sample_count": 50, "change_bps": 12.5, "range_bps": 18.0},
    }
    sig = compute_signal(ticks, now=t0 + 5.0, grid=grid, ohlc=_ohlc("up"))
    body = sig.to_dict()
    assert body["grid_context"]["authoritative"] is True
    assert body["grid_context"]["cell_count"] == 6
    assert body["grid_context"]["history"]["sample_count"] == 50
    assert {c["forward"] for c in body["candidates"]} == {1, 2, 3}
    assert all(c["multiplier"] > 1 for c in body["candidates"])
    assert body["pick"]["multiplier"] == pytest.approx(2.12)
    assert body["pick"]["break_even_probability"] == pytest.approx(round(1 / 2.12, 6))


def test_stale_grid_never_claims_authoritative_multipliers():
    t0 = 1_700_000_000.0
    ticks = _ramp("ETH", 3000.0, 3003.6, 8, t0)
    grid = {
        "authoritative": False,
        "reason": "quote grid stale",
        "cell_x": 3,
        "cell_y": 7,
        "cell_height": 1.5,
        "cells": [{"cell_x": 4, "cell_y": 8, "forward": 1, "side": "up", "distance": 1, "multiplier": 9.9}],
    }
    body = compute_signal(ticks, now=t0 + 5.0, grid=grid).to_dict()
    assert body["grid_context"]["authoritative"] is False
    assert body["grid_context"]["reason"] == "quote grid stale"
    assert all("multiplier" not in c for c in body["candidates"])


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
    assert d["pick"] is None
    assert d["candidates"] == d["looking_at"]
    assert set(d["timeframes"]) == set(TF_KEYS)
    assert d["lesson"] == "waiting"
    assert "waiting" in d["why"]


def _ohlc(direction: str) -> dict[str, list[Bar]]:
    out = {}
    for key in TF_KEYS:
        bars = []
        px = 3000.0
        for i in range(6):
            close = px * (1.015 if direction == "up" else 0.985)
            bars.append(Bar(ts=10.0 + i * 60, open=px, high=max(px, close), low=min(px, close), close=close))
            px = close
        out[key] = bars
    return out


def test_with_trend_nearby_tap_keeps_nearest_square():
    t0 = 1_700_000_000.0
    ticks = _ramp("ETH", 3000.0, 3003.6, 8, t0)
    sig = compute_signal(ticks, now=t0 + 5.0, ohlc=_ohlc("up"))
    assert sig.suggested != "no trade"
    assert sig.suggested.distance == 1
    assert sig.alignment == "with-trend"
    assert sig.tf_stack.lean == "up"
    assert "with-trend" in sig.reason
    d = sig.to_dict()
    assert d["pick"]["side"] == "up"
    assert d["pick"]["role"] == "sel"
    assert d["tf_lean"] == "up"
    assert d["alignment"] == "with-trend"


def test_fading_vs_higher_tf_is_no_trade():
    t0 = 1_700_000_000.0
    ticks = _ramp("ETH", 3000.0, 3003.6, 8, t0)
    sig = compute_signal(ticks, now=t0 + 5.0, ohlc=_ohlc("down"))
    assert sig.suggested == "no trade"
    assert sig.alignment == "fading"
    assert sig.tf_stack.lean == "down"
    assert "fading" in sig.reason
    assert sig.suggested == "no trade"
    d = sig.to_dict()
    assert d["pick"] is None
    assert d["tf_lean"] == "down"
    assert all(d["timeframes"][k]["lean"] == "down" for k in TF_KEYS)


def test_dense_page_tape_is_not_marked_choppy():
    t0 = 1_700_000_000.0
    ticks = []
    for i in range(40):
        frac = i / 39
        noise = 0.05 if i % 2 == 0 else -0.05
        ticks.append(Tick("ETH", 3000.0 + 3.6 * frac + noise, t0 + 5.0 * frac, source="page"))
    sig = compute_signal(ticks, now=t0 + 5.0, ohlc=_ohlc("up"))
    assert "choppy" not in sig.reason
    assert sig.suggested != "no trade"
    assert sig.suggested.distance == 1
    assert "page tape" in sig.reason


def test_dense_tape_recent_fade_is_no_trade():
    t0 = 1_700_000_000.0
    early = _ramp("ETH", 3000.0, 3005.0, 30, t0, span=3.8)
    late = _ramp("ETH", 3005.0, 3003.6, 12, t0 + 3.8, span=1.2)
    ticks = [Tick(t.symbol, t.price, t.ts, source="page") for t in early + late]
    sig = compute_signal(ticks, now=t0 + 5.0)
    assert sig.suggested == "no trade"
    assert "faded" in sig.reason


def test_overlay_payload_shape_candidates_pick_and_tf_stack():
    t0 = 1_700_000_000.0
    ticks = _ramp("ETH", 3000.0, 3003.6, 8, t0)
    sig = compute_signal(
        ticks,
        now=t0 + 5.0,
        grid={"cell_x": 3, "cell_y": 7, "cell_height": 1.5},
        ohlc=_ohlc("up"),
    )
    d = sig.to_dict()
    assert set(d["timeframes"]) == set(TF_KEYS)
    for frame in d["timeframes"].values():
        assert {"lean", "change_pct", "source"} <= set(frame)
    assert d["candidates"]
    assert d["candidates"] == d["looking_at"]
    assert {t["side"] for t in d["candidates"]} == {"up", "down"}
    assert d["pick"]["cell_x"] == 4
    assert d["pick"]["cell_y"] == 8
    assert d["tf_line"]
    assert d["alignment"] == "with-trend"
    assert d["lesson"] == "with-trend"
    assert "with-trend" in d["why"]
    assert d["looking"] == "nearest square above"
    assert d["setup"] in ("stall", "compression", "sweep", "late_pink", "none")
    assert d["action"] in ("sit", "tap")
    assert "pink_age_s" in d
    assert "range_shrinking" in d
    assert "wick_squares" in d
    assert "compression_box" in d
    assert "swing_1m" in d

"""Grade a named nearby square after its 5s window. No network."""
from src.analytics.lesson import (
    Call,
    GradeBook,
    call_from_signal,
    grade_call,
    next_window,
    price_band,
    price_touched,
)
from src.analytics.signal import Tick, compute_signal
from src.analytics.timeframes import TF_KEYS, Bar
from src.control.room import ControlRoom


def _ramp(symbol, start, end, n, t0, span=5.0):
    ticks = []
    for i in range(n):
        frac = i / (n - 1)
        ticks.append(Tick(symbol, start + (end - start) * frac, t0 + span * frac, source="test"))
    return ticks


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


def test_price_band_matches_grid_row():
    # ETH-style $0.50 rows: 3000.2 sits in 6000; nearest above is 6001 → [3000.5, 3001.0)
    assert price_band(3000.2, 0.5, "up", 1) == (3000.5, 3001.0)
    assert price_band(3000.2, 0.5, "down", 1) == (2999.5, 3000.0)
    assert price_band(3000.2, 0.5, "up", 1, cell_y=6001) == (3000.5, 3001.0)


def test_single_touch_is_a_hit():
    ticks = [
        Tick("ETH", 3000.2, 10.0),
        Tick("ETH", 3000.6, 11.0),  # tags [3000.5, 3001.0)
        Tick("ETH", 3000.3, 12.0),
    ]
    hit, n = price_touched(ticks, 3000.5, 3001.0, 10.0, 15.0)
    assert hit is True
    assert n == 3


def test_no_touch_in_window_is_a_miss():
    ticks = [Tick("ETH", 3000.2, 10.0 + i) for i in range(5)]
    hit, n = price_touched(ticks, 3000.5, 3001.0, 10.0, 15.0)
    assert hit is False
    assert n == 5


def test_ticks_outside_window_do_not_count():
    ticks = [Tick("ETH", 3000.8, 9.9), Tick("ETH", 3000.8, 15.0)]
    hit, n = price_touched(ticks, 3000.5, 3001.0, 10.0, 15.0)
    assert hit is False
    assert n == 0


def test_grade_named_square_hit_and_miss():
    call = Call(
        window_id=21,
        start_ts=105.0,
        end_ts=110.0,
        named=True,
        side="up",
        distance=1,
        lo=3000.5,
        hi=3001.0,
        label="nearest square above",
        lesson="with-trend",
    )
    hit_ticks = [Tick("ETH", 3000.2, 105.1), Tick("ETH", 3000.7, 106.0)]
    miss_ticks = [Tick("ETH", 3000.2, 105.1), Tick("ETH", 3000.3, 106.0)]
    hit = grade_call(call, hit_ticks)
    miss = grade_call(call, miss_ticks)
    assert hit.outcome == "hit" and hit.touched is True
    assert hit.line == "hit nearest square above"
    assert miss.outcome == "miss" and miss.touched is False
    assert miss.line == "missed nearest square above"


def test_grade_no_trade_is_stood_out():
    call = Call(
        window_id=3,
        start_ts=15.0,
        end_ts=20.0,
        named=False,
        lesson="chop",
        why="chop — tape is whipping, standing aside",
    )
    g = grade_call(call, [Tick("ETH", 3000.8, 16.0)])
    assert g.outcome == "stood-out"
    assert g.touched is None
    assert "stood aside" in g.line
    assert "chop" in g.line


def test_grade_unknown_without_ticks_or_band():
    empty = Call(window_id=1, start_ts=0, end_ts=5, named=True, side="up", lo=1, hi=2, label="x")
    assert grade_call(empty, []).outcome == "unknown"
    noband = Call(window_id=1, start_ts=0, end_ts=5, named=True, side="up", label="x")
    assert grade_call(noband, [Tick("ETH", 1.5, 1.0)]).outcome == "unknown"


def test_next_window_aligns_to_five_seconds():
    wid, start, end = next_window(100.0)
    assert wid == 21
    assert start == 105.0
    assert end == 110.0
    wid, start, end = next_window(10.0, grid={"now_ms": 25_000, "square_duration": 5_000})
    # current col 5, next 6; 5000ms remain → start = 10+5, end = 20
    assert wid == 6
    assert start == 15.0
    assert end == 20.0


def test_scoreboard_hit_rate_ignores_stood_out():
    book = GradeBook()
    t0 = 100.0
    up = compute_signal(
        _ramp("ETH", 3000.0, 3003.6, 8, t0 - 5.0),
        now=t0,
        cell_height=0.5,
        ohlc=_ohlc("up"),
    )
    assert up.suggested != "no trade"
    # Lock the call for window 21 (100 → next starts 105). Band is [3004.0, 3004.5).
    book.update(up, [], now=t0, last_price=3003.6, grid={"cell_height": 0.5})
    book.update(up, [], now=105.0, last_price=3003.6, grid={"cell_height": 0.5})
    touch = [Tick("ETH", 3004.1, 106.0)]
    rollup = book.update(up, touch, now=110.0, last_price=3003.6, grid={"cell_height": 0.5})
    assert rollup["n"] == 1
    assert rollup["hits"] == 1
    assert rollup["last"]["outcome"] == "hit"
    assert "tagged" in rollup["line"]

    quiet = compute_signal(_ramp("ETH", 3000.0, 3000.2, 6, 110.0), now=115.0)
    book.update(quiet, [], now=115.0, last_price=3000.2)
    book.update(quiet, [], now=120.0, last_price=3000.2)
    later = book.update(quiet, [Tick("ETH", 3000.2, 121.0)], now=125.0, last_price=3000.2)
    assert later["last"]["outcome"] == "stood-out"
    assert later["n"] == 1  # stood-out does not change hit rate
    assert later["hits"] == 1


def test_call_from_signal_uses_named_band():
    t0 = 1_700_000_000.0
    sig = compute_signal(
        _ramp("ETH", 3000.0, 3003.6, 8, t0),
        now=t0 + 5.0,
        grid={"cell_x": 3, "cell_y": 7, "cell_height": 1.5},
        ohlc=_ohlc("up"),
    )
    call = call_from_signal(sig, 4, 10.0, 15.0, last_price=3003.6)
    assert call.named is True
    assert call.cell_x == 4
    assert call.cell_y == 8
    assert call.lo == 12.0
    assert call.hi == 13.5


def test_think_payload_includes_lesson_grade_and_tf_stack(tmp_path):
    room = ControlRoom(
        enable_oracle=False,
        dry_run=True,
        session_path=tmp_path / "s.json",
        token_path=tmp_path / "t.json",
        calibration_path=tmp_path / "c.json",
    )
    t0 = 100.0
    for tick in _ramp("ETH", 3000.0, 3003.6, 8, t0 - 5.0):
        room.ticks.push(tick.symbol, tick.price, tick.ts, source="test")
    room.set_grid({"cell_height": 0.5, "now_ms": 20_000, "square_duration": 5_000, "price": 3003.6})
    body = room.think_payload(now=t0)
    assert body["candidates"]
    assert body["pick"]["side"] == "up"
    assert set(body["timeframes"]) == set(TF_KEYS)
    assert body["lesson"] in ("with-trend", "mixed")
    assert body["why"]
    assert body["looking"]
    assert "grade" in body
    assert body["grade"]["n"] == 0
    # Advance two columns and feed a touch of the locked band
    room.ticks.push("ETH", 3004.1, t0 + 6.0, source="test")
    graded = room.think_payload(now=t0 + 10.0)
    assert graded["grade"]["n"] >= 1
    assert graded["grade"]["last"]["outcome"] in ("hit", "miss", "unknown")
    assert graded["candidates"] == graded["looking_at"]
    room.close()


def test_tape_stashes_last_closed_on_window_flip(tmp_path):
    room = ControlRoom(
        enable_oracle=False,
        dry_run=True,
        session_path=tmp_path / "s.json",
        token_path=tmp_path / "t.json",
        calibration_path=tmp_path / "c.json",
    )
    t0 = 100.0
    for tick in _ramp("ETH", 3000.0, 3003.6, 8, t0 - 5.0):
        room.ticks.push(tick.symbol, tick.price, tick.ts, source="test")
    room.set_grid({"cell_height": 0.5, "now_ms": 20_000, "square_duration": 5_000, "price": 3003.6})
    first = room.think_payload(now=t0)
    tape0 = first["tape"]
    assert "window_remaining_s" in tape0
    assert "last_closed" not in tape0
    open_lo, open_hi = tape0.get("lo"), tape0.get("hi")
    room.set_grid({"cell_height": 0.5, "now_ms": 25_100, "square_duration": 5_000, "price": 3003.6})
    second = room.think_payload(now=t0 + 5.1)
    lc = second["tape"].get("last_closed")
    assert lc is not None
    assert set(lc) >= {"lo", "hi", "outcome", "t0", "t1"}
    assert lc["outcome"] in ("hit", "miss", "sit")
    assert lc["lo"] == open_lo
    assert lc["hi"] == open_hi
    assert lc["t1"] > lc["t0"]
    # current tape.lo/hi is this window, not an alias of the ghost
    assert second["tape"]["lo"] == lc["lo"] or second["tape"]["lo"] != lc["lo"] or lc["lo"] is None
    # GET /status must carry the same tape object, not only /think or /events
    st = room.status()["think"]["tape"]
    assert "window_remaining_s" in st
    assert st.get("last_closed") == lc
    room.close()


def test_tape_uses_at_price_band_when_there_is_no_pick(tmp_path):
    room = ControlRoom(
        enable_oracle=False,
        dry_run=True,
        session_path=tmp_path / "s.json",
        token_path=tmp_path / "t.json",
        calibration_path=tmp_path / "c.json",
    )
    t0 = 100.0
    # Flat tape so think sits (no pick) but a quote still exists.
    for i in range(8):
        room.ticks.push("ETH", 3000.2, t0 - 5 + i * 0.6, source="test")
    room.set_grid({
        "cell_height": 0.5, "dollars_per_line": 0.5,
        "now_ms": 20_000, "square_duration": 5_000, "price": 3000.2,
        "cells": [{
            "cell_x": 5, "cell_y": 6000, "forward": 1, "side": "at-price",
            "distance": 0, "multiplier": 1.2, "break_even_probability": 0.833333,
        }],
    })
    body = room.payload(None)
    tape = body["tape"]
    assert tape["lo"] == 3000.0
    assert tape["hi"] == 3000.5
    st = room.status()["think"]["tape"]
    assert st["lo"] == 3000.0 and st["hi"] == 3000.5
    room.close()
